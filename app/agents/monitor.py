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
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.agents.protocols import MonitorAgent
from app.config.settings import settings
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

# ----------------------------------------------------------------------
# M1 切面合格性监控（规格：流程监控智能体增加切面合格性监控）
# ----------------------------------------------------------------------
TAG_PLANE_NON_STANDARD_STREAK = "plane_non_standard_streak"
TAG_PLANE_KEY_STRUCTURE_MISSING = "plane_key_structure_missing"
TAG_IMAGE_QUALITY_POOR_STREAK = "image_quality_poor_streak"

# 各器官"标准切面应可见的关键结构"。真机口径应由现场标定/知识库覆盖，
# 此处只作为未标定时的保守默认。
EXPECTED_STRUCTURES_BY_ORGAN: dict[str, tuple] = {
    "thyroid": ("left_lobe", "right_lobe", "isthmus"),
    "liver": ("right_lobe", "left_lobe", "portal_vein"),
    "cardiac": ("left_ventricle", "left_atrium", "aortic_valve"),
}

# 图像质量"不达标"阈值（与前端展示阈值一致：< 0.5 为严重提示）
IMAGE_QUALITY_POOR_THRESHOLD = 0.5


@dataclass
class NavigationQualityState:
    """单会话的导航质量滑动状态。"""

    non_standard_streak: int = 0
    poor_quality_streak: int = 0
    last_plane_type: str | None = None
    # 上一次报告缺失的结构集合（用于边沿触发，避免逐帧刷日志）
    missing_structures: list[str] = field(default_factory=list)


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
        self._failure_windows: dict[Organ, deque[bool]] = defaultdict(
            lambda: deque(maxlen=self._window)
        )
        # M1/M6：会话级导航质量状态（纯内存 + 同步操作，可在导航热路径直接调用）
        self._nav_states: dict[str, NavigationQualityState] = {}
        self._plane_streak_threshold = settings.nav_plane_non_standard_streak_threshold
        self._quality_streak_threshold = settings.nav_image_quality_poor_streak_threshold

    # ------------------------------------------------------------------
    # 内部：metrics 埋点（容错）
    # ------------------------------------------------------------------

    def _record_latency(self, seconds: float, with_retry: bool = False) -> None:
        try:
            from app.observability.metrics_collector import get_metrics_collector
            collector = (self._metrics_factory or get_metrics_collector)()
            collector.record_diagnosis_latency(seconds, with_retry=with_retry)
        except Exception as exc:
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
        except Exception as exc:
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

    async def detect_anomaly(self, request: DiagnosisRequest) -> str | None:
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

    # ------------------------------------------------------------------
    # M1/M6：导航切面合格性与图像质量监控
    # ------------------------------------------------------------------
    def observe_navigation_frame(
        self,
        *,
        session_id: str,
        plane_assessment: Any = None,
        image_quality: Any = None,
        frame_id: str | None = None,
    ) -> list[str]:
        """观察一帧导航的切面/质量，返回本次**新触发**的告警标签。

        设计要点：

        1. **同步且廉价**：只有内存字典操作，可在导航热路径（15fps）直接调用。
        2. **边沿触发**：连续非标准帧与质量不达标在"恰好达到阈值"那一次告警，
           而不是此后每帧都报——否则 15fps 会把日志刷爆且掩盖真正的新事件。
        3. **无数据不臆断**：``required_structures_visible`` 为空表示"适配器未
           提供结构信息"，此时**不**产生关键结构缺失告警（否则未接真机时会
           全量误报）。仅当适配器确实报告了可见结构时，才与期望清单比对。

        Args:
            session_id: 会话标识（状态按会话隔离）。
            plane_assessment: ``app.models.schemas.PlaneAssessment`` 或兼容对象。
            image_quality: ``app.models.schemas.ImageQuality`` 或兼容对象。
            frame_id: 仅用于日志定位。

        Returns:
            本次新触发的告警标签列表（可能为空）。
        """
        if not session_id:
            return []

        state = self._nav_states.setdefault(session_id, NavigationQualityState())
        tags: list[str] = []

        if plane_assessment is not None:
            tags.extend(
                self._observe_plane(
                    state, session_id=session_id, frame_id=frame_id,
                    plane_assessment=plane_assessment,
                )
            )

        if image_quality is not None:
            tags.extend(
                self._observe_quality(
                    state, session_id=session_id, frame_id=frame_id,
                    image_quality=image_quality,
                )
            )

        return tags

    def _observe_plane(
        self,
        state: NavigationQualityState,
        *,
        session_id: str,
        frame_id: str | None,
        plane_assessment: Any,
    ) -> list[str]:
        tags: list[str] = []
        is_standard = bool(getattr(plane_assessment, "is_standard", True))
        state.last_plane_type = getattr(plane_assessment, "plane_type", None)

        if is_standard:
            state.non_standard_streak = 0
        else:
            state.non_standard_streak += 1
            if state.non_standard_streak == self._plane_streak_threshold:
                tags.append(TAG_PLANE_NON_STANDARD_STREAK)
                logger.warning(
                    "monitor_plane_non_standard_streak",
                    session_id=session_id,
                    frame_id=frame_id,
                    streak=state.non_standard_streak,
                    threshold=self._plane_streak_threshold,
                    plane_type=state.last_plane_type,
                    issues=list(getattr(plane_assessment, "issues", []) or []),
                )

        visible = list(getattr(plane_assessment, "required_structures_visible", None) or [])
        if not visible:
            # 适配器未报告结构信息：不做臆断，也不清除上一次的缺失记录
            return tags

        organ = getattr(plane_assessment, "organ_code", None)
        expected = EXPECTED_STRUCTURES_BY_ORGAN.get((organ or "").strip().lower(), ())
        missing = [name for name in expected if name not in visible]

        if missing != state.missing_structures:
            state.missing_structures = missing
            if missing:
                tags.append(TAG_PLANE_KEY_STRUCTURE_MISSING)
                logger.warning(
                    "monitor_plane_key_structure_missing",
                    session_id=session_id,
                    frame_id=frame_id,
                    organ=organ,
                    missing_structures=missing,
                    visible_structures=visible,
                )
        return tags

    def _observe_quality(
        self,
        state: NavigationQualityState,
        *,
        session_id: str,
        frame_id: str | None,
        image_quality: Any,
    ) -> list[str]:
        tags: list[str] = []
        overall = getattr(image_quality, "overall_score", None)
        if overall is None:
            return tags

        if float(overall) >= IMAGE_QUALITY_POOR_THRESHOLD:
            state.poor_quality_streak = 0
            return tags

        state.poor_quality_streak += 1
        if state.poor_quality_streak == self._quality_streak_threshold:
            tags.append(TAG_IMAGE_QUALITY_POOR_STREAK)
            logger.warning(
                "monitor_image_quality_poor_streak",
                session_id=session_id,
                frame_id=frame_id,
                streak=state.poor_quality_streak,
                threshold=self._quality_streak_threshold,
                overall_score=overall,
                issues=list(getattr(image_quality, "issues", []) or []),
            )
        return tags

    def get_navigation_state(self, session_id: str) -> NavigationQualityState | None:
        """返回指定会话的导航质量状态（测试/运维用）。"""
        return self._nav_states.get(session_id)

    def reset_navigation_state(self, session_id: str | None = None) -> None:
        """清空导航质量状态（传 None 清空全部）。"""
        if session_id is None:
            self._nav_states.clear()
            return
        self._nav_states.pop(session_id, None)

    async def health_check(self) -> bool:
        """MonitorAgent 自身健康检查。

        MonitorAgent 没有外部依赖（仅做内存态窗口维护 + 指标埋点），
        故健康检查直接返回 ``True``。保留该方法便于上层在 lifespan
        启动期统一调用 ``health_check()`` 验证所有智能体的可用性。
        """
        return True

    async def close(self) -> None:
        """清空所有滑动窗口与导航质量状态（释放内存态状态）。"""
        try:
            self.reset()
            self.reset_navigation_state()
        except Exception as exc:
            logger.warning(
                "monitor_close_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        logger.info("monitor_closed")

    # ------------------------------------------------------------------
    # 测试 / 运维辅助
    # ------------------------------------------------------------------

    def get_failure_window(self, organ: Organ) -> deque[bool]:
        """返回指定 organ 的最近 N 次结果状态（True=成功，False=失败）。"""
        return self._failure_windows[organ]

    def reset(self) -> None:
        """清空所有滑动窗口（用于测试和运维）。"""
        self._failure_windows.clear()


__all__ = [
    "CONFIDENCE_REVIEW_THRESHOLD",
    "CONSECUTIVE_FAILURE_WINDOW",
    "EXPECTED_STRUCTURES_BY_ORGAN",
    "IMAGE_QUALITY_POOR_THRESHOLD",
    "LATENCY_DEGRADED_THRESHOLD_MS",
    "TAG_IMAGE_QUALITY_POOR_STREAK",
    "TAG_NEEDS_REVIEW",
    "TAG_PERFORMANCE_DEGRADED",
    "TAG_PLANE_KEY_STRUCTURE_MISSING",
    "TAG_PLANE_NON_STANDARD_STREAK",
    "TAG_SYSTEM_ANOMALY",
    "MonitorAgentImpl",
    "NavigationQualityState",
]
