"""T-GA-INT-01 — 诊断管线编排。

提供 :func:`run_diagnosis_pipeline` ：从 DB 读取 request → 构造 AgentContext
→ 调用 dispatcher.invoke → 调用 fusion.fuse → 调用 safety_gateway.check_output
→ 写回 DB → 推送 WebSocket。

失败降级矩阵（每环节 try/except 包装，遵循「失败安全」原则）：

| 环节 | 失败现象 | 降级策略 | 标记字段 |
|------|---------|---------|---------|
| 调度 | dispatcher.invoke 抛错 | confidence=0.0 占位 | ai_diagnosis_failed=True, review_reason="dispatcher_failed" |
| 知识查询 | RAG 返回空 | 走 BM25 fallback（由 RAG Service 内部处理）| evidence.knowledge_ids 留空 |
| 专科推理 | LLM 抛错 | specialist 内部已重试，仍失败则 needs_review | requires_manual_review=True |
| 融合层 | fuse 抛错 | 使用 specialist 最高 confidence 响应 | fusion_status=DEGRADED |
| SafetyGateway | sg.check_output 失败 / 不可达 | service_unavailable=True, 返回 E4005 | consent_status_at_output=DENIED |
| 同意撤回 | SG 返回 consent_status=withdrawn | 强阻断, 抛异常 | review_reason="consent_withdrawn" |

延迟埋点（6 阶段）：
- ``db_read_ms`` / ``dispatcher.invoke_ms`` / ``fusion.fuse_ms`` /
  ``sg.check_output_ms`` / ``db_write_ms`` / ``pipeline.total_ms``
"""
from __future__ import annotations

import asyncio
import inspect
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.routing import AdaptiveRAGRouter, RetrievalProfile, RoutingDecision
from app.config.database import AsyncSessionLocal
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.models.schemas import (
    ConsentStatus,
    DesensitizationStatus,
    DiagnosisQualityControl,
    DiagnosisRequest,
    DiagnosisResult,
    FusionStatus,
    Organ,
    ResponseTier,
)
from app.repositories import (
    DiagnosisRepository,
)
from app.security.three_layer_desensitization import (
    get_three_layer_desensitization_manager,
)
from app.services.inflight_diagnosis import get_inflight_registry
from app.services.rollout_router import (
    RolloutDecision,
    RolloutTrack,
    get_rollout_router,
)
from app.services.safety_gateway_client import safety_gateway_client
from app.services.sg_output_data import build_sg_output_data, extract_findings_text

logger = structlog.get_logger("app.services.diagnosis_pipeline")

# Adaptive RAG 路由器（T-GB-AGT-02）。进程内单例：stats 需跨请求累计，
# 对应 05-可观测性与运维"标准路径占比 ≥70%"的路由分布指标。
_router: AdaptiveRAGRouter | None = None


def get_routing_router() -> AdaptiveRAGRouter:
    """取得进程内路由器单例。"""
    global _router
    if _router is None:
        _router = AdaptiveRAGRouter()
    return _router


async def _commit_if_possible(db: Any) -> None:
    """提交事务（对测试替身保持宽容）。

    生产路径下 ``AsyncSessionLocal`` 产出真实 ``AsyncSession``，必须显式
    ``commit()``：``async with AsyncSessionLocal() as db`` 退出时只调用
    ``close()``（回滚未提交事务），因此此前仅 ``flush()`` 的写库会被丢弃——
    诊断结论只留在 30s TTL 的 Redis 缓存里，缓存过期后外部就读不到结论。
    测试用例普遍用 ``MagicMock`` 充当会话，其 ``commit()`` 不可 await，
    这里做 awaitable 判定，保证既有用例不受影响。
    """
    commit = getattr(db, "commit", None)
    if commit is None:
        return
    try:
        maybe = commit()
    except Exception as exc:
        logger.warning("pipeline_commit_call_failed", error=str(exc))
        return
    if inspect.isawaitable(maybe):
        await maybe


def _coerce_response_tier(value: Any) -> ResponseTier:
    """把 DB 中的 tier 字符串转成枚举；无法识别时按最保守（全量）处理。"""
    try:
        return ResponseTier(str(value))
    except Exception as exc:
        logger.warning(
            "pipeline_response_tier_parse_failed",
            value=str(value),
            error=str(exc),
            fallback="full",
        )
        return ResponseTier.FULL


def _assess_routing(request: Any) -> RoutingDecision | None:
    """按规格 §5.3 给出路由判定（任何异常都不得阻断诊断）。"""
    try:
        return get_routing_router().assess(
            query=str(getattr(request, "clinical_text", "") or ""),
            target_organ=str(getattr(request, "organ", "") or "") or None,
            requested_tier=_coerce_response_tier(getattr(request, "tier", None)),
        )
    except Exception as exc:
        logger.warning(
            "routing_assessment_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


async def _apply_rollout_gate(
    request: Any, routing: RoutingDecision | None
) -> tuple[RoutingDecision | None, RolloutDecision | None]:
    """T-GR-INT-01：按灰度路由决定是否放行新行为。

    未放行的会话把检索档位退回基线（``RetrievalProfile.standard()``），使灰度
    效果真正体现为"一部分会话走新行为、其余走基线"，而不是只在日志里写一行
    "本会话未命中灰度"。

    任何异常都放行：灰度是控制新行为暴露面的手段，不该成为诊断不可用的原因。
    """
    try:
        requested_tier = getattr(routing, "requested_tier", None)
        tier_value = str(getattr(requested_tier, "value", requested_tier) or "").lower()
        track = (
            RolloutTrack.FULL.value if tier_value == "full" else RolloutTrack.COMPACT.value
        )

        decision = await get_rollout_router().decide(
            session_id=str(getattr(request, "session_id", "") or ""),
            track=track,
        )

        if not decision.allowed and routing is not None:
            routing = replace(routing, retrieval_profile=RetrievalProfile.standard())

        logger.info(
            "rollout_routing_decision",
            track=decision.track,
            stage=decision.stage,
            allowed=decision.allowed,
            reason=decision.reason,
            bucket=decision.bucket,
            session_index=decision.session_index,
        )
        return routing, decision
    except Exception as exc:
        logger.warning(
            "rollout_decision_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return routing, None


# ----------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------


async def run_diagnosis_pipeline(request_id: str) -> bool:
    """执行单次诊断的完整管线。

    Args:
        request_id: 诊断请求 ID（由 submit 端点生成）。

    Returns:
        True 表示管线成功完成（无论结果是 completed 还是 needs_review）；
        False 表示管线本身发生不可恢复错误（已写 DLQ）。

    Notes:
        - **失败安全**：任何内部异常都被捕获，记录 warning 并标记
          ``status="failed"`` 写回 DB；
        - **不抛异常**：调用方（worker）只关心 True/False，无需处理异常。
    """
    t_start = time.monotonic()
    timings: dict[str, float] = {}
    # 预置 None：DB 读取本身也可能抛异常，而异常/取消处理分支需要判断
    # "是否已经读到请求行"（有行才谈得上把它交给监控追踪）。
    request = None

    try:
        async with AsyncSessionLocal() as db:
            t = time.monotonic()
            diagnosis_repo = DiagnosisRepository(db)
            request = await diagnosis_repo.get_result(request_id)
            timings["db_read_ms"] = (time.monotonic() - t) * 1000.0

            if request is None:
                logger.error(
                    "pipeline_request_not_found",
                    request_id=request_id,
                )
                return False

            # 患者身份只能从**会话**解析：`diagnosis_results` 表没有 patient_id 列，
            # 原先这里（与 `_safe_safety_check`）用 `getattr(request, "patient_id", ...)`
            # 取，恒为 "unknown" → 2026-09-29 真机：SG 查不到该患者的同意记录
            # → `pipeline_consent_status_unavailable` → 保守 fail-closed，诊断必 failed。
            patient_id = await _resolve_patient_id(db, getattr(request, "session_id", None))
            if patient_id is None:
                logger.error(
                    "pipeline_patient_id_unresolved",
                    request_id=request_id,
                    session_id=getattr(request, "session_id", None),
                )

            # T-G1-INT-06：登记在途诊断。撤回同意时由消费端取消同一患者的
            # 在途任务（见 app/services/inflight_diagnosis.py）。
            get_inflight_registry().register(
                patient_id=patient_id or "unknown",
                request_id=request_id,
            )

            # T-GB-AGT-02: Adaptive RAG 动态路由判定（规格 §5.3）。
            # 判定结果写入 findings 的 routing 段：前端据此展示"初步结论 +
            # 是否切换深度分析"的确认对话框，观测侧据此统计标准路径占比。
            routing_decision = _assess_routing(request)

            # T-GR-INT-01：灰度发布路由。未放行的会话退回基线检索档位，
            # 判定结果落库供观测侧统计放量比例。
            routing_decision, rollout_decision = await _apply_rollout_gate(
                request, routing_decision
            )
        # ← DB session 在此关闭（T-Perf 实测钉子 2026-09-30）：
        # 原先 async with 包住整条管线，dispatcher/fusion 的分钟级 LLM
        # 调用期间事务空闲连接被 idle_in_transaction_session_timeout
        # 掐断，session 退出时隐式 rollback 抛 InterfaceError，整条诊断
        # 挂掉。读段（request/患者 ID/路由判定）结束后立即归还连接；
        # expire_on_commit=False 使 request 的已加载属性在分离后可读。
        # 写库走独立短会话（见 _write_result_to_db）。

        # 1) 调度 (dispatcher.invoke)
        # T-GB-AGT-02：把路由判定的检索档位一并注入，使"标准/全量"
        # 真正作用于专科智能体的检索强度（此前判定结果只落库不生效）。
        t = time.monotonic()
        specialist_responses = await _safe_dispatch(
            request, request_id, routing=routing_decision
        )
        timings["dispatcher.invoke_ms"] = (time.monotonic() - t) * 1000.0

        # 2) 融合 (fusion.fuse)
        t = time.monotonic()
        fusion_result = await _safe_fuse(
            request=request,
            specialist_responses=specialist_responses,
            request_id=request_id,
        )
        timings["fusion.fuse_ms"] = (time.monotonic() - t) * 1000.0

        # 3) 安全网关 (sg.check_output)
        t = time.monotonic()
        sg_result, sg_ok = await _safe_safety_check(
            request=request,
            fusion_result=fusion_result,
            request_id=request_id,
            patient_id=patient_id,
        )
        timings["sg.check_output_ms"] = (time.monotonic() - t) * 1000.0

        # 4) 组装 DiagnosisResult 并写 DB（独立短会话，见 _write_result_to_db）
        t = time.monotonic()
        write_ok = await _write_result_to_db(
            request=request,
            fusion_result=fusion_result,
            sg_result=sg_result,
            sg_ok=sg_ok,
            timings=timings,
            routing=routing_decision,
            rollout=rollout_decision,
            # 传入起点，使落库 findings 里带上真实的 pipeline.total_ms
            # （否则该键只在写库后才出现在本地 timings 中，接口读不到，
            #  processing_time_ms 会静默回退到 mock 值）
            t_start=t_start,
        )
        timings["db_write_ms"] = (time.monotonic() - t) * 1000.0

        # 5) 推送 WebSocket
        try:
            from app.api.websocket.nav import manager as nav_manager
            await nav_manager.send_diagnosis_result(
                session_id=getattr(request, "session_id", ""),
                request_id=request_id,
            )
        except Exception as exc:
            logger.warning(
                "pipeline_websocket_push_failed",
                request_id=request_id,
                error=str(exc),
            )

        timings["pipeline.total_ms"] = (time.monotonic() - t_start) * 1000.0
        logger.info(
            "pipeline_completed",
            request_id=request_id,
            organ=request.organ,
            success=write_ok,
            timings_ms={k: round(v, 2) for k, v in timings.items()},
        )
        return write_ok

    except asyncio.CancelledError:
        # T-G1-INT-06：同意撤回触发的在途取消（见 inflight_diagnosis）。
        # 必须留下终态：任务被取消时若 DB 行停在 in_progress，前端会一直转圈，
        # 且没有任何记录说明这次诊断为什么不告而终。
        logger.warning(
            "pipeline_cancelled_by_consent_withdrawal",
            request_id=request_id,
            elapsed_ms=round((time.monotonic() - t_start) * 1000.0, 2),
        )
        try:
            # shield：取消已投递过一次，收尾写库不能再被同一个取消打断
            await asyncio.shield(_mark_result_cancelled(request_id))
        except Exception as exc:
            logger.error(
                "pipeline_cancel_mark_failed",
                request_id=request_id,
                error=str(exc),
            )
        # 监控：取消同样是终态（同意撤回/策略停止），延迟要计入指标。
        # 注意 `_is_failure` 不把 cancelled 当失败 —— 这是有意为之：
        # R3 度量的是"AI 连续失败"，不是"被政策叫停"。
        if request is not None:
            await _safe_track_diagnosis(
                request,
                request_id=request_id,
                status="cancelled",
                processing_time_ms=int((time.monotonic() - t_start) * 1000.0),
            )
        raise

    except Exception as exc:
        logger.error(
            "pipeline_unhandled_exception",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        # 标记 status="failed" 写回 DB
        try:
            async with AsyncSessionLocal() as db:
                repo = DiagnosisRepository(db)
                await repo.update_result(
                    request_id=request_id,
                    status="failed",
                    confidence=0.0,
                    findings={"error": str(exc), "error_type": type(exc).__name__},
                )
                await _commit_if_possible(db)
        except Exception as exc:
            logger.error(
                "pipeline_failed_status_write_failed",
                request_id=request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
        # 监控：管线级失败必须计入 R3 的滑动窗口，否则"连续失败"永远不会触发。
        if request is not None:
            await _safe_track_diagnosis(
                request,
                request_id=request_id,
                status="failed",
                processing_time_ms=int((time.monotonic() - t_start) * 1000.0),
                confidence=0.0,
            )
        return False

    finally:
        # 无论成功、失败还是被取消，都必须摘牌，否则登记表会把已结束的任务
        # 一直留着，后续同患者的撤回会去 cancel 一个早已完成的任务
        get_inflight_registry().unregister(request_id)
        # 三层脱敏的按运行缓存同样必须清账（否则长跑进程里按 request_id 无界增长）
        _clear_desens_run(request_id)


# ----------------------------------------------------------------------
# 内部：单环节容错
# ----------------------------------------------------------------------


async def _safe_dispatch(
    request, request_id: str, routing: RoutingDecision | None = None
):
    """调用 dispatcher.invoke；失败时返回 [AgentResponse(confidence=0.0)]。

    Args:
        routing: Adaptive RAG 路由判定（T-GB-AGT-02）。传入时其
            ``retrieval_profile`` 会写入 ``AgentContext``，由专科智能体
            透传给知识智能体，使档位真正作用于检索强度。

    Returns:
        ``List[AgentResponse]``（不抛异常）。
    """
    try:
        from app.main import app as _app_instance  # 延迟导入避免循环

        dispatcher = getattr(_app_instance.state, "dispatcher", None)
        if dispatcher is None:
            logger.warning(
                "pipeline_dispatcher_unavailable",
                request_id=request_id,
            )
            return [_make_failed_response("dispatcher_unavailable")]

        organ = Organ(request.organ) if request.organ else Organ.THYROID
        # 三层脱敏 L1（标准化层）：clinical_text 由 create_request 落库（0009 迁移
        # 补列），**落的是原文**；此处脱敏后才交给智能体，避免原文进入 LLM 与 RAG。
        # getattr 兜底兼容测试替身与历史 NULL 行。
        user_query = await _desensitize_input_text(
            request_id=request_id,
            raw_text=str(
                getattr(request, "clinical_text", None) or "请基于提供的影像给出诊断"
            ),
            session_id=getattr(request, "session_id", None),
        )
        context = AgentContext(
            request_id=request_id,
            session_id=request.session_id,
            organ=organ,
            user_query=user_query,
            retrieval_profile=(
                routing.retrieval_profile.as_retrieval_kwargs()
                if routing is not None
                else None
            ),
            imaging_metadata=None,
            prior_messages=[],
        )
        response = await dispatcher.invoke(context)
        return [response]
    except Exception as exc:
        logger.warning(
            "pipeline_dispatch_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return [_make_failed_response(f"dispatcher_failed: {type(exc).__name__}")]


async def _safe_fuse(request, specialist_responses, request_id: str):
    """调用 fusion.fuse；失败时返回带 needs_review 的占位 FusionResult。"""
    try:
        from app.main import app as _app_instance

        fusion_engine = getattr(_app_instance.state, "fusion_engine", None)
        if fusion_engine is None:
            logger.warning(
                "pipeline_fusion_engine_unavailable",
                request_id=request_id,
            )
            return _make_failed_fusion_result(specialist_responses)

        from app.fusion.protocols import FusionRequest

        fusion_req = FusionRequest(
            request_id=request_id,
            organ=Organ(request.organ) if request.organ else Organ.THYROID,
            agent_responses=specialist_responses,
            use_crag=True,
        )
        return await fusion_engine.fuse(fusion_req)
    except Exception as exc:
        logger.warning(
            "pipeline_fuse_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return _make_failed_fusion_result(specialist_responses)


async def _resolve_patient_id(db: Any, session_id: str | None) -> str | None:
    """从 `sessions` 表解析患者 ID。

    为什么不在结果行上取：`diagnosis_results` **没有 patient_id 列**（患者身份属于会话），
    原先 `getattr(request, "patient_id", "unknown")` 因此恒为 `"unknown"`，导致
    SG 查不到该患者的同意记录 → `pipeline_consent_status_unavailable` → 保守 fail-closed。
    2026-09-29 真机（第 1 例真实诊断）正是卡在这里。

    失败安全：解析不出（无 session_id / 查询异常 / 该会话不存在）返回 `None`，
    由调用方决定 fail-closed，不编造患者 ID。
    """
    if not session_id:
        return None
    try:
        from sqlalchemy import select

        from app.models.database import DiagnosisSession

        result = await db.execute(
            select(DiagnosisSession.patient_id).where(
                DiagnosisSession.session_id == session_id
            )
        )
        value = result.scalar_one_or_none()
        return str(value) if value else None
    except Exception as exc:
        logger.warning(
            "pipeline_patient_id_resolve_failed",
            session_id=session_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


async def _safe_safety_check(
    request, fusion_result, request_id: str, patient_id: str | None = None
):
    """调用 sg.check_output；失败时返回 ``(None, False)`` 表示 SG 不可用。

    Args:
        patient_id: 由会话解析出的患者 ID（见 :func:`_resolve_patient_id`）。
            **为 None 时直接 fail-closed**：不能拿 `"unknown"` 去查同意记录 ——
            既必然查不到（等价于把患者当成无同意），又会在 SG 审计里留下一个
            不存在的患者 ID。

    Returns:
        ``(sg_result: dict | None, sg_ok: bool)``
    """
    try:
        # 发给安全网关的载荷**只允许由 app/services/sg_output_data.py 构造**。
        # 读路径（GET /api/v1/diagnosis/result/{id}）也会调一次 SG，两边形状一旦不同，
        # 同一份诊断会在"写"处 pass、在"读"处被判命中禁忌 —— 2026-09-29 真机实测。
        output_data: dict[str, Any] = build_sg_output_data(
            organ=request.organ,
            findings=_extract_findings(fusion_result),
            recommendation=_extract_recommendation(fusion_result),
            patient_context=getattr(request, "patient_context", None),
        )
        if output_data.get("diagnosis_data"):
            logger.info(
                "pipeline_patient_conditions_forwarded",
                request_id=request_id,
                patient_condition_count=len(
                    output_data["diagnosis_data"]["patient_conditions"]
                ),
            )
        # 查询同意状态（2026-09-28 接线审计修复）。
        # 此前这里硬编码 `"granted"`（原注释自述"简化"），三重后果：
        # ① 安全网关层的 `consent_status_at_input is None → raise`（fail-closed）
        #    永远不触发，输入侧同意校验形同虚设；
        # ② 三层脱敏 L3 依赖该值，接上后会拿到假状态（见 batch 4）；
        # ③ 审计链里"输入时同意状态"是伪造的。
        # 现在改为向 SG **直查**（与 SG 的 consent_checker 同源、无缓存）；
        # **拿不到就 fail-closed**（`(None, False)` → 调用方置 failed）。
        if not patient_id:
            logger.error(
                "pipeline_patient_id_unresolved_fail_closed",
                request_id=request_id,
                session_id=getattr(request, "session_id", None),
            )
            return None, False
        consent_status_at_input = await safety_gateway_client.get_consent_status(patient_id)
        if consent_status_at_input is None:
            logger.error(
                "pipeline_consent_status_unavailable",
                request_id=request_id,
                patient_id=patient_id,
            )
            return None, False
        sg_result = await asyncio.wait_for(
            safety_gateway_client.check_output(
            session_id=request.session_id,
            patient_id=patient_id,
            output_data=output_data,
            consent_status_at_input=consent_status_at_input,
            ),
            timeout=settings.pipeline_sg_budget_s,
        )
        # 三层脱敏 L3（SafetyGateway 兜底）：**复用**本次 SG check_output 的结果
        # 登记为第三层，不重复调用 SG（重复调用会产生双份审计事件与额外延迟）。
        await _register_l3_existing_sg_check(
            request_id=request_id, payload=output_data, sg_result=sg_result
        )
        return sg_result, True
    except Exception as exc:
        logger.error(
            "pipeline_safety_check_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None, False


# ----------------------------------------------------------------------
# 内部：写结果
# ----------------------------------------------------------------------


async def _mark_result_cancelled(request_id: str) -> None:
    """把被取消的诊断置为 ``cancelled`` 终态（T-G1-INT-06）。

    独立开一个会话而不是复用管线里那个：管线的事务已随取消进入不确定状态，
    在同一连接上再写很可能被一起回滚。
    """
    try:
        async with AsyncSessionLocal() as db:
            repo = DiagnosisRepository(db)
            await repo.update_result(
                request_id=request_id,
                status="cancelled",
                confidence=0.0,
                findings={
                    "error": "consent_withdrawn",
                    "reason": "in-flight diagnosis cancelled after consent withdrawal",
                },
            )
            await _commit_if_possible(db)
    except Exception as exc:
        logger.error(
            "pipeline_cancel_status_write_failed",
            request_id=request_id,
            error=str(exc),
        )


async def _write_result_to_db(
    request,
    fusion_result,
    sg_result: dict | None,
    sg_ok: bool,
    timings: dict[str, float],
    routing: RoutingDecision | None = None,
    rollout: RolloutDecision | None = None,
    t_start: float | None = None,
) -> bool:
    """根据 fusion_result + sg_result 决定 status / findings / confidence 并写 DB。

    Args:
        t_start: 管线起点的 ``time.monotonic()``。传入时会在 findings 的
            ``timings_ms`` 中补上 ``pipeline.total_ms``（截至写库前），供
            接口的 ``processing_time_ms`` 与前端进度展示直接使用。
    """
    # 1) 决定 status
    if not sg_ok:
        status = "failed"
        confidence = 0.0
        review_reason = "safety_gateway_unavailable"
    else:
        status = "completed"
        # confidence 取 fusion final_response.confidence
        if (
            fusion_result is not None
            and getattr(fusion_result, "final_response", None) is not None
        ):
            confidence = fusion_result.final_response.confidence
        else:
            confidence = 0.0
        # needs_review → status 标记 "needs_review"
        if (
            fusion_result is not None
            and getattr(fusion_result, "needs_review", False)
        ):
            status = "needs_review"
        review_reason = (
            getattr(fusion_result, "review_reason", None) if fusion_result else None
        )

    # 2) 组装 findings
    timings_ms = {k: round(v, 2) for k, v in timings.items()}
    if t_start is not None:
        # 截至写库前的总耗时（不含本次写库本身），接口的 processing_time_ms
        # 与前端进度都读这个键
        timings_ms["pipeline.total_ms"] = round(
            (time.monotonic() - t_start) * 1000.0, 2
        )
    findings = {
        "organ": request.organ,
        "fusion_result": _fusion_result_to_dict(fusion_result),
        "sg_result": sg_result or {"service_unavailable": True},
        "timings_ms": timings_ms,
        "review_reason": review_reason,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        # T-GB-AGT-02 路由判定（前端确认对话框与路由分布观测的依据）
        "routing": routing.as_dict() if routing is not None else None,
        # T-GR-INT-01：灰度判定落库，观测侧据此统计各轨道放量比例
        "rollout": rollout.as_dict() if rollout is not None else None,
    }

    # 三层脱敏 L2（融合层）：对**即将持久化**的输出载荷再脱敏一次。
    # 位置在"组装完成、写库之前"：这是 PII 真正落库的最后一个关口，
    # 也是"LLM 意外生成身份信息"的拦截点。
    findings = await _desensitize_output_payload(
        request_id=request.request_id, payload=findings
    )

    # 3) 写 DB —— **独立短会话**（T-Perf 实测钉子）：
    # 管线主 session（L197）从读请求起持有连接，跨过 dispatcher/fusion 的
    # 分钟级 LLM 调用后连接已被 PG/网络掐断（asyncpg InterfaceError:
    # underlying connection is closed，2026-09-30 真机 44/51 超时根因：
    # 结果写不进 → 请求永远 processing → 轮询查无此单）。pool_pre_ping
    # 只在借出时检测，救不了事务中途的断连——与 _mark_result_cancelled
    # 同理，写终态必须用新会话。
    try:
        async with AsyncSessionLocal() as db:
            fresh_repo = DiagnosisRepository(db)
            await fresh_repo.update_result(
                request_id=request.request_id,
                status=status,
                confidence=confidence,
                findings=findings,
                recommendations=(
                    _extract_recommendation(fusion_result) if fusion_result else None
                ),
                knowledge_ids=(
                    fusion_result.final_response.evidence_chunk_ids
                    if fusion_result
                    and getattr(fusion_result, "final_response", None)
                    and getattr(fusion_result.final_response, "evidence_chunk_ids", None)
                    else None
                ),
            )
            await db.commit()
        # 6) 交MonitorAgent 追踪（R1/R2/R3 + 延迟埋点）。
        #    放在**提交之后**：只追踪已真正落库的终态，避免"写失败也计入监控"。
        await _safe_track_diagnosis(
            request,
            request_id=request.request_id,
            status=status,
            processing_time_ms=int(timings_ms.get("pipeline.total_ms") or 0),
            confidence=confidence,
            sg_ok=sg_ok,
            review_reason=review_reason,
        )
        return True
    except Exception as exc:
        logger.error(
            "pipeline_db_write_failed",
            request_id=request.request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------


# ----------------------------------------------------------------------
# 三层脱敏（T-GA-SEC-03）接线
# ----------------------------------------------------------------------
#
# 2026-09-28 接线审计：`ThreeLayerDesensitizationManager` 此前**只被测试引用**，
# `run_diagnosis_pipeline` 未调用任何 `process_*` —— 合规意义上的"三层纵深"从未
# 生效，且临床原文（`request.clinical_text`）未经第一层就直接进入智能体与 RAG。
#
# ## 幂等为什么用"进程内、按运行"的缓存，而不是 Redis 跳过门
#
# 管线每次运行都从 DB **重新读原始行**（`clinical_text` 落库的是原文，脱敏结果
# 不回写；原文展示由 restore 服务按授权还原）。因此"用 Redis 标记跳过 L1"在重试 /
# 重复投递场景下会让**未脱敏的原文**直接进入智能体 —— 那是安全回退，不是幂等。
# 缓存要防的是另一件事：**同一次运行内把同一份载荷重复脱敏**（对同一份文本再跑
# 一次 L1/L2 会破坏语义）。Redis 侧只写**审计留痕**，明确不作为跳过判据。
#
# ## 缓存为什么必须带上"来源"一起比对（2026-09-28 由既有集成测试抓出）
#
# 第一版只按 `request_id` 存"脱敏后的结果"。当**同一 request_id 出现第二次运行**
# （测试里是同一 stub id 的相邻用例；生产里是重复投递/并发同请求）时，第二次会拿到
# **上一次运行的载荷** —— L2 因此把 A 次的输出当成 B 次的输出写库
# （`tests/integration/int/test_routing_wiring.py` 立刻抓到了：B 次的 findings 里
# 出现了 A 次的 routing 标记）。
# 现在缓存存 `(来源, 结果)`，**来源一致才复用**；来源不同一律重算。
# `finally` 里的 `_clear_desens_run` 仍保留，用于长跑进程的按运行清账。
_desens_l1_cache: dict[str, tuple[str, str]] = {}
_desens_l2_cache: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
_desens_l3_done: set[str] = set()

DESENS_AUDIT_TTL_SECONDS = 3600


def _clear_desens_run(request_id: str) -> None:
    """运行结束清缓存（长跑进程里避免无界增长）。"""
    _desens_l1_cache.pop(request_id, None)
    _desens_l2_cache.pop(request_id, None)
    _desens_l3_done.discard(request_id)


async def _mark_desens_audit(
    request_id: str, layer: str, *, degraded: bool = False
) -> None:
    """把"该层已生效"写入 Redis 供运维审计（best-effort，**不作为跳过判据**）。"""
    try:
        await RedisPool.setex(
            RedisKey.custom_idempotency("desens", f"{layer}:{request_id}"),
            DESENS_AUDIT_TTL_SECONDS,
            "degraded" if degraded else "applied",
        )
    except Exception as exc:
        logger.warning(
            "desens_audit_mark_failed",
            request_id=request_id,
            layer=layer,
            error=str(exc),
        )


async def _desensitize_input_text(
    *, request_id: str, raw_text: str, session_id: str | None = None
) -> str:
    """L1 标准化层：临床文本脱敏后才交给智能体（``AgentContext.user_query``）。

    失败安全：引擎异常时**原文透传**并 error 留痕 —— 与既有降级矩阵一致
    （第三层 SG 仍是最终兜底），但绝不静默。
    """
    cached = _desens_l1_cache.get(request_id)
    if cached is not None and cached[0] == raw_text:
        logger.info("desens_l1_reused", request_id=request_id)
        return cached[1]
    try:
        manager = get_three_layer_desensitization_manager()
        masked, layer_result = await manager.process_standardization_layer(
            {"clinical_text": raw_text}, session_id=session_id
        )
        masked_text = str(masked.get("clinical_text", raw_text))
        _desens_l1_cache[request_id] = (raw_text, masked_text)
        await _mark_desens_audit(request_id, "l1")
        logger.info(
            "desens_l1_applied",
            request_id=request_id,
            modified=bool(layer_result.get("modified")),
        )
        return masked_text
    except Exception as exc:
        logger.error(
            "desens_l1_failed_passthrough",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        _desens_l1_cache[request_id] = (raw_text, raw_text)
        await _mark_desens_audit(request_id, "l1", degraded=True)
        return raw_text


async def _desensitize_output_payload(
    *, request_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    """L2 融合层脱敏：对**即将持久化**的输出载荷再脱敏。

    目标是 LLM/融合层产出的内容（``findings``）—— 防止推理过程意外生成的身份
    信息（姓名/手机号/证件号）落库。

    失败安全：引擎异常时**原样返回**并 error 留痕（不阻断诊断，L3 仍在 SG 侧兜底）。
    """
    cached = _desens_l2_cache.get(request_id)
    if cached is not None and cached[0] == payload:
        logger.info("desens_l2_reused", request_id=request_id)
        return cached[1]
    try:
        manager = get_three_layer_desensitization_manager()
        masked, layer_result = await manager.process_fusion_layer(payload)
        _desens_l2_cache[request_id] = (payload, masked)
        await _mark_desens_audit(request_id, "l2")
        logger.info(
            "desens_l2_applied",
            request_id=request_id,
            modified=bool(layer_result.get("modified")),
        )
        return masked
    except Exception as exc:
        logger.error(
            "desens_l2_failed_passthrough",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        _desens_l2_cache[request_id] = (payload, payload)
        await _mark_desens_audit(request_id, "l2", degraded=True)
        return payload


async def _register_l3_existing_sg_check(
    *, request_id: str, payload: dict[str, Any], sg_result: dict[str, Any]
) -> None:
    """L3：把**既有** SG ``check_output`` 结果登记为第三层，**不重复调用 SG**。

    管线已自行调用 ``safety_gateway_client.check_output``（输入侧同意直查 +
    输出侧校验）。若改走 ``process_safety_gateway_layer``，会对同一请求**第二次**
    调用 SG —— 重复审计事件 + 额外延迟。
    """
    if request_id in _desens_l3_done:
        logger.info("desens_l3_reused", request_id=request_id)
        return
    try:
        manager = get_three_layer_desensitization_manager()
        manager.record_safety_gateway_existing_check(payload, sg_result)
        _desens_l3_done.add(request_id)
        await _mark_desens_audit(request_id, "l3")
        logger.info("desens_l3_registered", request_id=request_id)
    except Exception as exc:
        logger.error(
            "desens_l3_register_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


# MonitorAgent 的 R1（低置信度）/R2（性能降级）判据是 `status == "completed"`。
# 管线的终态除 completed 还有一个 `needs_review`（融合层已标记需复核）——两者
# 都是"已出结论"的终态，而 needs_review 恰恰是低置信度最常见的情形。若原样传入，
# R1/R2 会被静默跳过，因此这里把 needs_review 归一为 completed 再交给监控。
# 失败/超时/取消保持原值，以保证 R3（连续失败）的判定不被污染。
_MONITOR_TERMINAL_COMPLETED = ("completed", "needs_review")


def _get_monitor_agent():
    """取进程内的 MonitorAgent（lifespan 装配于 ``app.state.monitor_agent``）。"""
    try:
        from app.main import app as _app_instance

        return getattr(_app_instance.state, "monitor_agent", None)
    except Exception as exc:
        logger.warning(
            "pipeline_monitor_agent_unavailable",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


async def _safe_track_diagnosis(
    request_row: Any,
    *,
    request_id: str,
    status: str,
    processing_time_ms: int,
    confidence: float | None = None,
    sg_ok: bool | None = None,
    review_reason: str | None = None,
) -> None:
    """把一次诊断交给 :class:`MonitorAgent` 追踪（R1/R2/R3 + 延迟埋点）。

    ## 为什么需要这个接线（2026-09-28 接线审计）

    ``MonitorAgent.track_diagnosis`` 早已实现，但**没有任何生产调用方** ——
    R1（低置信度）/R2（性能降级）/R3（同器官连续失败）三条规则从未在诊断链路上
    运行过。`agents/monitor.py` 的单测只证明"规则本身正确"，证明不了"规则被调用"
    （本会话同类缺陷已出现多次：指标导出、告警回调、前端 WS、CRAG、三层脱敏）。

    ## 失败安全

    监控是**旁路**：任何异常（取不到代理、器官无法解析、代理内部报错）都只告警，
    **绝不影响诊断结论**与落库结果。

    ## 字段说明

    ``desensitization_status`` / ``consent_status_at_output`` 只为满足
    :class:`DiagnosisResult` 的必填契约（MonitorAgent 不读这两个字段），
    权威值由接口层 ``api/v1/diagnosis.py`` 从 ``sg_result`` 真实推导。
    """
    monitor = _get_monitor_agent()
    if monitor is None:
        logger.warning("monitor_agent_unavailable", request_id=request_id)
        return

    organ_raw = getattr(request_row, "organ", None)
    try:
        organ = organ_raw if isinstance(organ_raw, Organ) else Organ(organ_raw)
    except (TypeError, ValueError):
        logger.warning(
            "monitor_organ_unresolved",
            request_id=request_id,
            organ=str(organ_raw),
        )
        return

    # failed / timeout 与结果接口的 ai_diagnosis_failed 判据同源（见 73f5d1f），
    # 否则 MonitorAgent 的 R3 滑动窗口永远收不到失败。
    is_failure = status in ("failed", "timeout")
    monitor_status = "completed" if status in _MONITOR_TERMINAL_COMPLETED else status

    monitor_request = DiagnosisRequest(
        session_id=str(getattr(request_row, "session_id", "") or ""),
        image_ids=[],
        target_organ=organ,
        retry=bool(getattr(request_row, "retry", False)),
    )
    monitor_response = DiagnosisResult(
        request_id=request_id,
        status=monitor_status,
        desensitization_status=(
            DesensitizationStatus.DEGRADED if sg_ok is False else DesensitizationStatus.FULL
        ),
        consent_status_at_output=(
            ConsentStatus.DENIED if sg_ok is False else ConsentStatus.GRANTED
        ),
        fusion_status=FusionStatus.NORMAL,
        diagnosis={},
        quality_control=DiagnosisQualityControl(confidence_score=confidence),
        ai_diagnosis_failed=is_failure,
        requires_manual_review=status == "needs_review",
        review_reason=review_reason,
        processing_time_ms=processing_time_ms,
    )

    try:
        await monitor.track_diagnosis(monitor_request, monitor_response)
    except Exception as exc:
        logger.warning(
            "monitor_track_diagnosis_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


def _make_failed_response(reason: str) -> AgentResponse:
    """构造 dispatch 失败时的占位 AgentResponse。"""
    return AgentResponse(
        agent_name="pipeline_fallback",
        content=f"[pipeline] {reason}",
        confidence=0.0,
        reasoning_trace=[f"pipeline_dispatch_failed: {reason}"],
    )


def _make_failed_fusion_result(specialist_responses):
    """构造 fusion 失败时的占位 FusionResult（取 specialist 最高 confidence）。"""
    from app.fusion.protocols import FusionResult

    if specialist_responses:
        best = max(specialist_responses, key=lambda r: r.confidence)
    else:
        best = _make_failed_response("no_specialist_responded")

    return FusionResult(
        final_response=best,
        needs_review=True,
        review_reason="fusion_layer_failed",
    )


def _extract_findings(fusion_result) -> str:
    """从 fusion_result 抽取 findings 文本。

    实现委托给 ``app/services/sg_output_data.extract_findings_text``：
    该函数同时认**对象**（写路径）与 **dict**（读路径拿到的是落库后的 dict），
    避免两处各写一份抽取逻辑而漂移。
    """
    return extract_findings_text(fusion_result)


def _extract_recommendation(fusion_result) -> str | None:
    """从 fusion_result 抽取建议（简化为截断 content 前 500 字符）。"""
    content = extract_findings_text(fusion_result)
    if not content:
        return None
    return content[:500]


def _clinical_attributes_to_dict(response) -> dict:
    """从专科响应抽取 M1~M12 的结构化临床属性（仅含确实存在的字段）。

    取 ``final_response`` 而非任意候选：这些属性是"这个结论"的属性，必须与
    结论同源，否则会出现"结论来自 A、测量与概率来自 B"的错配。

    只写有值的字段——缺字段与"字段为空值"在语义上不同（前者是"未产出"，
    后者可能被误解为"计算结果是空"）。
    """
    if response is None:
        return {}

    attributes: dict = {}

    measurement = getattr(response, "measurement", None)
    if measurement is not None:
        attributes["measurement"] = (
            measurement.model_dump() if hasattr(measurement, "model_dump") else measurement
        )

    risk = getattr(response, "malignancy_risk", None)
    if risk is not None:
        attributes["malignancy_risk"] = (
            risk.model_dump() if hasattr(risk, "model_dump") else risk
        )

    posterior = getattr(response, "posterior_acoustic", None)
    if posterior is not None:
        attributes["posterior_acoustic"] = getattr(posterior, "value", posterior)

    for list_field in (
        "differential_diagnoses",
        "clinical_actions",
        "cross_modality_hints",
    ):
        value = getattr(response, list_field, None)
        if value:
            attributes[list_field] = [
                item.model_dump() if hasattr(item, "model_dump") else item for item in value
            ]

    for single_field in ("patient_explanation", "research_metrics"):
        value = getattr(response, single_field, None)
        if value is not None:
            attributes[single_field] = (
                value.model_dump() if hasattr(value, "model_dump") else value
            )

    return attributes


def _fusion_result_to_dict(fusion_result) -> dict:
    """把 FusionResult 转为可 JSON 序列化的 dict。"""
    if fusion_result is None:
        return {}
    final_response = getattr(fusion_result, "final_response", None)
    payload = {
        "needs_review": getattr(fusion_result, "needs_review", False),
        "review_reason": getattr(fusion_result, "review_reason", None),
        "self_rag_passed": getattr(fusion_result, "self_rag_passed", None),
        "crag_triggered": getattr(fusion_result, "crag_triggered", False),
        "crag_recovered": getattr(fusion_result, "crag_recovered", False),
        "total_latency_ms": getattr(fusion_result, "total_latency_ms", 0.0),
        "final_confidence": getattr(final_response, "confidence", 0.0) if final_response is not None else 0.0,
    }
    # 主结论文本必须进 findings（响应契约缺口，2026-10-01 T2c 实测）：
    # 此前 final_response.content（TI-RADS 分级+理由+证据）只经
    # recommendations 列落库，findings JSONB 与 API 响应的 diagnosis
    # 字段里都没有它——评测与前端拿不到结论文本。
    content = extract_findings_text(fusion_result)
    if content:
        payload["content"] = content
    clinical = _clinical_attributes_to_dict(final_response)
    if clinical:
        payload["clinical_attributes"] = clinical
    return payload


__all__ = [
    "run_diagnosis_pipeline",
]
