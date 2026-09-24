"""T-GA-EDG-02 边缘端导航推理服务（模型加载 + 15fps + 温度分级）。

职责：把「帧 → 推理 → 本地安全校验 → 上行」串起来，并按 GPU 温度
动态调整推理节流（T-GB-EDG-04 的落地位置）：

    正常 15fps → 主动降频 10fps → 隔帧推理 7.5fps → 暂停导航 → 紧急降级全停

模型后端通过 :class:`edge.protocols.NavInferenceBackend` 注入：
生产用 TensorRT（Jetson），开发机用假实现跑单测，互不影响。
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

import structlog

from edge.gpu_thermal import GPUThermalPolicy, ThermalDecision, ThermalLevel
from edge.image_quality import (
    ImageQualityAdapter,
    NullImageQualityAdapter,
)
from edge.plane_assessment import (
    NullPlaneAssessmentAdapter,
    PlaneAssessmentAdapter,
)
from edge.protocols import (
    Frame,
    ImageQuality,
    NavInferenceBackend,
    NavLabel,
    NavResult,
    PlaneAssessment,
)
from edge.safety import LocalSafetyChecker

logger = structlog.get_logger(__name__)

# 延迟采样窗口（P99 用）
DEFAULT_LATENCY_WINDOW = 512


class NavigationInferenceService:
    """导航推理调度器（同步推理 + 显式节流，便于在边缘线程/协程中复用）。

    M1/M6 的切面评估与图像质量评估通过适配器注入（未部署时为空实现，
    ``assess`` 返回 ``None``，不产出字段）；M2 的高危标记（B+ 级）由
    :class:`edge.safety.LocalSafetyChecker` 在本地安全校验后统一打标。
    """

    def __init__(
        self,
        backend: NavInferenceBackend,
        *,
        thermal_policy: Optional[GPUThermalPolicy] = None,
        safety_checker: Optional[LocalSafetyChecker] = None,
        target_fps: float = 15.0,
        latency_window: int = DEFAULT_LATENCY_WINDOW,
        plane_assessment_adapter: Optional[PlaneAssessmentAdapter] = None,
        image_quality_adapter: Optional[ImageQualityAdapter] = None,
        organ_code: Optional[str] = None,
        enable_risk_marking: bool = True,
    ) -> None:
        if target_fps <= 0:
            raise ValueError("target_fps 必须为正数")
        self._backend = backend
        self._thermal = thermal_policy or GPUThermalPolicy()
        self._safety = safety_checker or LocalSafetyChecker()
        self._base_target_fps = target_fps
        self._latencies_ms: Deque[float] = deque(maxlen=latency_window)
        self._plane_adapter = plane_assessment_adapter or NullPlaneAssessmentAdapter()
        self._quality_adapter = image_quality_adapter or NullImageQualityAdapter()
        self._organ_code = organ_code
        self._enable_risk_marking = enable_risk_marking

        self._loaded = False
        self._frames_in = 0
        self._frames_inferred = 0
        self._frames_skipped = 0
        self._risk_marked = 0
        self._last_infer_at: Optional[float] = None
        self._decision: ThermalDecision = self._thermal.decide(0.0)

    # ---- 生命周期 ----
    def load(self) -> None:
        if not self._loaded:
            self._backend.load()
            self._plane_adapter.load()
            self._quality_adapter.load()
            self._loaded = True
            logger.info(
                "edge_nav_backend_loaded",
                backend=self._backend.name,
                plane_assessment_backend=self._plane_adapter.name,
                image_quality_backend=self._quality_adapter.name,
            )

    def close(self) -> None:
        if self._loaded:
            self._backend.close()
            self._plane_adapter.close()
            self._quality_adapter.close()
            self._loaded = False

    # ---- 温度 ----
    def record_temperature(self, celsius: float, now: Optional[float] = None) -> ThermalDecision:
        """上报 GPU 温度并刷新调度决策（档位变化时打日志便于运维追踪）。"""
        previous = self._decision.level
        self._decision = self._thermal.decide(celsius, now=now)
        if self._decision.level != previous:
            logger.warning(
                "edge_nav_thermal_level_changed",
                celsius=celsius,
                previous=previous.value,
                current=self._decision.level.value,
                target_fps=self._decision.target_fps,
                reason=self._decision.reason,
            )
        return self._decision

    @property
    def decision(self) -> ThermalDecision:
        return self._decision

    @property
    def target_fps(self) -> float:
        """当前允许的推理帧率（取温度档位与配置上限的较小值）。"""
        if not self._decision.nav_inference_allowed:
            return 0.0
        thermal_fps = self._decision.target_fps
        if thermal_fps <= 0:
            return 0.0
        return min(self._base_target_fps, thermal_fps)

    # ---- 调度 ----
    def should_infer(self, frame_index: int, now: Optional[float] = None) -> bool:
        """是否对当前帧执行推理（温度档位 + 隔帧 + 帧率节流）。"""
        if not self._decision.nav_inference_allowed:
            return False

        if self._decision.frame_skip > 0:
            # 隔帧推理：每 (frame_skip + 1) 帧推理 1 帧
            if frame_index % (self._decision.frame_skip + 1) != 0:
                return False

        fps = self.target_fps
        if fps <= 0:
            return False

        now = time.monotonic() if now is None else now
        min_interval = 1.0 / fps
        if self._last_infer_at is not None and (now - self._last_infer_at) < min_interval:
            return False
        return True

    # ---- 主流程 ----
    def process_frame(self, frame: Frame, now: Optional[float] = None) -> Optional[NavResult]:
        """处理一帧；返回 None 表示本帧按策略被跳过。"""
        self._frames_in += 1
        now = time.monotonic() if now is None else now

        if not self.should_infer(frame.index, now=now):
            self._frames_skipped += 1
            return None

        self.load()

        start = time.monotonic()
        raw_labels: List[NavLabel] = self._backend.infer(frame)
        latency_ms = (time.monotonic() - start) * 1000.0

        self._last_infer_at = now
        self._frames_inferred += 1
        self._latencies_ms.append(latency_ms)

        # T-GB-INT-02：边缘端推理延迟埋点（指标此前只有定义与读取方）。
        # 边缘端可能以独立进程运行、未必装配 metrics collector，故做尽力而为。
        try:
            from app.observability.metrics_collector import get_metrics_collector

            get_metrics_collector().record_edge_nav_latency(latency_ms / 1000.0)
        except Exception as exc:  # noqa: BLE001 - 埋点失败不得影响导航
            logger.debug("edge_nav_latency_metric_failed", error=str(exc))

        accepted, violations, _rejected = self._safety.filter_labels(
            raw_labels, organ=self._organ_code
        )
        if violations:
            logger.warning(
                "edge_nav_local_safety_violations",
                frame_id=frame.frame_id,
                violations=violations[:10],
            )

        # M2：B+ 级高危标记（本地安全校验之后统一打标）
        if self._enable_risk_marking:
            marked = self._safety.mark_risk_batch(accepted, organ=self._organ_code)
            self._risk_marked += sum(1 for label in marked if label.risk_flag)
            accepted = marked

        return NavResult(
            frame_id=frame.frame_id,
            session_id=frame.session_id,
            labels=accepted,
            infer_latency_ms=round(latency_ms, 3),
            backend=self._backend.name,
            plane_assessment=self._assess_plane(frame),
            image_quality=self._assess_quality(frame),
        )

    # ---- M1/M6 评估（适配器失败不得影响导航主链路）----
    def _assess_plane(self, frame: Frame) -> Optional[PlaneAssessment]:
        try:
            return self._plane_adapter.assess(frame, organ_code=self._organ_code)
        except Exception as exc:  # noqa: BLE001 - 评估失败降级为不产出
            logger.warning(
                "edge_plane_assessment_failed",
                frame_id=frame.frame_id,
                backend=self._plane_adapter.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def _assess_quality(self, frame: Frame) -> Optional[ImageQuality]:
        try:
            return self._quality_adapter.assess(frame)
        except Exception as exc:  # noqa: BLE001 - 评估失败降级为不产出
            logger.warning(
                "edge_image_quality_assessment_failed",
                frame_id=frame.frame_id,
                backend=self._quality_adapter.name,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    # ---- 观测 ----
    def stats(self) -> Dict[str, object]:
        return {
            "frames_in": self._frames_in,
            "frames_inferred": self._frames_inferred,
            "frames_skipped": self._frames_skipped,
            "p50_latency_ms": self._percentile(50.0),
            "p95_latency_ms": self._percentile(95.0),
            "p99_latency_ms": self._percentile(99.0),
            "target_fps": self.target_fps,
            "thermal_level": self._decision.level.value,
            "nav_inference_allowed": self._decision.nav_inference_allowed,
            "backend": self._backend.name,
            "risk_marked_labels": self._risk_marked,
            "plane_assessment_backend": self._plane_adapter.name,
            "image_quality_backend": self._quality_adapter.name,
        }

    def _percentile(self, pct: float) -> Optional[float]:
        if not self._latencies_ms:
            return None
        ordered = sorted(self._latencies_ms)
        n = len(ordered)
        index = (pct / 100.0) * (n - 1)
        lower = int(index)
        upper = min(lower + 1, n - 1)
        if lower == upper:
            value = ordered[lower]
        else:
            value = ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)
        return round(value, 3)


__all__ = ["DEFAULT_LATENCY_WINDOW", "NavigationInferenceService"]
