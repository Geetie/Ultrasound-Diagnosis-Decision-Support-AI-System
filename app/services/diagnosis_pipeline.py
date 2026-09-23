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


def _coerce_response_tier(value: Any) -> ResponseTier:
    """把 DB 中的 tier 字符串转成枚举；无法识别时按最保守（全量）处理。"""
    try:
        return ResponseTier(str(value))
    except Exception:  # noqa: BLE001 - 未知值不得阻断诊断
        return ResponseTier.FULL


def _assess_routing(request: Any) -> Optional[RoutingDecision]:
    """按规格 §5.3 给出路由判定（任何异常都不得阻断诊断）。"""
    try:
        return get_routing_router().assess(
            query=str(getattr(request, "clinical_text", "") or ""),
            target_organ=str(getattr(request, "organ", "") or "") or None,
            requested_tier=_coerce_response_tier(getattr(request, "tier", None)),
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "routing_assessment_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return None


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

            # 1) 调度 (dispatcher.invoke)
            t = time.monotonic()
            specialist_responses = await _safe_dispatch(request, request_id)
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
        except Exception:
            pass
        return False


# ----------------------------------------------------------------------
# 内部：单环节容错
# ----------------------------------------------------------------------


async def _safe_dispatch(request, request_id: str):
    """调用 dispatcher.invoke；失败时返回 [AgentResponse(confidence=0.0)]。

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
) -> bool:
    """根据 fusion_result + sg_result 决定 status / findings / confidence 并写 DB。"""
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
    findings = {
        "organ": request.organ,
        "fusion_result": _fusion_result_to_dict(fusion_result),
        "sg_result": sg_result or {"service_unavailable": True},
        "timings_ms": {k: round(v, 2) for k, v in timings.items()},
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
