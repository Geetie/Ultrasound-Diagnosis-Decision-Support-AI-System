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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

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


# ----------------------------------------------------------------------
# A.88 配置接线④：`dispatch_all`（多专科并行）—— 门控开关 + dry-run 计划
# ----------------------------------------------------------------------
#
# 为什么这里只有"计划 + 受门控的可选执行"，**没有**接进 `run_multi_organ_diagnosis`
# 的主循环（诚实交付，理由不是"来不及"）：
#
#   ① **显存**：`dispatch_all` 是 ``asyncio.gather`` 真并发，同一时刻多个
#      specialist 各打 vLLM。本机 48GB，SKILL 记录在跑的组合已占 ≈45.4GB
#      （8002 主生成 0.46 + 判官 0.36 + 嵌入 6.8 等常驻）→ 并发峰值按专科数
#      叠加，极易 OOM，而且会把**整台机器**的推理服务一起打挂（不只本服务）。
#   ② **语义**：主循环是"逐器官跑完整管线"（含每器官自己的融合 / SafetyGateway /
#      写终态，见 ``_run_single_organ`` → ``run_diagnosis_pipeline``）。
#      `dispatch_all` 只覆盖 ``dispatcher.invoke`` 一段，若用它替换 ``_run_single_organ``
#      就等于**跳过融合与安全门**直接拿专科响应当结论 —— 那是削弱安全链路，
#      不是"接线"。而把两条都拼起来（先并行 dispatch 再补跑管线）会让同一次
#      判读调用两次 LLM，成本翻倍且两份结论可能不一致。
#   ③ **预算**：现状是串行 + 单器官 30s + 全局 90s。并行化会改变"90s 内能跑完
#      几个器官"的隐含前提（原本 3×30s=90s 正好耗尽），需要真机重标定。
#
# 因此本次交付：契约测试（tests/unit/agt/test_a88_dispatch_all_contract.py）
# + 默认关的门控（``multi_organ_parallel_dispatch_enabled=False``）
# + 可运行的 dry-run 计划（本函数，不发任何 LLM 请求）。
# 真机验证显存与端到端耗时后，再决定是否把 ``dispatch_organs_parallel`` 接进主循环。

#: dry-run 计划里"为什么当前不建议打开"的固定判据（真机重标定后应更新这里）。
_PARALLEL_DISPATCH_BLOCKERS: tuple[str, ...] = (
    "显存：dispatch_all 是 asyncio.gather 真并发，本机 48GB 常驻组合已占 "
    "≈45.4GB（8002 主生成 0.46 + 判官 0.36 + 嵌入 6.8 等），并发峰值按专科数"
    "叠加易 OOM，会连带打挂整机推理服务。",
    "预算：现有语义是「串行 + 单器官 30s + 全局 90s」，3×30s 正好耗尽 90s；"
    "并行会改变隐含前提，需真机重标定后才能改默认。",
    "语义：并行只覆盖 dispatcher.invoke 一段，不含每器官的融合/SafetyGateway/"
    "写终态（在 run_diagnosis_pipeline）；替换主循环等于跳过安全链路。",
)


def parallel_dispatch_plan(
    organs: Sequence[Organ | str],
    *,
    dispatcher: Any | None = None,
    gate_enabled: bool | None = None,
) -> dict[str, Any]:
    """产出多器官并行 dispatch 的**可执行计划**（dry-run，绝不发 LLM 请求）。

    这是"能力真实存在且可观测"的落点：即便门控默认关，运维/答辩也能一眼看到
    "这条路径要做什么、受什么限制、当前为什么没开"，而不是一个只有代码没有
    入口的壳。

    Args:
        organs: 目标器官列表（``Organ`` 或字符串；重复项**保留**——同器官多专科
            是喂饱 `GRADING_CONFLICT`（要求同器官 ≥2 条）的合法用法）。
        dispatcher: 可选，用于检出"哪些器官没有注册 specialist"（缺了会静默
            降级成占位响应，所以必须提前报出来）。
        gate_enabled: 覆盖门控判定（测试/审计用）；``None`` 时读 settings。

    Returns:
        ``{mode, gate_enabled, organs, estimated_llm_calls, missing_specialists,
        recommended, reasons}``。``mode`` 恒为 ``"dry_run"``。
    """
    normalized: list[Organ] = []
    for item in organs:
        if isinstance(item, Organ):
            normalized.append(item)
            continue
        try:
            normalized.append(Organ(item))
        except ValueError:
            logger.warning("parallel_dispatch_plan_dropped_unknown_organ", organ=item)

    if gate_enabled is None:
        gate_enabled = bool(
            getattr(settings, "multi_organ_parallel_dispatch_enabled", False)
        )

    missing: list[str] = []
    if dispatcher is not None:
        registered = getattr(dispatcher, "_specialists", {}) or {}
        missing = [o.value for o in normalized if o not in registered]

    reasons = list(_PARALLEL_DISPATCH_BLOCKERS)
    if missing:
        reasons.append(
            "注册不全：这些器官没有 specialist，真跑会静默降级成占位响应 → "
            f"{missing}"
        )

    return {
        "mode": "dry_run",
        "gate_enabled": gate_enabled,
        "organs": [o.value for o in normalized],
        "estimated_llm_calls": len(normalized),
        "missing_specialists": missing,
        # 当前不推荐打开：三条阻塞项都还没在真机消除
        "recommended": False,
        "reasons": reasons,
    }


async def dispatch_organs_parallel(
    *,
    session_id: str,
    organs: Sequence[Organ],
    dispatcher: Any,
    dry_run: bool = True,
    gate_enabled: bool | None = None,
    context: Any | None = None,
) -> dict[str, Any]:
    """多专科并行 dispatch 的**受门控**入口（默认 dry-run）。

    行为分层（每一层都留痕，不静默）：
      1. ``dry_run=True``（默认）→ 返回 :func:`parallel_dispatch_plan` 的计划，
         **不调用任何 specialist**；
      2. ``dry_run=False`` 但门控关闭 → 返回 ``status="disabled"`` + 真实原因，
         **仍然不调用**（这是"默认关"的可执行含义，不是注释里的愿望）；
      3. ``dry_run=False`` 且门控打开 → 走 :func:`app.agents.dispatcher.dispatch_all`
         真并发，返回各专科响应（**不聚合**，由调用方决定）。

    Returns:
        dict：``status`` ∈ {``dry_run``, ``disabled``, ``dispatched``}。
    """
    from app.agents.dispatcher import dispatch_all

    if dry_run:
        plan = parallel_dispatch_plan(organs, dispatcher=dispatcher, gate_enabled=gate_enabled)
        plan["session_id"] = session_id
        # 三态统一用 ``status`` 表达；``mode`` 保留 dry_run 语义供计划消费方读。
        plan["status"] = "dry_run"
        return plan

    if gate_enabled is None:
        gate_enabled = bool(
            getattr(settings, "multi_organ_parallel_dispatch_enabled", False)
        )

    if not gate_enabled:
        reason = (
            "multi_organ_parallel_dispatch_enabled=False：并行 dispatch 默认关闭"
            "（显存峰值 / 串行 90s 预算 / 只覆盖 dispatcher 段 三条阻塞项未消除，"
            "见 settings.py 该键注释）。本调用未发出任何 LLM 请求。"
        )
        logger.warning(
            "parallel_dispatch_refused_gate_off",
            session_id=session_id,
            organs=[o.value for o in organs],
            reason=reason,
        )
        return {
            "status": "disabled",
            "session_id": session_id,
            "organs": [o.value for o in organs],
            "reason": reason,
            "responses": [],
        }

    if context is None:
        from app.agents.protocols import AgentContext

        context = AgentContext(
            request_id=f"multi_organ_parallel_{session_id}",
            session_id=session_id,
            organ=organs[0] if organs else Organ.THYROID,
            user_query="multi-organ parallel dispatch",
        )

    responses = await dispatch_all(dispatcher, context, list(organs))
    logger.info(
        "parallel_dispatch_completed",
        session_id=session_id,
        organs=[o.value for o in organs],
        response_count=len(responses),
    )
    return {
        "status": "dispatched",
        "session_id": session_id,
        "organs": [o.value for o in organs],
        "responses": [
            {
                "organ": r.organ.value if isinstance(r.organ, Organ) else str(r.organ),
                "agent_name": r.agent_name,
                "confidence": round(float(r.confidence or 0.0), 4),
                "needs_review": bool(r.needs_review),
            }
            for r in responses
        ],
    }


@dataclass
class OrganRunResult:
    """单个器官的执行结果。"""

    organ: str
    status: str  # OrganStatus 值
    request_id: str | None = None
    confidence: float = 0.0
    elapsed_ms: float = 0.0
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == OrganStatus.COMPLETED.value

    def as_dict(self) -> dict[str, Any]:
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
    organs: list[str] = field(default_factory=list)
    organ_results: list[OrganRunResult] = field(default_factory=list)
    organ_statuses: dict[str, str] = field(default_factory=dict)
    total_ms: float = 0.0
    partial: bool = False
    #: A.88 跨器官融合汇总（``cross_organ_fusion`` 的返回值）。
    #: 空 dict = 未执行（例如没有可用结论）；``status`` 见该模块的四种取值。
    cross_organ: dict[str, Any] = field(default_factory=dict)

    def organs_with_status(self, status: str) -> list[str]:
        return [r.organ for r in self.organ_results if r.status == status]

    @property
    def completed_organs(self) -> list[str]:
        return self.organs_with_status(OrganStatus.COMPLETED.value)

    @property
    def failed_organs(self) -> list[str]:
        return self.organs_with_status(OrganStatus.FAILED.value)

    @property
    def skipped_organs(self) -> list[str]:
        return self.organs_with_status(OrganStatus.SKIPPED.value)

    @property
    def needs_review_organs(self) -> list[str]:
        return self.organs_with_status(OrganStatus.NEEDS_REVIEW.value)

    def as_dict(self) -> dict[str, Any]:
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
            # A.88：跨器官融合汇总（无结论时为 {}，序列化安全）
            "cross_organ": dict(self.cross_organ),
        }


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------


async def run_multi_organ_diagnosis(
    session_id: str,
    *,
    organs: Sequence[str] | None = None,
    per_organ_timeout_s: float | None = None,
    total_timeout_s: float | None = None,
    response_tier: ResponseTier | None = None,
    clinical_text: str | None = None,
    patient_context: dict[str, Any] | None = None,
    image_ids: Sequence[str] | None = None,
) -> MultiOrganOutcome:
    """对会话执行多器官串行诊断。

    Args:
        session_id: 会话 ID（须已存在）。
        organs: 显式指定器官顺序；``None`` 时取会话 ``target_organs``。
        per_organ_timeout_s: 单器官超时（默认取 settings，30s）。
        total_timeout_s: 全局超时预算（默认取 settings，90s）。
        response_tier: 新建请求时使用的档位；默认
            ``settings.multi_organ_default_response_tier``。
        clinical_text / patient_context / image_ids: A.88 新增。临床事实由**本次
            请求**显式传入（与单器官端点一致）；缺省时仍从同会话已有请求继承。
            **为什么必须补**：多器官请求体原先没有任何临床字段，只能靠继承 ——
            若该会话从未发过单器官请求，医生这次填的临床信息与勾选的禁忌条件
            就进不了安全网关（见 ``MultiOrganDiagnosisRequest`` 的 docstring）。

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

        # A.88 配置接线④：把"并行 dispatch 这条路当前是否可用 / 为什么没开"
        # 写进启动日志（**只记录计划，不发 LLM 请求**）。这样答辩或排障时能直接
        # 看到能力状态，而不是只能读代码判断"是不是忘了接"。
        # 真并发入口是 `dispatch_organs_parallel(..., dry_run=False)`，受
        # `multi_organ_parallel_dispatch_enabled` 门控（默认关，理由见 settings 注释）。
        logger.info(
            "multi_organ_parallel_dispatch_plan",
            session_id=session_id,
            **parallel_dispatch_plan(organ_list),
        )

        # 会话进入 active：多器官串行进行中。
        organ_statuses: dict[str, str] = dict(getattr(session, "organ_statuses", None) or {})
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
                # A.88：本次请求显式带的临床事实优先；缺省仍从 donor 继承。
                clinical_text=clinical_text,
                patient_context=patient_context,
                image_ids=list(image_ids) if image_ids else None,
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

        # A.88 跨器官融合（**必须放在全部器官跑完之后**）：
        # 融合层的冲突检测 / 跨器官一致性都要求 `len(responses) >= 2`
        # （app/fusion/conflict.py:287,313,335,384；consistency.py:656），
        # 而单器官端点每次只产 1 份结论 → 那两条防线在生产里从未被喂过。
        # 多器官会话是唯一"天然 ≥2 份独立结论"的场景，所以接在这里。
        # 只追加旁信息、不改任何器官的 status/confidence/正文（见该模块 docstring）。
        outcome.cross_organ = await _apply_cross_organ_fusion(
            diagnosis_repo=diagnosis_repo,
            session_id=session_id,
            organ_results=outcome.organ_results,
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
    request_id: str | None,
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
    reason: str | None = None
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
    except Exception as exc:
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
    reason: str | None,
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
    except Exception as exc:
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
    except Exception as exc:
        logger.warning(
            "multi_organ_mark_timeout_failed",
            request_id=request_id,
            error=str(exc),
        )


# ----------------------------------------------------------------------
# A.88 跨器官融合（接线：多器官会话是唯一"天然 ≥2 份独立结论"的场景）
# ----------------------------------------------------------------------


def _has_conclusion(result: OrganRunResult) -> bool:
    """该器官是否**产出了结论**（可参与跨器官校验）。

    ``completed`` / ``needs_review`` 都有落库正文（后者只是被标记人工复核）；
    ``failed`` / ``skipped`` 没有正文 —— 把它们塞进融合层会让"空正文"参与
    分级/建议关键词匹配，产生假冲突假一致。
    """
    return result.status in (
        OrganStatus.COMPLETED.value,
        OrganStatus.NEEDS_REVIEW.value,
    )


async def _apply_cross_organ_fusion(
    *,
    diagnosis_repo: DiagnosisRepository,
    session_id: str,
    organ_results: Sequence[OrganRunResult],
) -> dict[str, Any]:
    """收集各器官已落库的结论 → 跑跨器官融合 → 写回 findings。

    写回位置：每个参与器官的 ``findings["cross_organ_fusion"]``。为什么写进各器官
    而不是新建一张表：读路径（``GET /result/{id}``）按 request_id 取单个器官的结果，
    跨器官信息必须能被**同一个请求**读到，否则前端要多打一次接口（且要处理"哪个
    器官是主"的问题）。写入**不改** status / confidence / 结论正文。

    Returns:
        融合汇总 dict（含 ``status``）；任何异常都降级为 ``{"status": "unavailable"}``。
    """
    candidates = [r for r in organ_results if _has_conclusion(r) and r.request_id]
    if len(candidates) < 2:
        # 诚实口径：不足 2 个器官产出结论 = 前提不成立，不是"无冲突"。
        logger.info(
            "cross_organ_skipped_insufficient_conclusions",
            session_id=session_id,
            concluded_organs=[r.organ for r in candidates],
        )
        return {
            "status": "insufficient_responses",
            "session_id": session_id,
            "organ_count": len(candidates),
            "reason": "fewer than 2 organs produced a conclusion",
        }

    # 读取落库 findings（管线写终态用的是**独立短会话**，这里的会话可能还缓存着
    # processing 快照 —— 与 _read_back_status 同因，先 expire_all 再读）。
    expire = getattr(getattr(diagnosis_repo, "db", None), "expire_all", None)
    if expire is not None:
        expire()

    payloads: list[dict[str, Any]] = []
    rows_by_request: dict[str, Any] = {}
    for run in candidates:
        row = await diagnosis_repo.get_result(str(run.request_id))
        if row is None:
            logger.warning(
                "cross_organ_result_row_missing",
                session_id=session_id,
                organ=run.organ,
                request_id=run.request_id,
            )
            continue
        rows_by_request[str(run.request_id)] = row
        payloads.append(
            {
                "organ": run.organ,
                "findings": row.findings if isinstance(row.findings, dict) else {},
            }
        )

    fusion_engine = _resolve_fusion_engine()
    from app.services.cross_organ_fusion import cross_organ_fusion

    summary = await cross_organ_fusion(
        session_id=session_id,
        organ_results=payloads,
        fusion_engine=fusion_engine,
    )

    # 写回各参与器官的 findings（只加一个键，其余字段原样保留）。
    for request_id, row in rows_by_request.items():
        findings = dict(row.findings) if isinstance(row.findings, dict) else {}
        findings["cross_organ_fusion"] = summary
        try:
            await diagnosis_repo.update_findings(request_id=request_id, findings=findings)
        except Exception as exc:
            logger.warning(
                "cross_organ_findings_write_failed",
                session_id=session_id,
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
    await _commit_if_possible(getattr(diagnosis_repo, "db", None))
    return summary


def _resolve_fusion_engine() -> Any:
    """取应用级的融合引擎（复用同一套检测器与阈值）。

    **不自己新建** ConflictDetector / ConsistencyChecker：另建一套就会出现
    "两条产线两套阈值"，且一致性校验需要注入了 RAG 的实例（指南比对才可能真跑）。
    """
    try:
        from app.main import app as _app_instance

        return getattr(_app_instance.state, "fusion_engine", None)
    except Exception as exc:
        logger.warning(
            "cross_organ_fusion_engine_lookup_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


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
    organ_statuses: dict[str, str],
    status: SessionStatus | None = None,
) -> None:
    """把器官进度写回会话（每器官提交一次，保证外部可见）。"""
    try:
        await session_repo.update_session(
            session_id=session_id,
            status=status,
            organ_statuses=dict(organ_statuses),
        )
        await _commit_if_possible(getattr(session_repo, "db", None))
    except Exception as exc:
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
    except Exception as exc:
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
    clinical_text: str | None = None,
    patient_context: dict | None = None,
    image_ids: list[str] | None = None,
) -> str | None:
    """复用会话内该器官的可重跑请求；不存在则新建。

    临床事实（clinical_text / patient_context / image_ids）会透传给
    ``create_request``——它们是 SafetyGateway 的检查对象，漏传会让禁忌检查
    失去事实依据（P2-10）。缺省时从同会话已有请求继承。
    """
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
        # ⚠️ 合规必填（P2-10 修复，2026-10-04）：clinical_text / patient_context /
        # image_ids 是 **SafetyGateway 的检查对象**。不传 →
        # build_sg_output_data 拿到的患者事实全空 → SG 的禁忌匹配
        # （contraindication）拿不到既往症，只能走"缺事实 → 保守判命中"分支，
        # **等于把合规检查变成了摆设**。单器官路径（api/v1/diagnosis.py:207-215）
        # 一直是全量传入的，多器官这里只传三字段是漏传，不是设计选择。
        #
        # 数据来源：优先用调用方显式传入的；否则从该会话**已有的诊断请求**继承
        # （医生在单器官路径下已经录过 clinical_text/patient_context/image_ids，
        #   多器官请求属于同一会话，继承它才能让 SG 看到与单器官一致的事实）。
        donor = next(
            (
                r
                for r in existing
                if getattr(r, "clinical_text", None)
                or getattr(r, "patient_context", None)
                or getattr(r, "image_ids", None)
            ),
            None,
        )
        eff_text = clinical_text or (str(getattr(donor, "clinical_text", "") or "") or None)
        eff_ctx = patient_context if patient_context is not None else (
            getattr(donor, "patient_context", None) if donor is not None else None
        )
        eff_images = image_ids or (list(getattr(donor, "image_ids", None) or []) or None)

        return await diagnosis_repo.create_request(
            session_id=session_id,
            organ=organ.value,
            response_tier=response_tier,
            clinical_text=eff_text,
            patient_context=eff_ctx,
            image_ids=eff_images,
        )
    except Exception as exc:
        logger.error(
            "multi_organ_create_request_failed",
            session_id=session_id,
            organ=organ.value,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _resolve_organs(
    organs: Sequence[str] | None, session_organs: Sequence[str] | None
) -> list[Organ]:
    """归一化器官列表：按规格顺序排序、去重、丢弃非法值。"""
    raw = list(organs) if organs else list(session_organs or [])
    resolved: list[Organ] = []
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
    except ValueError:  # silent-ok: 取值不可解析 → 回退默认（正常控制流）
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
    except Exception as exc:
        logger.debug("multi_organ_latency_metric_failed", error=str(exc))


async def _commit_if_possible(db: Any) -> None:
    """提交事务（对测试替身保持宽容）——见 diagnosis_pipeline 同名函数。"""
    import inspect

    commit = getattr(db, "commit", None)
    if commit is None:
        return
    try:
        maybe = commit()
    except Exception as exc:
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
