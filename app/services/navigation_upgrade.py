"""M2 快速路径：B+ 级高危标记 → 自动升级为深度诊断。

规格（13-医生直接信息需求缺口实施指南 §M2）：
导航标注带 ``risk_flag="high"``（B+ 级）时，**无需医生手动操作**，后端自动
发起标准/全量诊断请求（"导航→标准路径自动升级"）。

安全与稳定性约束（本模块的实现要点）：

1. **同意优先**：必须复用既有同意校验。撤回或未签署的会话不做升级——
   自动升级会产生新的诊断输出，绝不能在同意缺失时"自动"发生。
2. **防抖是必需的，不是优化**：导航流按 15fps 上报，同一病灶会在连续数十帧
   持续携带 ``risk_flag``。逐帧入队会在几秒内打满诊断队列并使其他患者的
   请求饥饿。因此以「同一会话冷却窗口 + 单会话次数上限」做同步预留，
   **预留发生在任何 await 之前**，并发协程之间不会重复入队；后续步骤失败
   时释放预留，不虚耗配额。
3. **绝不抛出到导航链路**：升级是旁路能力，任何失败只记日志并返回决策对象。
4. **可审计**：每次升级（含被拒原因）都写结构化日志，成功升级额外写审计日志。
"""
from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.models.schemas import ConsentStatus, ResponseTier, RiskFlag
from app.repositories import ConsentRepository, DiagnosisRepository, SessionRepository
from app.services.audit_service import AuditService

logger = structlog.get_logger("app.services.navigation_upgrade")

# 升级触发原因（写审计与日志，便于回溯"为什么多了一次诊断"）
UPGRADE_REASON_RISK_FLAG = "b_plus_risk_flag"
# 审计动作名
AUDIT_ACTION = "nav_auto_upgrade_triggered"


@dataclass
class UpgradeDecision:
    """一次自动升级判定结果。"""

    triggered: bool
    reason: str
    request_id: str | None = None
    organ: str | None = None
    risk_labels: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "reason": self.reason,
            "request_id": self.request_id,
            "organ": self.organ,
            "risk_labels": list(self.risk_labels),
        }


def collect_risk_labels(annotations: Sequence[Any]) -> list[str]:
    """从导航标注中收集携带 B+ 高危标记的标签名（去重保序）。

    兼容后端枚举（``RiskFlag.HIGH``）与边缘端字符串（``"high"``）两种形态，
    并忽略任何非 "high" 的取值——避免未升级或异常边缘端把任意字符串当标记。
    """
    seen: list[str] = []
    for annotation in annotations or ():
        flag = getattr(annotation, "risk_flag", None)
        value = getattr(flag, "value", flag)
        if value != RiskFlag.HIGH.value:
            continue
        name = getattr(annotation, "label", None)
        if name and name not in seen:
            seen.append(name)
    return seen


def _pick_organ(target_organs: Any, preferred: str | None) -> str | None:
    """从会话的 ``target_organs`` 中选出本次升级对应的器官。"""
    organs: list[str] = []
    if isinstance(target_organs, (list, tuple)):
        organs = [str(item) for item in target_organs if item]
    elif isinstance(target_organs, str) and target_organs:
        organs = [target_organs]
    if not organs:
        return preferred or None
    if preferred and preferred in organs:
        return preferred
    return organs[0]


class NavigationUpgradeService:
    """B+ 级高危标记 → 自动深度诊断的编排服务。"""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        tier: ResponseTier | None = None,
        cooldown_seconds: float | None = None,
        max_per_session: int | None = None,
        enqueue: Callable[[str], Awaitable[bool]] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._enabled = settings.nav_auto_upgrade_enabled if enabled is None else enabled
        self._tier = tier or ResponseTier(settings.nav_auto_upgrade_tier)
        self._cooldown = (
            settings.nav_auto_upgrade_cooldown_seconds
            if cooldown_seconds is None
            else cooldown_seconds
        )
        self._max_per_session = (
            settings.nav_auto_upgrade_max_per_session
            if max_per_session is None
            else max_per_session
        )
        self._enqueue_override = enqueue
        self._clock = clock or time.monotonic
        # 会话级防抖状态（纯内存 + 同步读写，见模块 docstring 第 2 点）
        self._last_upgrade_at: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    # ---- 只读视图（运维 / 测试）----
    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def tier(self) -> ResponseTier:
        return self._tier

    @property
    def cooldown_seconds(self) -> float:
        return self._cooldown

    @property
    def max_per_session(self) -> int:
        return self._max_per_session

    def upgrade_count(self, session_id: str) -> int:
        return self._counts.get(session_id, 0)

    def remaining_budget(self, session_id: str) -> int:
        return max(0, self._max_per_session - self.upgrade_count(session_id))

    def reset(self, session_id: str | None = None) -> None:
        """清空防抖状态（测试与运维用；传 None 清空全部）。"""
        if session_id is None:
            self._last_upgrade_at.clear()
            self._counts.clear()
            return
        self._last_upgrade_at.pop(session_id, None)
        self._counts.pop(session_id, None)

    # ---- 同步预留（禁止在其中 await）----
    def try_reserve(self, session_id: str) -> str | None:
        """尝试占用一次升级配额；成功返回 None，被拒返回原因。

        纯同步、微秒级，可安全地在导航热路径上同步调用——防抖必须在这里
        生效，否则每帧都会派生一个后台任务。
        """
        if not self._enabled:
            return "disabled"
        now = self._clock()
        last = self._last_upgrade_at.get(session_id)
        if last is not None and (now - last) < self._cooldown:
            return "cooldown"
        if self.upgrade_count(session_id) >= self._max_per_session:
            return "session_limit"
        self._last_upgrade_at[session_id] = now
        self._counts[session_id] = self.upgrade_count(session_id) + 1
        return None

    def release(self, session_id: str) -> None:
        """释放预留（后续步骤失败时调用，不虚耗配额）。"""
        self._counts[session_id] = max(0, self.upgrade_count(session_id) - 1)
        self._last_upgrade_at.pop(session_id, None)

    # ---- 主编排 ----
    async def maybe_upgrade(
        self,
        *,
        session_id: str,
        frame_id: str,
        annotations: Sequence[Any],
        organ: str | None = None,
        patient_id: str | None = None,
    ) -> UpgradeDecision:
        """若标注含 B+ 级高危标记则尝试自动升级；否则不做任何事。

        Returns:
            决策对象（不论成功与否都返回，便于调用方记录与测试断言）。
        """
        risk_labels = collect_risk_labels(annotations)
        if not risk_labels:
            return UpgradeDecision(False, "no_risk_flag")

        rejection = self.try_reserve(session_id)
        if rejection:
            logger.info(
                "nav_auto_upgrade_skipped",
                session_id=session_id,
                frame_id=frame_id,
                reason=rejection,
                risk_labels=risk_labels,
                upgrade_count=self.upgrade_count(session_id),
            )
            return UpgradeDecision(False, rejection, organ=organ, risk_labels=risk_labels)

        return await self.perform_upgrade(
            session_id=session_id,
            frame_id=frame_id,
            annotations=annotations,
            organ=organ,
            patient_id=patient_id,
        )

    async def perform_upgrade(
        self,
        *,
        session_id: str,
        frame_id: str,
        annotations: Sequence[Any],
        organ: str | None = None,
        patient_id: str | None = None,
    ) -> UpgradeDecision:
        """执行一次**已预留配额**的升级；失败时自行释放预留、绝不抛出。

        调用方通常是导航热路径派生出的后台任务（见
        ``app.api.websocket.nav._schedule_auto_upgrade``）。
        """
        risk_labels = collect_risk_labels(annotations)
        try:
            decision = await self._perform_upgrade(
                session_id=session_id,
                frame_id=frame_id,
                organ=organ,
                patient_id=patient_id,
                risk_labels=risk_labels,
            )
        except Exception as exc:
            self.release(session_id)
            logger.error(
                "nav_auto_upgrade_failed",
                session_id=session_id,
                frame_id=frame_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return UpgradeDecision(
                False, f"error:{type(exc).__name__}", organ=organ, risk_labels=risk_labels
            )

        if not decision.triggered:
            self.release(session_id)
        return decision

    async def _perform_upgrade(
        self,
        *,
        session_id: str,
        frame_id: str,
        organ: str | None,
        patient_id: str | None,
        risk_labels: list[str],
    ) -> UpgradeDecision:
        resolved_organ = organ
        resolved_patient = patient_id

        async with AsyncSessionLocal() as db:
            session = None
            if not resolved_organ or not resolved_patient:
                session = await SessionRepository(db).get_session(session_id)
                if session is None:
                    return UpgradeDecision(
                        False, "session_not_found", organ=resolved_organ, risk_labels=risk_labels
                    )
                resolved_patient = resolved_patient or session.patient_id
                resolved_organ = resolved_organ or _pick_organ(
                    getattr(session, "target_organs", None), None
                )
            if not resolved_organ:
                return UpgradeDecision(
                    False, "missing_target_organ", risk_labels=risk_labels
                )

            consent = await ConsentRepository(db).get_latest_consent(resolved_patient)
            consent_status = (
                ConsentStatus(consent.status) if consent else ConsentStatus.DENIED
            )
            if consent_status != ConsentStatus.GRANTED:
                logger.warning(
                    "nav_auto_upgrade_blocked_by_consent",
                    session_id=session_id,
                    patient_id=resolved_patient,
                    consent_status=consent_status.value,
                )
                return UpgradeDecision(
                    False,
                    f"consent_{consent_status.value}",
                    organ=resolved_organ,
                    risk_labels=risk_labels,
                )

            clinical_text = (
                "导航高危标记自动升级（触发标签："
                + "、".join(risk_labels)
                + "）。本次请求由导航链路自动发起，等待医生确认与补充。"
            )
            request_id = await DiagnosisRepository(db).create_request(
                session_id=session_id,
                organ=resolved_organ,
                response_tier=self._tier,
                clinical_text=clinical_text,
            )
            await db.commit()

            actor = f"nav_auto_upgrade:{frame_id}"
            try:
                await AuditService(db).log_action(
                    action=AUDIT_ACTION,
                    actor=actor,
                    session_id=session_id,
                    target_type="diagnosis_request",
                    target_id=request_id,
                    detail={
                        "reason": UPGRADE_REASON_RISK_FLAG,
                        "risk_labels": risk_labels,
                        "organ": resolved_organ,
                        "tier": self._tier.value,
                        "frame_id": frame_id,
                    },
                )
                await db.commit()
            except Exception as exc:
                logger.warning(
                    "nav_auto_upgrade_audit_failed",
                    request_id=request_id,
                    error=str(exc),
                )

        enqueued = await self._enqueue(request_id)
        if not enqueued:
            logger.error(
                "nav_auto_upgrade_enqueue_failed",
                session_id=session_id,
                request_id=request_id,
            )
            return UpgradeDecision(
                False,
                "enqueue_failed",
                request_id=request_id,
                organ=resolved_organ,
                risk_labels=risk_labels,
            )

        logger.warning(
            "nav_auto_upgrade_triggered",
            session_id=session_id,
            frame_id=frame_id,
            request_id=request_id,
            organ=resolved_organ,
            tier=self._tier.value,
            risk_labels=risk_labels,
            upgrade_count=self.upgrade_count(session_id),
        )
        return UpgradeDecision(
            True,
            UPGRADE_REASON_RISK_FLAG,
            request_id=request_id,
            organ=resolved_organ,
            risk_labels=risk_labels,
        )

    async def _enqueue(self, request_id: str) -> bool:
        """把请求交给后台 worker 池（延迟导入避免循环依赖）。"""
        if self._enqueue_override is not None:
            return await self._enqueue_override(request_id)
        from app.services.diagnosis_worker import enqueue_diagnosis

        return await enqueue_diagnosis(request_id)


# ----------------------------------------------------------------------
# 进程级单例
# ----------------------------------------------------------------------
_navigation_upgrade_service: NavigationUpgradeService | None = None


def get_navigation_upgrade_service() -> NavigationUpgradeService:
    """获取进程级升级服务（无 asyncio 原语，无需绑定事件循环）。"""
    global _navigation_upgrade_service
    if _navigation_upgrade_service is None:
        _navigation_upgrade_service = NavigationUpgradeService()
    return _navigation_upgrade_service


def reset_navigation_upgrade_service() -> None:
    """清除单例（测试用）。"""
    global _navigation_upgrade_service
    _navigation_upgrade_service = None


__all__ = [
    "AUDIT_ACTION",
    "UPGRADE_REASON_RISK_FLAG",
    "NavigationUpgradeService",
    "UpgradeDecision",
    "collect_risk_labels",
    "get_navigation_upgrade_service",
    "reset_navigation_upgrade_service",
]
