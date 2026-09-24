"""T-GB-AGT-01 — 多器官联合诊断（三器官串行 + 部分完成处理）。

规格：

- ``01-系统架构设计.md``：一次会话可对甲状腺 / 肝脏 / 心脏按序推理，
  单器官失败不得阻断其余器官。
- ``03-接口规范.md`` / ``06-开发环境与进度规划.md``：单器官超时 30s、
  多器官串行上限 90s；会话以 ``target_organs`` + ``organ_statuses``
  表达多器官进度。

设计要点：

1. **在既有单器官管线之上编排，不改其语义**：每个器官仍走
   :func:`app.services.diagnosis_pipeline.run_diagnosis_pipeline`
   （调度 → 融合 → SafetyGateway → 写库 → 推送），本模块只负责
   「串行顺序 + 超时预算 + 状态回写 + 部分完成汇总」。
   既有 4000+ 用例因此零回归。
2. **部分完成**：某器官超时或失败只标记该器官，其余器官继续；会话状态
   汇总为 ``completed`` / ``needs_review`` / ``partial`` / ``failed``
   （``SessionStatus`` 中 ``partial`` / ``needs_review`` 此前无任何写入方，
   本模块是首个使用者）。
3. **超时预算**：全局预算 ``multi_organ_total_timeout_seconds``（默认 90s）
   为硬上限；单器官实际预算 = ``min(单器官预算, 剩余全局预算)``。
   全局预算耗尽的器官标记 ``skipped``（而非 ``failed``）——它不是推理失败，
   医生可单独重跑，不应污染质量统计。
4. **进度推送**：复用前端已声明（``frontend/src/types/websocket.ts`` 的
   ``WsOrganSwitching``）但后端此前从未发送的 ``organ_switching`` 消息，
   在器官开始 / 结束时各推一次，前端据此显示「正在切换至肝脏」。
5. **不新增数据模型**：器官状态写入会话既有 ``organ_statuses`` 字段。
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

import structlog

from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.models.schemas import Organ, OrganStatus, ResponseTier, SessionStatus
from app.repositories import DiagnosisRepository, SessionRepository

logger = structlog.get_logger("app.services.multi_organ_orchestrator")


# 规格指定的串行顺序（甲状腺 → 肝脏 → 心脏）。
ORGAN_ORDER: tuple[Organ, ...] = (Organ.THYROID, Organ.LIVER, Organ.CARDIAC)

# 请求可复用（允许重跑）的状态集合。
REUSABLE_REQUEST_STATUSES = frozenset({"processing", "failed", "needs_review", "timeout"})

# 器官级失败分类（写入 findings，供前端与运维区分原因）。
REASON_PER_ORGAN_TIMEOUT = "per_organ_timeout"
REASON_PIPELINE_ERROR = "pipeline_error"
REASON_TOTAL_BUDGET_EXHAUSTED = "total_budget_exhausted"
REASON_SESSION_NOT_FOUND = "session_not_found"


class SessionNotFoundError(RuntimeError):
    """会话不存在（由 API 层转换为 404）。"""


@dataclass
class OrganRunResult:
    """单个器官的执行结果。"""

    organ: str
    status: str  # OrganStatus 值
    request_id: Optional[str] = None
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    reason: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.status == OrganStatus.COMPLETED.value

    def as_dict(self) -> Dict[str, Any]:
        return {
            "organ": self.organ,
            "status": self.status,
            "request_id": self.request_id,
            "confidence": round(self.confidence, 4),
            "elapsed_ms": round(self.elapsed_ms, 2),
            "reason": self.reason,
        }


@dataclass
class MultiOrganOutcome:
    """一次多器官联合诊断的汇总结果。"""

    session_id: str
    session_status: str  # SessionStatus 值
    organs: List[str] = field(default_factory=list)
    organ_results: List[OrganRunResult] = field(default_factory=list)
    organ_statuses: Dict[str, str] = field(default_factory=dict)
    total_ms: float = 0.0
    partial: bool = False

    def organs_with_status(self, status: str) -> List[str]:
        return [r.organ for r in self.organ_results if r.status == status]

    @property
    def completed_organs(self) -> List[str]:
        return self.organs_with_status(OrganStatus.COMPLETED.value)

    @property
    def failed_organs(self) -> List[str]:
        return self.organs_with_status(OrganStatus.FAILED.value)

    @property
    def skipped_organs(self) -> List[str]:
        return self.organs_with_status(OrganStatus.SKIPPED.value)

    @property
    def needs_review_organs(self) -> List[str]:
        return self.organs_with_status(OrganStatus.NEEDS_REVIEW.value)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "session_status": self.session_status,
            "organs": list(self.organs),
            "organ_statuses": dict(self.organ_statuses),
            "organ_results": [r.as_dict() for r in self.organ_results],
            "completed_organs": self.completed_organs,
            "failed_organs": self.failed_organs,
            "skipped_organs": self.skipped_organs,
            "needs_review_organs": self.needs_review_organs,
            "partial": self.partial,
            "total_ms": round(self.total_ms, 2),
        }


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------


async def run_multi_organ_diagnosis(
    session_id: str,
    *,
    organs: Optional[Sequence[str]] = None,
    per_organ_timeout_s: Optional[float] = None,
    total_timeout_s: Optional[float] = None,
    response_tier: Optional[ResponseTier] = None,
) -> MultiOrganOutcome:
    """对会话执行多器官串行诊断。

    Args:
        session_id: 会话 ID（须已存在）。
        organs: 显式指定器官顺序；``None`` 时取会话 ``target_organs``。
        per_organ_timeout_s: 单器官超时（默认取 settings，30s）。
        total_timeout_s: 全局超时预算（默认取 settings，90s）。
        response_tier: 新建请求时使用的档位；默认
            ``settings.multi_organ_default_response_tier``。

    Returns:
        :class:`MultiOrganOutcome`（含各器官状态与会话汇总状态）。

    Raises:
        SessionNotFoundError: 会话不存在。
    """
    t_start = time.monotonic()
    per_organ_budget = float(
        per_organ_timeout_s or settings.multi_organ_per_organ_timeout_seconds
    )
    total_budget = float(total_timeout_s or settings.multi_organ_total_timeout_seconds)
    tier = response_tier or _default_response_tier()
    deadline = t_start + total_budget

    outcome: MultiOrganOutcome = MultiOrganOutcome(
        session_id=session_id,
        session_status=SessionStatus.PENDING.value,
    )

    async with AsyncSessionLocal() as db:
        session_repo = SessionRepository(db)
        diagnosis_repo = DiagnosisRepository(db)

        session = await session_repo.get_session(session_id)
        if session is None:
            logger.warning(
                "multi_organ_session_not_found",
                session_id=session_id,
            )
            raise SessionNotFoundError(f"session not found: {session_id}")

        organ_list = _resolve_organs(organs, getattr(session, "target_organs", None))
        outcome.organs = [o.value for o in organ_list]
        if not organ_list:
            logger.warning("multi_organ_no_organs", session_id=session_id)
            outcome.total_ms = (time.monotonic() - t_start) * 1000.0
            return outcome

        # 会话进入 active：多器官串行进行中。
        organ_statuses: Dict[str, str] = dict(getattr(session, "organ_statuses", None) or {})
        for organ in organ_list:
            organ_statuses.setdefault(organ.value, OrganStatus.PENDING.value)
        await _persist_progress(
            session_repo, session_id, organ_statuses, SessionStatus.ACTIVE
        )

        existing = await diagnosis_repo.get_results_by_session(session_id)

        for organ in organ_list:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                # 全局预算耗尽：标记 skipped，保留医生单独重跑的入口。
                run_result = OrganRunResult(
                    organ=organ.value,
                    status=OrganStatus.SKIPPED.value,
                    reason=REASON_TOTAL_BUDGET_EXHAUSTED,
                )
                outcome.organ_results.append(run_result)
                organ_statuses[organ.value] = run_result.status
                await _persist_progress(session_repo, session_id, organ_statuses)
                continue

            budget = min(per_organ_budget, remaining)
            request_id = await _resolve_request_id(
                diagnosis_repo=diagnosis_repo,
                existing=existing,
                session_id=session_id,
                organ=organ,
                response_tier=tier,
            )

            organ_statuses[organ.value] = OrganStatus.IN_PROGRESS.value
            await _persist_progress(session_repo, session_id, organ_statuses)
            await _push_organ_switching(session_id, organ.value, switching=True)

            run_result = await _run_single_organ(
                diagnosis_repo=diagnosis_repo,
                db=db,
                session_id=session_id,
                organ=organ,
                request_id=request_id,
                budget_s=budget,
            )
            outcome.organ_results.append(run_result)
            organ_statuses[organ.value] = run_result.status

            await _push_organ_switching(session_id, organ.value, switching=False)
            await _persist_progress(session_repo, session_id, organ_statuses)

        outcome.organ_statuses = organ_statuses
        outcome.session_status = _aggregate_session_status(outcome.organ_results)
        outcome.partial = _is_partial(outcome.organ_results)
        await _persist_progress(
            session_repo,
            session_id,
            organ_statuses,
            SessionStatus(outcome.session_status),
        )

    outcome.total_ms = (time.monotonic() - t_start) * 1000.0
    _record_latency(outcome)
    logger.info("multi_organ_diagnosis_completed", **outcome.as_dict())
    return outcome


# ----------------------------------------------------------------------
# 单器官执行
# ----------------------------------------------------------------------


async def _run_single_organ(
    *,
    diagnosis_repo: DiagnosisRepository,
    db: Any,
    session_id: str,
    organ: Organ,
    request_id: Optional[str],
    budget_s: float,
) -> OrganRunResult:
    """串行执行单个器官的完整管线（含超时与异常降级）。"""
    if not request_id:
        return OrganRunResult(
            organ=organ.value,
            status=OrganStatus.FAILED.value,
            reason="request_unavailable",
        )

    t0 = time.monotonic()
    reason: Optional[str] = None
    try:
        from app.services.diagnosis_pipeline import run_diagnosis_pipeline

        ok = await asyncio.wait_for(
            run_diagnosis_pipeline(request_id), timeout=budget_s
        )
    except asyncio.TimeoutError:
        ok = False
        reason = REASON_PER_ORGAN_TIMEOUT
        logger.warning(
            "multi_organ_organ_timeout",
            session_id=session_id,
            organ=organ.value,
            request_id=request_id,
            budget_s=budget_s,
        )
        await _mark_organ_timeout(diagnosis_repo, request_id)
    except Exception as exc:  # noqa: BLE001 - 单器官失败不得阻断其余器官
        ok = False
        reason = f"{REASON_PIPELINE_ERROR}:{type(exc).__name__}"
        logger.error(
            "multi_organ_organ_pipeline_error",
            session_id=session_id,
            organ=organ.value,
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    elapsed_ms = (time.monotonic() - t0) * 1000.0

    status, confidence = await _read_back_status(
        diagnosis_repo=diagnosis_repo,
        db=db,
        request_id=request_id,
        fallback_ok=ok,
        reason=reason,
    )
    return OrganRunResult(
        organ=organ.value,
        status=status,
        request_id=request_id,
        confidence=confidence,
        elapsed_ms=elapsed_ms,
        reason=reason,
    )


async def _read_back_status(
    *,
    diagnosis_repo: DiagnosisRepository,
    db: Any,
    request_id: str,
    fallback_ok: bool,
    reason: Optional[str],
) -> tuple[str, float]:
    """回读管线写入的最终状态并映射为 ``OrganStatus``。

    管线在独立会话中提交结果，因此这里必须先使本会话的标识映射失效
    （``expire_all``）再查询，否则会读到提交前的 ``processing`` 快照。
    """
    try:
        expire = getattr(db, "expire_all", None)
        if expire is not None:
            expire()
        row = await diagnosis_repo.get_result(request_id)
    except Exception as exc:  # noqa: BLE001 - 回读失败按管线返回值降级
        logger.warning(
            "multi_organ_status_readback_failed",
            request_id=request_id,
            error=str(exc),
        )
        row = None

    if row is None:
        return (
            OrganStatus.COMPLETED.value if fallback_ok else OrganStatus.FAILED.value,
            0.0,
        )

    row_status = str(getattr(row, "status", "") or "")
    confidence = float(getattr(row, "confidence", 0.0) or 0.0)

    if reason == REASON_PER_ORGAN_TIMEOUT:
        # 超时 = 该器官**未产出结论**，与"已产出结论但需医生复核"的
        # needs_review 区分开：会话层据此把该器官计入"部分完成"的缺口。
        # （落库状态仍写 needs_review，便于医生识别为可重跑。）
        return OrganStatus.FAILED.value, confidence

    if row_status == "completed":
        return OrganStatus.COMPLETED.value, confidence
    if row_status == "needs_review":
        return OrganStatus.NEEDS_REVIEW.value, confidence
    if row_status == "failed":
        return OrganStatus.FAILED.value, confidence
    # processing / timeout / 未知：以管线返回值兜底
    if fallback_ok:
        return OrganStatus.COMPLETED.value, confidence
    return OrganStatus.FAILED.value, confidence


async def _mark_organ_timeout(
    diagnosis_repo: DiagnosisRepository, request_id: str
) -> None:
    """单器官超时：把该请求标记为 needs_review（可重跑），不写 failed。"""
    try:
        await diagnosis_repo.update_result(
            request_id=request_id,
            status="needs_review",
            confidence=0.0,
            findings={
                "error": REASON_PER_ORGAN_TIMEOUT,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        await _commit_if_possible(getattr(diagnosis_repo, "db", None))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "multi_organ_mark_timeout_failed",
            request_id=request_id,
            error=str(exc),
        )


# ----------------------------------------------------------------------
# 汇总与持久化
# ----------------------------------------------------------------------


def _status_counts(results: Sequence[OrganRunResult]) -> tuple[int, int, int]:
    """统计（已产出结论、需复核、未产出结论）三类器官数量。

    - 已产出结论：``completed``；
    - 需复核：``needs_review``（有结论但被标记人工复核，仍属"产出"）；
    - 未产出结论：``failed`` / ``skipped``。
    """
    completed = sum(1 for r in results if r.status == OrganStatus.COMPLETED.value)
    needs_review = sum(1 for r in results if r.status == OrganStatus.NEEDS_REVIEW.value)
    unproduced = sum(
        1
        for r in results
        if r.status in (OrganStatus.FAILED.value, OrganStatus.SKIPPED.value)
    )
    return completed, needs_review, unproduced


def _aggregate_session_status(results: Sequence[OrganRunResult]) -> str:
    """把各器官状态汇总为会话状态。

    - 全部 ``completed`` → ``completed``；
    - 无器官失败/跳过、但存在 ``needs_review`` → ``needs_review``
      （三器官都产出了结论，只是部分待医生复核）；
    - 既有产出结论的器官、又有未产出结论的器官 → ``partial``（部分完成）；
    - 全部未产出结论 → ``failed``。
    """
    if not results:
        return SessionStatus.FAILED.value

    completed, needs_review, unproduced = _status_counts(results)

    if completed == len(results):
        return SessionStatus.COMPLETED.value
    if unproduced == 0 and needs_review > 0:
        return SessionStatus.NEEDS_REVIEW.value
    if completed + needs_review > 0:
        return SessionStatus.PARTIAL.value
    return SessionStatus.FAILED.value


def _is_partial(results: Sequence[OrganRunResult]) -> bool:
    """是否"部分完成"：既有产出结论的器官，也有未产出结论的器官。"""
    completed, needs_review, unproduced = _status_counts(results)
    return completed + needs_review > 0 and unproduced > 0


async def _persist_progress(
    session_repo: SessionRepository,
    session_id: str,
    organ_statuses: Dict[str, str],
    status: Optional[SessionStatus] = None,
) -> None:
    """把器官进度写回会话（每器官提交一次，保证外部可见）。"""
    try:
        await session_repo.update_session(
            session_id=session_id,
            status=status,
            organ_statuses=dict(organ_statuses),
        )
        await _commit_if_possible(getattr(session_repo, "db", None))
    except Exception as exc:  # noqa: BLE001 - 进度回写失败不得中断诊断
        logger.warning(
            "multi_organ_progress_persist_failed",
            session_id=session_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _push_organ_switching(session_id: str, organ: str, *, switching: bool) -> None:
    """推送 ``organ_switching`` WS 消息（前端已声明该契约）。"""
    if not settings.multi_organ_progress_push_enabled:
        return
    try:
        from app.api.websocket.nav import manager as nav_manager

        await nav_manager.send_personal_message(
            {
                "type": "organ_switching",
                "organ": organ,
                "switching": switching,
                "timestamp": int(time.time()),
                "session_id": session_id,
            },
            session_id,
        )
    except Exception as exc:  # noqa: BLE001 - 推送失败不影响诊断
        logger.warning(
            "multi_organ_progress_push_failed",
            session_id=session_id,
            organ=organ,
            error=str(exc),
        )


# ----------------------------------------------------------------------
# 请求解析
# ----------------------------------------------------------------------


async def _resolve_request_id(
    *,
    diagnosis_repo: DiagnosisRepository,
    existing: Sequence[Any],
    session_id: str,
    organ: Organ,
    response_tier: ResponseTier,
) -> Optional[str]:
    """复用会话内该器官的可重跑请求；不存在则新建。"""
    candidates = [
        row
        for row in existing
        if str(getattr(row, "organ", "")) == organ.value
        and str(getattr(row, "status", "") or "") in REUSABLE_REQUEST_STATUSES
    ]
    if candidates:
        # ``get_results_by_session`` 按 created_at 倒序，取最新的可重跑请求。
        return str(getattr(candidates[0], "request_id", "") or "") or None

    try:
        return await diagnosis_repo.create_request(
            session_id=session_id,
            organ=organ.value,
            response_tier=response_tier,
        )
    except Exception as exc:  # noqa: BLE001
        logger.error(
            "multi_organ_create_request_failed",
            session_id=session_id,
            organ=organ.value,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _resolve_organs(
    organs: Optional[Sequence[str]], session_organs: Optional[Sequence[str]]
) -> List[Organ]:
    """归一化器官列表：按规格顺序排序、去重、丢弃非法值。"""
    raw = list(organs) if organs else list(session_organs or [])
    resolved: List[Organ] = []
    for item in raw:
        value = getattr(item, "value", item)
        try:
            organ = Organ(str(value))
        except ValueError:
            logger.warning("multi_organ_unknown_organ_skipped", organ=str(value))
            continue
        if organ not in resolved:
            resolved.append(organ)

    # 按规格串行顺序排列；不在规格顺序内的（理论上不存在）追加在后。
    ordered = [o for o in ORGAN_ORDER if o in resolved]
    ordered.extend(o for o in resolved if o not in ORGAN_ORDER)
    return ordered


def _default_response_tier() -> ResponseTier:
    try:
        return ResponseTier(str(settings.multi_organ_default_response_tier))
    except ValueError:
        return ResponseTier.STANDARD


# ----------------------------------------------------------------------
# 可观测性
# ----------------------------------------------------------------------


def _record_latency(outcome: MultiOrganOutcome) -> None:
    """上报多器官端到端延迟（``05-可观测性与运维`` 口径）。

    ``with_retry`` 对应规格中的两档延迟（不含重试 < 25s / 含重试 < 35s）：
    本次运行只要**不是一次干净通过**即计入含重试档——包括器官超时/跳过
    （有 reason）、器官失败或需复核（failed/needs_review，均需重跑）。
    只按 ``reason`` 判断会把"器官失败"错记到不含重试档，使两档延迟统计
    混入需要重跑的样本。
    """
    try:
        from app.observability.metrics_collector import get_metrics_collector

        needs_retry = (
            bool(outcome.failed_organs)
            or bool(outcome.skipped_organs)
            or any(r.reason for r in outcome.organ_results)
            or any(
                getattr(r, "status", None) not in ("completed",)
                for r in outcome.organ_results
            )
        )
        collector = get_metrics_collector()
        collector.record_multi_organ_latency(
            outcome.total_ms / 1000.0,
            with_retry=needs_retry,
        )
    except Exception as exc:  # noqa: BLE001 - 埋点失败不得影响业务
        logger.debug("multi_organ_latency_metric_failed", error=str(exc))


async def _commit_if_possible(db: Any) -> None:
    """提交事务（对测试替身保持宽容）——见 diagnosis_pipeline 同名函数。"""
    import inspect

    commit = getattr(db, "commit", None)
    if commit is None:
        return
    try:
        maybe = commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("multi_organ_commit_call_failed", error=str(exc))
        return
    if inspect.isawaitable(maybe):
        await maybe


__all__ = [
    "ORGAN_ORDER",
    "MultiOrganOutcome",
    "OrganRunResult",
    "SessionNotFoundError",
    "run_multi_organ_diagnosis",
]
