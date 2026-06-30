"""T-GA-AGT-07 — 流程监控智能体实现。

``MonitorAgentImpl`` 提供三项能力：

1. ``track_diagnosis()``：记录每次诊断的 latency / confidence，并在以下情况打标签：
   - R1: ``confidence_score < 0.3`` → ``needs_review``
   - R2: ``processing_time_ms > 5000`` → ``performance_degraded``
   - R3: 同一 organ 连续 10 次失败 → ``system_anomaly``
2. ``detect_anomaly()``：对入参做最简预检（image_ids 为空、target_organ 缺失等）。
3. 复用 ``app.observability.metrics_collector.get_metrics_collector`` 埋点；
   任何埋点失败都吞掉，监控组件本身绝不影响主流程。

器官级滑动窗口：用 ``defaultdict(deque)`` 维护每个 organ 最近 N 次结果状态
（成功 / 失败），命中 R3 阈值时打 ``system_anomaly`` 标签并降级
``set_service_health_rate``。
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict, Optional

import structlog

from app.agents.protocols import MonitorAgent
from app.models.schemas import DiagnosisRequest, DiagnosisResult, Organ

logger = structlog.get_logger("app.agents.monitor")


# 异常检测规则常量
CONFIDENCE_REVIEW_THRESHOLD = 0.3       # R1
LATENCY_DEGRADED_THRESHOLD_MS = 5000    # R2
CONSECUTIVE_FAILURE_WINDOW = 10         # R3

# 异常标签
TAG_NEEDS_REVIEW = "needs_review"
TAG_PERFORMANCE_DEGRADED = "performance_degraded"
TAG_SYSTEM_ANOMALY = "system_anomaly"


class MonitorAgentImpl(MonitorAgent):
    """流程监控智能体。

    Args:
        consecutive_failure_window: 同一 organ 连续失败多少次后触发
            ``system_anomaly``。默认 10。
        metrics_collector_factory: 可选 ``callable``，返回 ``MetricsCollector``。
            传入 ``None`` 时使用 ``get_metrics_collector``。
    """

    def __init__(
        self,
        consecutive_failure_window: int = CONSECUTIVE_FAILURE_WINDOW,
        metrics_collector_factory=None,
    ) -> None:
        if consecutive_failure_window <= 0:
            raise ValueError("consecutive_failure_window must be positive")
        self._window = consecutive_failure_window
        self._metrics_factory = metrics_collector_factory
        # 滑动窗口：organ -> 最近 N 次结果（True=成功，False=失败）
        self._failure_windows: Dict[Organ, Deque[bool]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )

    # ------------------------------------------------------------------
    # 内部：metrics 埋点（容错）
    # ------------------------------------------------------------------

    def _record_latency(self, seconds: float, with_retry: bool = False) -> None:
        try:
            from app.observability.metrics_collector import get_metrics_collector
            collector = (self._metrics_factory or get_metrics_collector)()
            collector.record_diagnosis_latency(seconds, with_retry=with_retry)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "monitor_metrics_latency_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _record_health(self, rate: float) -> None:
        try:
            from app.observability.metrics_collector import get_metrics_collector
            collector = (self._metrics_factory or get_metrics_collector)()
            collector.set_service_health_rate(rate)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "monitor_metrics_health_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # 内部：辅助函数
    # ------------------------------------------------------------------

    @staticmethod
    def _is_failure(response: DiagnosisResult) -> bool:
        """判定一次诊断结果是否为「失败」。

        规则：status 字段为 ``"failed"`` / ``"timeout"`` 或 ``ai_diagnosis_failed=True``。
        """
        status = (response.status or "").lower()
        if status in ("failed", "timeout"):
            return True
        if response.ai_diagnosis_failed:
            return True
        return False

    @staticmethod
    def _confidence_score(response: DiagnosisResult) -> float:
        """提取置信度分数。

        当 ``quality_control`` 字段缺失时，视为 ``0.0``（即最差置信度），
        以便触发 R1（``needs_review``）规则。这与设计文档中
        「R1: confidence_score < 0.3 → needs_review」的语义一致 —
        一个没有任何质量评分的诊断应当被人工复核。
        """
        if response.quality_control is None:
            return 0.0
        score = response.quality_control.confidence_score
        # 如果 score 也是 None，按 0.0 处理
        return 0.0 if score is None else score

    # ------------------------------------------------------------------
    # MonitorAgent protocol
    # ------------------------------------------------------------------

    async def track_diagnosis(
        self,
        request: DiagnosisRequest,
        response: DiagnosisResult,
    ) -> None:
        """追踪一次完整诊断并按规则打标签。

        行为契约（与 01-架构 §2 + debug-pro-1.0.0 审计要求一致）：

        1) **延迟埋点**（与 status 无关）：所有诊断均记录 latency 指标；
        2) **R3 滑动窗口**（与 status 无关）：所有诊断均更新 organ 级别的
           连续失败滑动窗口，R3 在窗口满且全部为失败时触发；
        3) **R1/R2 仅在终态（``status == "completed"``）判定**：在
           ``processing`` 状态下的诊断不触发 R1（低置信度） / R2（性能降级）
           规则。

        设计理由（处理 P3 审查项）：

        - R1 语义上是"诊断结果置信度不足，需人工复核"——"未完成的诊断"在
          概念上**没有**置信度结果，不应被误判为 needs_review；
        - R2 语义上是"处理时间过长，性能下降"——``processing`` 状态的请求
          还没结束，``processing_time_ms`` 是中间值，不能据此判定性能降级；
        - 运维可观测性需求通过**步骤 1（latency 埋点）+ 步骤 2（滑动窗口
          更新）**覆盖：``processing`` 状态的延迟会被记录到指标库，超时
          之后其转 ``"timeout"`` 终态的诊断会被 R2 / R3 正确捕获。

        因此 ``if response.status != "completed": return`` 后的早返回**不是**
        遗漏了 "processing 状态下的超时前最后状态的性能降级记录"，而是
        故意为之（避免假阳性）。这条契约与设计文档保持一致，无需调整。
        """
        # 1) 延迟埋点（秒）— 与 status 无关
        seconds = response.processing_time_ms / 1000.0
        self._record_latency(seconds, with_retry=bool(request.retry))

        # 2) 更新 organ 滑动窗口（无论终态与否都更新，R3 依赖此窗口）
        is_failure = self._is_failure(response)
        self._failure_windows[request.target_organ].append(not is_failure)

        # 3) R3 — 同一 organ 连续失败（独立于 status，非 completed 也触发）
        window = self._failure_windows[request.target_organ]
        if (
            len(window) == self._window
            and not any(window)  # 全部 False（即全部失败）
        ):
            logger.error(
                "monitor_anomaly",
                request_id=response.request_id,
                organ=request.target_organ.value,
                rule="R3",
                tag=TAG_SYSTEM_ANOMALY,
                consecutive_failures=self._window,
            )
            # 系统级异常：下调整体健康度
            self._record_health(0.5)

        # 4) R1 / R2 仅对终态（completed）结果判定
        # 行为契约：``processing`` 状态不触发 R1/R2（见方法 docstring）。
        if response.status != "completed":
            return

        # 5) R1 — 低置信度
        confidence = self._confidence_score(response)
        if confidence < CONFIDENCE_REVIEW_THRESHOLD:
            logger.warning(
                "monitor_anomaly",
                request_id=response.request_id,
                organ=request.target_organ.value,
                rule="R1",
                tag=TAG_NEEDS_REVIEW,
                confidence=confidence,
            )

        # 6) R2 — 性能降级
        if response.processing_time_ms > LATENCY_DEGRADED_THRESHOLD_MS:
            logger.warning(
                "monitor_anomaly",
                request_id=response.request_id,
                organ=request.target_organ.value,
                rule="R2",
                tag=TAG_PERFORMANCE_DEGRADED,
                processing_time_ms=response.processing_time_ms,
            )

    async def detect_anomaly(self, request: DiagnosisRequest) -> Optional[str]:
        """对入参做最简预检。

        Phase 0A 只检查：
        - ``image_ids`` 为空 → ``"missing_image_ids"``
        - ``target_organ`` 缺失 → ``"missing_target_organ"``

        后续 Phase 0B+ 可加入「请求频率 / 已知坏器官 / 数据集黑名单」等规则。
        """
        if not request.image_ids:
            return "missing_image_ids"
        if request.target_organ is None:
            return "missing_target_organ"
        return None

    async def health_check(self) -> bool:
        """MonitorAgent 自身健康检查。

        MonitorAgent 没有外部依赖（仅做内存态窗口维护 + 指标埋点），
        故健康检查直接返回 ``True``。保留该方法便于上层在 lifespan
        启动期统一调用 ``health_check()`` 验证所有智能体的可用性。
        """
        return True

    async def close(self) -> None:
        """清空所有滑动窗口（释放内存态状态）。"""
        try:
            self.reset()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "monitor_close_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        logger.info("monitor_closed")

    # ------------------------------------------------------------------
    # 测试 / 运维辅助
    # ------------------------------------------------------------------

    def get_failure_window(self, organ: Organ) -> Deque[bool]:
        """返回指定 organ 的最近 N 次结果状态（True=成功，False=失败）。"""
        return self._failure_windows[organ]

    def reset(self) -> None:
        """清空所有滑动窗口（用于测试和运维）。"""
        self._failure_windows.clear()


__all__ = [
    "MonitorAgentImpl",
    "CONFIDENCE_REVIEW_THRESHOLD",
    "LATENCY_DEGRADED_THRESHOLD_MS",
    "CONSECUTIVE_FAILURE_WINDOW",
    "TAG_NEEDS_REVIEW",
    "TAG_PERFORMANCE_DEGRADED",
    "TAG_SYSTEM_ANOMALY",
]
