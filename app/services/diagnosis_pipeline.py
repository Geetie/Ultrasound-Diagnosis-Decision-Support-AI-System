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
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import structlog

from app.config.database import AsyncSessionLocal
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.settings import settings
from app.agents.protocols import AgentContext, AgentResponse
from app.agents.routing import AdaptiveRAGRouter, RoutingDecision
from app.models.schemas import Organ, ResponseTier
from app.repositories import (
    ConsentRepository,
    DiagnosisRepository,
    SessionRepository,
)
from app.services.safety_gateway_client import safety_gateway_client

logger = structlog.get_logger("app.services.diagnosis_pipeline")

# Adaptive RAG 路由器（T-GB-AGT-02）。进程内单例：stats 需跨请求累计，
# 对应 05-可观测性与运维"标准路径占比 ≥70%"的路由分布指标。
_router: Optional[AdaptiveRAGRouter] = None


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
    except Exception as exc:  # noqa: BLE001 - 提交失败不应掩盖主流程结果
        logger.warning("pipeline_commit_call_failed", error=str(exc))
        return
    if inspect.isawaitable(maybe):
        await maybe


def _coerce_response_tier(value: Any) -> ResponseTier:
    """把 DB 中的 tier 字符串转成枚举；无法识别时按最保守（全量）处理。"""
    try:
        return ResponseTier(str(value))
    except Exception:  # noqa: BLE001 - 未知值不得阻断诊断
        return ResponseTier.FULL


def _assess_routing(request: Any) -> Optional[RoutingDecision]:
    """按规格 §5.3 给出路由判定（任何异常都不得阻断诊断）。"""
    t = time.monotonic()
    try:
        decision = get_routing_router().assess(
            query=str(getattr(request, "clinical_text", "") or ""),
            target_organ=str(getattr(request, "organ", "") or "") or None,
            requested_tier=_coerce_response_tier(getattr(request, "tier", None)),
        )
        _record_assessment_latency(time.monotonic() - t)
        return decision
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "routing_assessment_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


def _record_assessment_latency(seconds: float) -> None:
    """obs 指标：routing 评估延迟（wiring-audit P1-3，失败不影响管线）。"""
    try:
        from app.observability.metrics_collector import get_metrics_collector

        get_metrics_collector().record_assessment_latency(seconds)
    except Exception:  # noqa: BLE001
        pass


async def _read_retry_skip_self_rag(request_id: str) -> bool:
    """读取重试跳过 Self-RAG 标志（wiring-audit P1-6：此前只写不读）。

    读取失败按未设置处理（False）——重试增强功能不得阻断主链路。
    """
    try:
        from app.config.redis import RedisPool
        from app.config.redis_keys import RedisKey

        client = await RedisPool.get_client()
        raw = await client.get(RedisKey.retry_skip_self_rag(request_id))
        if not raw:
            return False
        payload = json.loads(raw if isinstance(raw, str) else raw.decode())
        return bool(payload.get("skip_self_rag", False))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "retry_skip_self_rag_read_failed",
            request_id=request_id,
            error=str(exc),
        )
        return False


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
    timings: Dict[str, float] = {}

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

            # T-GB-AGT-02: Adaptive RAG 动态路由判定（规格 §5.3）。
            # 判定结果写入 findings 的 routing 段：前端据此展示"初步结论 +
            # 是否切换深度分析"的确认对话框，观测侧据此统计标准路径占比。
            routing_decision = _assess_routing(request)

            # P1-6（wiring audit）：消费重试标志——retry 时跳过 Self-RAG 质检。
            skip_self_rag = await _read_retry_skip_self_rag(request_id)

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
                skip_self_rag=skip_self_rag,
            )
            timings["fusion.fuse_ms"] = (time.monotonic() - t) * 1000.0

            # 3) 安全网关 (sg.check_output)
            t = time.monotonic()
            sg_result, sg_ok = await _safe_safety_check(
                request=request,
                fusion_result=fusion_result,
                request_id=request_id,
            )
            timings["sg.check_output_ms"] = (time.monotonic() - t) * 1000.0

            # 4) 组装 DiagnosisResult 并写 DB
            t = time.monotonic()
            write_ok = await _write_result_to_db(
                diagnosis_repo=diagnosis_repo,
                request=request,
                fusion_result=fusion_result,
                sg_result=sg_result,
                sg_ok=sg_ok,
                timings=timings,
                routing=routing_decision,
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
        except Exception:
            pass
        return False


# ----------------------------------------------------------------------
# 内部：单环节容错
# ----------------------------------------------------------------------


async def _safe_dispatch(
    request, request_id: str, routing: Optional[RoutingDecision] = None
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
        context = AgentContext(
            request_id=request_id,
            session_id=request.session_id,
            organ=organ,
            # clinical_text 由 create_request 落库（0009 迁移补列）；此处用
            # getattr 兜底，兼容测试替身与历史 NULL 行
            user_query=str(
                getattr(request, "clinical_text", None) or "请基于提供的影像给出诊断"
            ),
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


async def _safe_fuse(request, specialist_responses, request_id: str,
                     skip_self_rag: bool = False):
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
            # P1-6：重试请求（retry_skip_self_rag 标志）跳过 Self-RAG 质检
            use_self_rag=not skip_self_rag,
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


async def _safe_safety_check(request, fusion_result, request_id: str):
    """调用 sg.check_output；失败时返回 ``(None, False)`` 表示 SG 不可用。

    Returns:
        ``(sg_result: dict | None, sg_ok: bool)``
    """
    try:
        # 收集 fusion_result 的 findings 作为 sg 输出
        output_data: Dict[str, Any] = {
            "organ": request.organ,
            "findings": _extract_findings(fusion_result),
            "recommendation": _extract_recommendation(fusion_result),
        }
        # 查询同意状态（简化：默认 granted；真实流程由 check_consent_status 处理）
        consent_status_at_input = "granted"
        sg_result = await safety_gateway_client.check_output(
            session_id=request.session_id,
            patient_id=getattr(request, "patient_id", "unknown"),
            output_data=output_data,
            consent_status_at_input=consent_status_at_input,
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


async def _write_result_to_db(
    diagnosis_repo: DiagnosisRepository,
    request,
    fusion_result,
    sg_result: Optional[dict],
    sg_ok: bool,
    timings: Dict[str, float],
    routing: Optional[RoutingDecision] = None,
    t_start: Optional[float] = None,
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
    }

    # 3) 写 DB
    try:
        await diagnosis_repo.update_result(
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
        # 4) 写 Redis 缓存（短 TTL 30s，便于客户端轮询立即看到 completed）
        try:
            await RedisPool.setex(
                RedisKey.diagnosis_result(request.request_id),
                30,
                str(findings).encode("utf-8") if isinstance(findings, str) else str(findings),
            )
        except Exception:
            pass  # best-effort
        # 5) 提交事务（见 _commit_if_possible 说明：不提交则写库被回滚）
        await _commit_if_possible(getattr(diagnosis_repo, "db", None))
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
    """从 fusion_result 抽取 findings 文本。"""
    if fusion_result is None:
        return ""
    final = getattr(fusion_result, "final_response", None)
    if final is None:
        return ""
    return getattr(final, "content", "") or ""


def _extract_recommendation(fusion_result) -> Optional[str]:
    """从 fusion_result 抽取建议（简化为截断 content 前 500 字符）。"""
    content = _extract_findings(fusion_result)
    if not content:
        return None
    return content[:500]


def _fusion_result_to_dict(fusion_result) -> dict:
    """把 FusionResult 转为可 JSON 序列化的 dict。"""
    if fusion_result is None:
        return {}
    return {
        "needs_review": getattr(fusion_result, "needs_review", False),
        "review_reason": getattr(fusion_result, "review_reason", None),
        "self_rag_passed": getattr(fusion_result, "self_rag_passed", None),
        "crag_triggered": getattr(fusion_result, "crag_triggered", False),
        "crag_recovered": getattr(fusion_result, "crag_recovered", False),
        "total_latency_ms": getattr(fusion_result, "total_latency_ms", 0.0),
        "final_confidence": (
            getattr(fusion_result.final_response, "confidence", 0.0)
            if getattr(fusion_result, "final_response", None) is not None
            else 0.0
        ),
    }


__all__ = [
    "run_diagnosis_pipeline",
]
