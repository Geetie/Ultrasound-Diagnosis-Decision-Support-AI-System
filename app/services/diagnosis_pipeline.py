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
import os
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

import structlog

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.routing import AdaptiveRAGRouter, RetrievalProfile, RoutingDecision
from app.agents.specialist.readable import pipeline_unavailable_text
from app.config.database import AsyncSessionLocal
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.settings import settings
from app.imaging.dicom_structure import (
    frame_indices,
    measurement_allowed,
    segmentation_allowed,
    structure_of,
)
from app.inference.nodule_segmentation import (
    NoduleSegmenter,
    SegmentationUnavailableError,
)
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
from app.observability.tracing import (
    SPAN_DIAGNOSIS_DISPATCH,
    SPAN_DIAGNOSIS_FUSION,
    SPAN_DIAGNOSIS_OUTPUT,
    DiagnosisSpan,
)
from app.repositories import (
    DiagnosisRepository,
)
from app.repositories.dicom_image_repository import DicomImageRepository
from app.security.three_layer_desensitization import (
    DesensitizationLayer,
    get_three_layer_desensitization_manager,
)
from app.services.degraded_texts import pipeline_failed_text
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
    t = time.monotonic()
    try:
        decision = get_routing_router().assess(
            query=str(getattr(request, "clinical_text", "") or ""),
            target_organ=str(getattr(request, "organ", "") or "") or None,
            requested_tier=_coerce_response_tier(getattr(request, "tier", None)),
        )
        _record_assessment_latency(time.monotonic() - t)
        return decision
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


def _record_assessment_latency(seconds: float) -> None:
    """obs 指标：routing 评估延迟（wiring-audit P1-3，失败不影响管线）。"""
    try:
        from app.observability.metrics_collector import get_metrics_collector

        get_metrics_collector().record_assessment_latency(seconds)
    except Exception as exc:
        logger.debug("assessment_latency_record_failed", error=str(exc))


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
    except Exception as exc:
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
    timings: dict[str, float] = {}
    # 预置 None：DB 读取本身也可能抛异常，而异常/取消处理分支需要判断
    # "是否已经读到请求行"（有行才谈得上把它交给监控追踪）。
    request = None
    # A.88 A2：既往诊断（读段取；取不到为 None，不编造）。
    prior_diagnosis: dict[str, Any] | None = None

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

            # A.88 A2：既往诊断必须在**读段**取（DB 仍打开时）。
            # 为什么只能在读段：跨 LLM 调用持有连接会被
            # idle_in_transaction_session_timeout 掐断（见下方 2026-09-30 钉子），
            # 而 fusion 在本次诊断的 LLM 调用之后才发生 —— 若等到那时再查，
            # 要么连接已断、要么又开一个会话把"读段已归还连接"的约束破坏掉。
            # 取不到时**不编造**：返回 None + 真实原因留痕（见函数内注释）。
            t = time.monotonic()
            prior_diagnosis = await _safe_load_prior_diagnosis(
                diagnosis_repo=diagnosis_repo,
                patient_id=patient_id,
                organ=getattr(request, "organ", None),
                request_id=request_id,
            )
            timings["db_prior_read_ms"] = (time.monotonic() - t) * 1000.0
        # ← DB session 在此关闭（T-Perf 实测钉子 2026-09-30）：
        # 原先 async with 包住整条管线，dispatcher/fusion 的分钟级 LLM
        # 调用期间事务空闲连接被 idle_in_transaction_session_timeout
        # 掐断，session 退出时隐式 rollback 抛 InterfaceError，整条诊断
        # 挂掉。读段（request/患者 ID/路由判定）结束后立即归还连接；
        # expire_on_commit=False 使 request 的已加载属性在分离后可读。
        # 写库走独立短会话（见 _write_result_to_db）。

        # P1-6（wiring audit）：消费重试标志——retry 时跳过 Self-RAG 质检。
        # （放在读段之后、调度之前：Redis 读取很快，不占 DB 连接。）
        skip_self_rag = await _read_retry_skip_self_rag(request_id)

        # A.51 降载：Self-RAG LLM 质检只在 FULL 档执行——FAST/COMPACT 档
        # 本就是「省 LLM 调用」的语义（T-GB-AGT-02），此前却统一跑全量校验
        # （实测 validate 含 LLM 1.85s P50）。降级后档位差异真正生效，
        # 且 FULL 档行为不变。
        from app.models.schemas import ResponseTier as _ResponseTier

        # A.58：原为 try/except AttributeError 兜 FULL —— 属性缺失会被**静默**吞掉
        # （表现成"意外降级却无人知"）。改 getattr 显式默认，语义不变但不再有静默分支
        # （零静默守卫的零容忍区要求：主链路 except 必须带证据）。
        _effective_tier = getattr(routing_decision, "effective_tier", _ResponseTier.FULL)
        use_self_rag = (not skip_self_rag) and (_effective_tier == _ResponseTier.FULL)

        # 1) 分割旁路**提前**（A.37 ③）：分割量化特征（bbox/面积/概率）必须在
        #    dispatch 之前产出，才能经 imaging_metadata 注入 LLM 上下文 ——
        #    原顺序 dispatch 在前、分割在后且默认关闭，影像信息永远赶不上
        #    prompt 拼装，LLM 只能反向索要影像特征（真机走查实测）。
        t = time.monotonic()
        segmentation = await _safe_nodule_contour(request)
        timings["segmentation.bypass_ms"] = (time.monotonic() - t) * 1000.0

        # 1b) 相似病例旁路（A.82）：影像向量**独立输出**，不进 LLM 的知识上下文。
        #     默认只查同一病人的历史影像（合规默认）；失败不影响主判读。
        t = time.monotonic()
        similar_cases = await _safe_similar_cases(request)
        timings["similar_cases.bypass_ms"] = (time.monotonic() - t) * 1000.0

        # 2) 调度 (dispatcher.invoke)
        # T-GB-AGT-02：把路由判定的检索档位一并注入，使"标准/全量"
        # 真正作用于专科智能体的检索强度（此前判定结果只落库不生效）。
        #
        # A.46 流式渐进渲染（AWS Bedrock 同步护栏模式的管线级映射）：
        # dispatcher 流式期间由 watcher 做段落级 SG —— LLM 累积文本按段落切分，
        # 新完成段落过 check_output 才进「安全预览」键（前端「分析中」显示）；
        # 段落被 SG 拒绝 → 写 refused 标志（前端停止显示），终态结论仍由
        # 步骤 3 的全文 SG 兜底 —— 双重保障，预览永远不越过安全网关。
        watcher_task: asyncio.Task | None = None
        if _STREAM_PREVIEW_ENABLED and patient_id is not None:
            watcher_task = asyncio.create_task(
                _stream_preview_watcher(
                    request_id=request_id,
                    session_id=getattr(request, "session_id", "") or "",
                    patient_id=patient_id,
                    organ=request.organ,
                    patient_context=getattr(request, "patient_context", None),
                )
            )
        t = time.monotonic()
        # A.88 T4-①（`retrieval-wiring` 交付的注入口，这里是**唯一需要的接线**）：
        # 影像检索腿此前**结构性不可达** —— `Retriever._resolve_image_leg` 要求
        # `request.image_data`，而三个生产调用点（`agents/knowledge.py` /
        # `fusion/consistency.py` / `fusion/crag.py`）都不传字节，于是"影像增强
        # 检索"这个开关在生产上等于没开。
        #
        # 作用域包住 **dispatch + fusion** 两段：检索发生在专科智能体内部
        # （`knowledge.query`）与融合层的指南比对 / CRAG 再检索里，都在这个区间内。
        #
        # 为什么必须 `async with`（不是调一次就算）：worker 协程会被复用
        # （`diagnosis_worker` 顺序消费多条请求），作用域漏退出会让**下一次判读**
        # 读到**上一次的影像字节** —— 跨病人串号是 A.78 红线。`async with` 由解释器
        # 保证退出，比手写 try/finally 少一个出错点。
        #
        # 失败语义：本作用域**永不抛异常**（`attach_image_leg_for_request` 把任何
        # 失败归到 `skip_reason` 并留痕），所以它不会成为主链路的新失败点；
        # 取不到字节时检索侧照常工作，并把真实原因（能力不可用 / 没送字节 /
        # 解码失败 …）写进 `RetrievalResult.image_leg_skip_reason`。
        from app.rag.image_leg import image_leg_scope_for_request

        async with image_leg_scope_for_request(request) as _image_leg:
            timings["image_leg.attach_ms"] = (time.monotonic() - t) * 1000.0
            if _image_leg is not None and getattr(_image_leg, "skip_reason", None):
                logger.info(
                    "pipeline_image_leg_skipped",
                    request_id=request_id,
                    reason=_image_leg.skip_reason,
                )
            # A.47 业务 span（Langfuse trace 树）：attributes 全部经 PhiMaskingExporter
            # 导出前脱敏 —— 正文文本进 trace 的唯一入口在这里与 output span。
            t = time.monotonic()
            async with DiagnosisSpan(
                SPAN_DIAGNOSIS_DISPATCH,
                attributes={
                    "organ": request.organ,
                    "tier": getattr(routing_decision, "effective_tier", None)
                    and routing_decision.effective_tier.value,
                    "image_count": len(getattr(request, "image_ids", []) or []),
                },
            ):
                specialist_responses = await _safe_dispatch(
                    request,
                    request_id,
                    routing=routing_decision,
                    imaging_metadata=segmentation if isinstance(segmentation, dict) else None,
                )
            timings["dispatcher.invoke_ms"] = (time.monotonic() - t) * 1000.0
            if watcher_task is not None:
                watcher_task.cancel()

            # 2) 融合 (fusion.fuse)
            t = time.monotonic()
            async with DiagnosisSpan(
                SPAN_DIAGNOSIS_FUSION,
                attributes={"skip_self_rag": skip_self_rag, "self_rag_enabled": use_self_rag},
            ):
                fusion_result = await _safe_fuse(
                    request=request,
                    specialist_responses=specialist_responses,
                    request_id=request_id,
                    skip_self_rag=skip_self_rag,
                    use_self_rag=use_self_rag,
                    # A.88 A2：既往诊断真正接到融合层（此前恒 None →
                    # consistency.py 的"与既往比"分支生产不可达）。
                    prior_diagnosis=prior_diagnosis,
                )
        timings["fusion.fuse_ms"] = (time.monotonic() - t) * 1000.0
        # A.38 计时拆分：fuse 内部含 LLM 校验（validate/Self-RAG 与 CRAG），
        # 14s 波动此前不可归因 —— 从 FusionResult 抄出子计时一起落库。
        # 注意 validate_ms/crag_ms 是 fuse 内部掐的表，不含编排开销。
        timings["fusion.validate_ms"] = float(getattr(fusion_result, "validate_ms", 0.0))
        timings["fusion.crag_ms"] = float(getattr(fusion_result, "crag_ms", 0.0))
        # A.51 降载可观测性：非 FULL 档跳过 Self-RAG / validate 超预算降级
        timings["fusion.self_rag_skipped"] = not use_self_rag
        timings["fusion.validate_degraded"] = bool(
            getattr(fusion_result, "validate_degraded", False)
        )

        # 2b) T2a 影像分类旁路（A.88 T5-1）：**只作辅助参考**，不改结论/置信度/状态。
        #     位置说明：放在融合之后 —— 它要的是"本次影像"而不是"本次结论"，
        #     放在 dispatch 之前会与分割旁路抢同一次磁盘读；放在融合之后则
        #     完全处在 LLM 关键路径之外，默认关时零开销。
        t = time.monotonic()
        t2a_classification = await _safe_t2a_classification(request)
        timings["t2a_classification.bypass_ms"] = (time.monotonic() - t) * 1000.0

        # 2c) 幻觉检测（A.88 T5-3）：对本次**最终响应**做一次检测，把真实的
        #     严重度写进 findings.fusion_result。此前 hallucination_severity
        #     全仓无生产者 → 结果接口恒 None（"无生产者不得填值"的诚实做法）。
        #     成本与档位：只在 FULL 档或开关显式打开时执行（检测是纯正则/词表，
        #     但仍要遍历 5 类策略 + 一次 CUSUM 上报，不该在 FAST/COMPACT 档白跑）。
        t = time.monotonic()
        hallucination = _detect_hallucination_safely(
            fusion_result,
            organ=getattr(request, "organ", None),
            request_id=request_id,
            force_enabled=bool(
                getattr(settings, "hallucination_detection_in_pipeline", False)
            ),
            is_full_tier=(_effective_tier == _ResponseTier.FULL),
        )
        timings["hallucination.detect_ms"] = (time.monotonic() - t) * 1000.0

        # 3) 安全网关 (sg.check_output) —— output span 带 gen_ai.* 语义属性，
        # Langfuse 按 OTel GenAI 约定渲染为 LLM Generation（A.47）
        t = time.monotonic()
        async with DiagnosisSpan(
            SPAN_DIAGNOSIS_OUTPUT,
            attributes={
                "gen_ai.system": "vllm",
                "gen_ai.request.model": "Qwen3-8B",
                "gen_ai.completion": extract_findings_text(fusion_result)[:800],
            },
        ):
            sg_result, sg_ok = await _safe_safety_check(
                request=request,
                fusion_result=fusion_result,
                request_id=request_id,
                patient_id=patient_id,
            )
        timings["sg.check_output_ms"] = (time.monotonic() - t) * 1000.0

        # 4) 组装 DiagnosisResult 并写 DB（独立短会话，见 _write_result_to_db）
        # 4a) A.88 T5-4：三层脱敏的**单一汇总入口**（此前 process_full_pipeline
        #     仅测试调用 → "三层合并后的 all_passed"在生产上恒缺）。
        #     三层动作已在各自时刻完成（L1 在 dispatch 前、L2 在写库前、
        #     L3 在 SG 之后），此处只汇总判定。
        t = time.monotonic()
        desensitization = await _run_desens_full_pipeline(
            request_id=request_id, patient_id=patient_id
        )
        timings["desensitization.aggregate_ms"] = (time.monotonic() - t) * 1000.0

        t = time.monotonic()
        write_ok = await _write_result_to_db(
            request=request,
            fusion_result=fusion_result,
            sg_result=sg_result,
            sg_ok=sg_ok,
            timings=timings,
            routing=routing_decision,
            rollout=rollout_decision,
            segmentation=segmentation,
            similar_cases=similar_cases,
            t2a_classification=t2a_classification,
            hallucination=hallucination,
            desensitization=desensitization,
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
                    # A.73：**失败也必须留下可读正文**。历史上有 18 条只写了
                    # `{"error": ...}`，`_conclusion_of()` 取不到 content → 报告是空白，
                    # 医生看到"什么都没有"却不知道为什么（比"说一句实话"更糟）。
                    findings={
                        "fusion_result": {
                            "content": pipeline_failed_text(type(exc).__name__),
                            "degraded": True,
                            "error_type": type(exc).__name__,
                        },
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    },
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
    request,
    request_id: str,
    routing: RoutingDecision | None = None,
    imaging_metadata: dict | None = None,
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
        # A.36 ⑤：PatientContext 的 age/gender/relevant_history 此前只在安全网关
        # 消费（且仅 conditions 被转发），从不进入 LLM —— 结论恒为「信息不足」。
        # A.37 ③补充：显式告知模型"影像已被系统测量"，否则 thyroid.md 把
        # 成分/回声/形态列为预期输入、模型收不到就反向索要（真机走查实测）。
        if imaging_metadata and imaging_metadata.get("available"):
            im = imaging_metadata
            bbox = im.get("bbox")
            # A.51 多帧分期③：把帧归属一并交给模型，结论可写「第 X/Y 帧」
            # （多帧栈的分割来自择优帧，医生需要据此回看核对）。
            _tf = im.get("total_frames")
            _fi = im.get("frame_index")
            frame_note = (
                f"分割取自第 {_fi + 1}/{_tf} 帧"
                f"（{'多帧候选择优' if im.get('selection_policy') == 'best_mean_prob' else '中间帧'}），"
                if isinstance(_tf, int) and _tf > 1 and isinstance(_fi, int)
                else ""
            )
            user_query = user_query + (
                "\n【系统自动影像分析（已由分割模型测得，直接引用，不要向医生索要）】"
                f"病灶 bbox(像素)={bbox}，面积={im.get('area_mm2')}mm²，"
                f"分割置信度 max={im.get('max_prob')}/mean={im.get('mean_prob')}，"
                f"像素间距={im.get('pixel_spacing_mm')}mm/px。{frame_note}"
                "结论中请引用这些量化数据；成分/回声等定性描述如无法从量化数据得出，"
                "请在结论中标注『需医生镜下确认』而不是要求医生提供。"
            )
        pc = getattr(request, "patient_context", None)
        if pc is not None:
            parts = []
            if getattr(pc, "age", None):
                parts.append(f"{pc.age} 岁")
            if getattr(pc, "gender", None):
                parts.append(str(pc.gender))
            if getattr(pc, "relevant_history", None):
                parts.append(f"病史：{pc.relevant_history}")
            if parts:
                user_query = user_query + "\n患者信息：" + "；".join(parts)
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
            # A.37 ③：分割量化特征注入 LLM 上下文（base.py「影像元数据」段
            # 早已实现，此前恒传 None —— 影像从未进入判读）。
            imaging_metadata=imaging_metadata or None,
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
                     skip_self_rag: bool = False, use_self_rag: bool | None = None,
                     prior_diagnosis: dict | None = None):
    """调用 fusion.fuse；失败时返回带 needs_review 的占位 FusionResult。

    Args:
        prior_diagnosis: 既往诊断（A.88 A2）。形如
            ``{"organ": "thyroid", "grade": "TR3", "confidence": 0.85}``；
            ``None`` 表示**确实没有可比对的既往结论**（原因由调用方留痕，
            本函数不猜测、不编造默认值）。非 None 时才会落到
            ``FusionRequest.prior_diagnosis``，进而使
            ``ConsistencyChecker`` 的"与既往诊断对比"分支真正执行
            （``app/fusion/consistency.py:604-631``）。
    """
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
            # A.51：use_self_rag 由调用方按档位/重试标志算好（非 FULL 档跳过
            # Self-RAG LLM）；未传时回退到 P1-6 的旧语义（重试即跳过）。
            use_self_rag=(not skip_self_rag) if use_self_rag is None else use_self_rag,
            # A.88 A2：既往诊断（此前从不传 → "与既往比"生产不可达）。
            prior_diagnosis=prior_diagnosis,
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


#: A.88 A2：往回翻多少条历史结论去找"可解析分级/置信度"的那一条。
#: 不是 1 —— 最近一条完全可能没有分级（真机历史库里有 451 条无结论、66 条
#: 输出缺分级，见 AGENTS.md「数据质量事故归档」），只翻 1 条会把"有既往但
#: 正文没分级"误报成"没有既往"。翻到就停，不会扫库。
_PRIOR_LOOKBACK_LIMIT = 5


async def _safe_load_prior_diagnosis(
    *,
    diagnosis_repo: Any,
    patient_id: str | None,
    organ: str | None,
    request_id: str,
) -> dict | None:
    """读段内取"同一患者 + 同器官 + 已终态 + 非本次"的最近一次结论（A.88 A2）。

    返回值直接交给 ``FusionRequest.prior_diagnosis``，格式见
    ``app/fusion/consistency.py:688-695``::

        {"organ": "thyroid", "grade": "tr3", "confidence": 0.85}

    ## 为什么必须真接线（而不是记成"待接线"）

    ``ConsistencyChecker.check`` 的"与既往比"分支以
    ``request.prior_diagnosis is not None`` 为唯一前提
    （``app/fusion/consistency.py:623``）；管线此前组 ``FusionRequest`` 时
    **从不传**该字段 → 这一层校验在生产上从未执行过，而对外契约却一直写着
    ``consistency_flag=consistent``（A3 同批修）。

    ## 取不到时必须说清**是哪一种**取不到

    每个失败分支留自己的 ``reason``，聚合处取真实原因 —— **不许统一写死一个
    默认值**（AGENTS.md 硬规则 8 的血泪：``best is None`` 一律报
    ``image_bytes_unavailable``，把"本来不该勾画"说成"系统取不到影像"）。
    取值：``no_patient_id`` / ``no_organ`` / ``no_previous`` /
    ``unparsable_grade`` / ``no_confidence`` / ``lookup_failed``。

    分级抽取复用单一事实源 :func:`app.inference.grading_definitions.extract_grading_severest`
    （**不新写正则**）；正文抽取复用
    :func:`app.services.sg_output_data.extract_findings_text`（认对象也认 dict）。
    """
    if not patient_id:
        logger.info(
            "pipeline_prior_diagnosis_absent",
            request_id=request_id,
            reason="no_patient_id",
        )
        return None
    if not organ:
        logger.info(
            "pipeline_prior_diagnosis_absent",
            request_id=request_id,
            reason="no_organ",
        )
        return None

    # 函数内 import：与 _resolve_patient_id 同样的延迟导入风格，避免模块级
    # 依赖 app.inference 的加载顺序（该模块会拉 lxml/regex 等重依赖）。
    from app.inference.grading_definitions import extract_grading_severest
    from app.services.sg_output_data import extract_findings_text

    try:
        rows = await diagnosis_repo.get_previous_result_same_organ(
            patient_id=patient_id,
            organ=organ,
            exclude_request_id=request_id,
            limit=_PRIOR_LOOKBACK_LIMIT,
        )
    except Exception as exc:
        logger.warning(
            "pipeline_prior_diagnosis_lookup_failed",
            request_id=request_id,
            patient_id=patient_id,
            organ=organ,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        logger.info(
            "pipeline_prior_diagnosis_absent",
            request_id=request_id,
            reason="lookup_failed",
        )
        return None

    if not rows:
        logger.info(
            "pipeline_prior_diagnosis_absent",
            request_id=request_id,
            reason="no_previous",
        )
        return None

    # 往回翻：优先用"有分级"的那条；都没有分级时，退而用"有置信度"的那条
    # （confidence 单信号也能比，见 consistency.py 的 gap 判定）。
    best_any: dict | None = None
    scanned = 0
    for row in rows:
        scanned += 1
        findings = getattr(row, "findings", None)
        payload = findings if isinstance(findings, dict) else {}
        text = extract_findings_text(payload.get("fusion_result"))
        grade = extract_grading_severest(text, organ) if text else None
        confidence = getattr(row, "confidence", None)
        candidate = {
            "organ": organ,
            "grade": grade,
            "confidence": float(confidence) if confidence is not None else None,
            # 只作留痕/追溯用：consistency 只读 organ/grade/confidence，
            # 多带的键不会改变判定（且落库时会被 _fusion_result_to_dict 之外的
            # 路径忽略）。
            "source_request_id": getattr(row, "request_id", None),
        }
        if grade:
            logger.info(
                "pipeline_prior_diagnosis_loaded",
                request_id=request_id,
                prior_request_id=candidate["source_request_id"],
                grade=grade,
                confidence=candidate["confidence"],
                scanned=scanned,
            )
            return candidate
        if best_any is None and candidate["confidence"] is not None:
            best_any = candidate

    if best_any is not None:
        # 有既往行、但正文里抽不出分级 → 只用置信度比（**不假装有分级**）。
        logger.info(
            "pipeline_prior_diagnosis_loaded",
            request_id=request_id,
            prior_request_id=best_any["source_request_id"],
            grade=None,
            confidence=best_any["confidence"],
            scanned=scanned,
            note="grade_unparsable_using_confidence_only",
        )
        return best_any

    logger.info(
        "pipeline_prior_diagnosis_absent",
        request_id=request_id,
        reason="unparsable_grade",
        scanned=scanned,
    )
    return None


# ── A.46 流式渐进渲染：段落级 SG watcher ────────────────────────────────
# 业界调研（2026-10-10）：AWS Bedrock Guardrails 同步模式（chunk 缓冲→逐段
# 扫描→通过才发，**支持脱敏**）vs 异步模式（立即发，**不支持遮蔽**，官方
# WARNING）；本项目 SG 含 PHI 脱敏 → 必须同步模式。NeMo Guardrails 同构：
# chunk_size（累积送检）+ context_size（滑窗防边界误判）。

_STREAM_PREVIEW_POLL_S = 1.0
_STREAM_PREVIEW_REFUSED_KEY = "diag_preview_refused"


async def _stream_preview_watcher(
    request_id: str,
    session_id: str,
    patient_id: str,
    organ: str,
    patient_context: object | None,
) -> None:
    """dispatcher 流式期间的段落级 SG 检查循环（AWS 同步护栏模式的管线级实现）。

    每 1s 读 inference 层写的原始预览键 → 按 ``\\n\\n`` 切段落 → 新完成段落
    过 ``check_output``（带管线上下文）→ 通过则追加进「安全预览」键；
    段落被拒 → 写 refused 标志并结束（预览停止，终态由全文 SG 兜底）。
    全程 best-effort：任何异常只影响预览，不影响判读本体。
    """
    from app.inference.base import (
        read_generation_raw_preview,
        read_generation_safe_preview,
        write_generation_preview_refused,
        write_generation_safe_preview,
    )

    seen = 0  # 已处理完成的段落数
    while True:
        await asyncio.sleep(_STREAM_PREVIEW_POLL_S)
        try:
            raw = await read_generation_raw_preview(request_id)
            if not raw:
                continue
            paras = raw.split("\n\n")
            complete = paras[:-1]  # 最后一段可能仍在生成，不算完成
            if len(complete) <= seen:
                continue
            for para in complete[seen:]:
                text = para.strip()
                if not text:
                    continue
                output_data = build_sg_output_data(
                    organ=organ,
                    findings=text,
                    patient_context=patient_context,
                )
                consent_status = await safety_gateway_client.get_consent_status(patient_id)
                if consent_status is None:
                    # 查不到同意记录 → 与全文 SG 同口径 fail-closed：拒绝该段
                    await write_generation_preview_refused(request_id)
                    logger.warning(
                        "stream_preview_refused_consent_unavailable",
                        request_id=request_id,
                    )
                    return
                sg = await safety_gateway_client.check_output(
                    session_id=session_id,
                    patient_id=patient_id,
                    output_data=output_data,
                    consent_status_at_input=consent_status,
                )
                if not sg.get("passed", sg.get("allowed", False)):
                    await write_generation_preview_refused(request_id)
                    logger.warning(
                        "stream_preview_refused",
                        request_id=request_id,
                        reason=str(sg.get("message", ""))[:80],
                    )
                    return
                # 通过：追加进安全预览键（Tail 800 字防爆体）
                safe_now = await read_generation_safe_preview(request_id)
                merged = (safe_now + "\n\n" + text).strip() if safe_now else text
                await write_generation_safe_preview(request_id, merged[-800:])
            seen = len(complete)
        except asyncio.CancelledError:  # silent-ok: 巡检任务被取消（停机/回收路径），非失败
            return
        except Exception as exc:
            logger.warning(
                "stream_preview_watcher_error", request_id=request_id, error=str(exc)[:120]
            )


_STREAM_PREVIEW_ENABLED = os.environ.get("UAI_LLM_STREAM_PROGRESS", "") == "1"


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
    segmentation: dict[str, Any] | None = None,
    similar_cases: dict[str, Any] | None = None,
    t2a_classification: dict[str, Any] | None = None,
    hallucination: dict[str, Any] | None = None,
    desensitization: dict[str, Any] | None = None,
    t_start: float | None = None,
) -> bool:
    """根据 fusion_result + sg_result 决定 status / findings / confidence 并写 DB。

    Args:
        segmentation: 分割旁路结果（**总是写入**，含 available=False 的情形）。
        t2a_classification: T2a 分类旁路结果（A.88 T5-1）。**只作辅助参考**，
            不参与 status/confidence 的判定（见 :func:`_safe_t2a_classification`）。
        hallucination: 幻觉检测结果（A.88 T5-3）。``severity`` 会写进
            ``findings.fusion_result.hallucination_severity``（与 schema 字段同名，
            供结果接口直接读取）；``checked``/``reason`` 用于区分"评估过且干净"
            与"压根没评估"。
        desensitization: 三层脱敏的**汇总判定**（A.88 T5-4，由
            :func:`_run_desens_full_pipeline` 经 ``process_full_pipeline``
            算出）。``None`` 时不写该键（不冒充"已汇总"）。
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
        "fusion_result": _fusion_result_to_dict(fusion_result),        "sg_result": sg_result or {"service_unavailable": True},
        "timings_ms": timings_ms,
        # A.52 口径修复：报告/结果声明的**知识库版本**必须是本次判读真正检索的
        # 那个库。此前 findings 不写该键，读路径（api/v1/diagnosis.py）只能退回
        # Phase 0A 占位常量 `phase0a_mock_kb_version`(=kb_v1.0.0)，于是出现
        # 「证据行是 kb_v1.3.0:… 而报告标注知识库版本 kb_v1.0.0」的自相矛盾。
        # 单一事实源 = settings.rag_kb_version（生产启动脚本由 .env 注入）。
        "knowledge_base_version": settings.rag_kb_version,
        # A.52 版本溯源：报告/结果里「模型版本」写**本次运行的真实值**——
        # ①model_version = 诊断引擎版本（参与「模型↔知识库」兼容矩阵，可解析 vN.M）
        # ②llm_model = 实际产出结论的生成模型标识（值来自 vLLM 配置，可对线上核对）
        # 二者都取自运行期 settings，不写任何 Phase 0A 占位常量。
        "model_version": settings.resolved_model_version,
        "llm_model": settings.resolved_llm_model,
        "review_reason": review_reason,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        # T-GB-AGT-02 路由判定（前端确认对话框与路由分布观测的依据）
        "routing": routing.as_dict() if routing is not None else None,
        # T-GR-INT-01：灰度判定落库，观测侧据此统计各轨道放量比例
        "rollout": rollout.as_dict() if rollout is not None else None,
        # 分割旁路结果（轮廓/面积/概率）。**总是写入**（含 available=False
        # 的情形）——报告层据此显示"未做分割"与原因，而不是字段整体消失，
        # 那会让医生以为"没 nodules"或"系统没这项能力"。
        "segmentation": segmentation
        if segmentation is not None
        else {"available": False, "reason": "not_run"},
        # A.82 相似病例：**独立字段**，不混进 knowledge_ids / 不进 LLM 上下文
        "similar_cases": similar_cases
        if similar_cases is not None
        else {"status": "not_run", "cases": []},
        # A.88 T5-1：T2a 影像分类（**只作辅助参考**，不改结论/置信度/状态）。
        # 总是写入（含 available=False）——报告层要能显示"未做分类"与**真实原因**，
        # 而不是字段消失（那会被读成"没有病灶"）。
        "t2a_classification": t2a_classification
        if t2a_classification is not None
        else {"available": False, "reason": "not_run", "skipped": True},
    }

    # A.88 T5-3：幻觉检测的真实结果写进 fusion_result（键名与 schema 字段同名，
    # 结果接口可直接取）。`checked=False` 时写 None + 真实原因，**不写 False**
    # —— "没评估"与"评估过且干净"必须可区分。
    if hallucination is not None:
        fusion_payload = findings["fusion_result"]
        if isinstance(fusion_payload, dict):
            fusion_payload["hallucination_checked"] = bool(
                hallucination.get("checked", False)
            )
            fusion_payload["hallucination_severity"] = hallucination.get("severity")
            if hallucination.get("reason"):
                fusion_payload["hallucination_reason"] = str(hallucination["reason"])
            if hallucination.get("checked"):
                # 只有真跑过才写这些细节（否则等于给"未评估"编造证据）
                for key in (
                    "is_hallucination",
                    "issue_count",
                    "confidence",
                    "issues",
                ):
                    if key in hallucination:
                        fusion_payload[f"hallucination_{key}"] = hallucination[key]

    # 三层脱敏 L2（融合层）：对**即将持久化**的输出载荷再脱敏一次。
    # 位置在"组装完成、写库之前"：这是 PII 真正落库的最后一个关口，
    # 也是"LLM 意外生成身份信息"的拦截点。
    findings = await _desensitize_output_payload(
        request_id=request.request_id, payload=findings
    )

    # A.88 T5-4：三层脱敏的**单一汇总入口**。
    # 三层动作仍发生在各自的时刻（语义不变），这里只把本次运行的层台账交给
    # `process_full_pipeline(recorded_stages=...)` 产出唯一判定 —— 该判定
    # 此前只存在于 process_full_pipeline 内部，而它没被生产调用 → 生产上
    # 压根没有"三层合并后的 all_passed"这个结论。
    # `desensitization` 由调用方（它才知道真实 patient_id）算好后传入；
    # 未传则整体不写该键（不编一个 "unknown" 患者 ID 去冒充汇总结果）。
    if desensitization is not None:
        findings["desensitization"] = desensitization

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
#: A.88 T5-4：本次运行**已生效**的各层结果台账（层 → layer_result）。
#: 三层在时间上分离（LLM 之前 / 落库之前 / SG 之后），无法在同一时刻串行执行；
#: 收口的正确做法是"各自就地执行 + 登记台账"，运行结束由
#: ``process_full_pipeline(recorded_stages=...)`` 产出**唯一**的合规汇总判定。
#: 此前该汇总只存在于 ``process_full_pipeline`` 内部，而它没被生产调用 →
#: 生产上压根没有"三层合并后的 all_passed"这个结论。
_desens_stage_results: dict[str, dict[Any, dict[str, Any]]] = {}

DESENS_AUDIT_TTL_SECONDS = 3600


def _clear_desens_run(request_id: str) -> None:
    """运行结束清缓存（长跑进程里避免无界增长）。"""
    _desens_l1_cache.pop(request_id, None)
    _desens_l2_cache.pop(request_id, None)
    _desens_l3_done.discard(request_id)
    _desens_stage_results.pop(request_id, None)


def _record_desens_stage(request_id: str, layer: Any, layer_result: dict[str, Any]) -> None:
    """把某层的**真实** ``layer_result`` 登记进本次运行台账（A.88 T5-4）。"""
    _desens_stage_results.setdefault(request_id, {})[layer] = dict(layer_result or {})


async def _run_desens_full_pipeline(
    *, request_id: str, patient_id: str | None
) -> dict[str, Any]:
    """把本次运行的三层结果交给 ``process_full_pipeline`` 汇总（A.88 T5-4）。

    ## 为什么不是"把三次调用替换成一次"

    三层发生在三个不同时刻，硬塞进一个时刻等于**二次脱敏**（对同一份载荷
    再跑一次会破坏语义，见上方 L1/L2 缓存注释）。所以收口收的是**汇总判定**，
    不是执行时刻。

    ## 产出

    ``findings["desensitization"]``：三层合并后的
    ``all_passed`` / ``security_check_failed`` / ``degraded_mode`` +
    参与汇总的层与各自 ``modified``/``degraded``。这是此前生产上**不存在**的
    结论（``process_full_pipeline`` 仅测试调用）。

    失败安全：汇总本身异常 → ``status="unavailable"`` + 真实原因；
    **绝不影响**主判读（三层各自的脱敏动作已经真实发生了，汇总只是留痕）。
    """
    stages = _desens_stage_results.get(request_id) or {}
    if not stages:
        return {"status": "skipped", "reason": "no_stage_results"}

    t0 = time.monotonic()
    try:
        manager = get_three_layer_desensitization_manager()
        result = await manager.process_full_pipeline(
            patient_id=str(patient_id or "unknown"),
            input_data={},
            llm_output_data=None,
            recorded_stages=stages,
        )
    except Exception as exc:
        logger.warning(
            "desens_full_pipeline_failed",
            request_id=request_id,
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return {
            "status": "unavailable",
            "reason": f"{type(exc).__name__}: {exc}"[:200],
        }

    return {
        "status": "ok",
        "all_passed": bool(result.all_passed),
        "security_check_failed": bool(result.security_check_failed),
        "degraded_mode": bool(result.degraded_mode),
        "layers": {
            str(getattr(layer, "value", layer)): {
                "modified": bool(layer_result.get("modified", False)),
                "degraded": bool(layer_result.get("degraded", False)),
            }
            for layer, layer_result in result.layer_results.items()
        },
        "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 2),
    }


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
        # A.88 T5-4：把本层**真实**结果登记进运行台账（供运行末统一汇总）
        _record_desens_stage(
            request_id, DesensitizationLayer.STANDARDIZATION, layer_result
        )
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
        # A.88 T5-4：登记本层真实结果（供运行末统一汇总）
        _record_desens_stage(request_id, DesensitizationLayer.FUSION, layer_result)
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
        layer_result = manager.record_safety_gateway_existing_check(payload, sg_result)
        _desens_l3_done.add(request_id)
        # A.88 T5-4：登记本层真实结果（供运行末统一汇总）
        _record_desens_stage(
            request_id, DesensitizationLayer.SAFETY_GATEWAY, layer_result
        )
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


#: 分割"未做"原因 → 医生可读中文（A.86）。
#: 每条都要**可行动**：要么说明"不需要做"，要么说明"该找谁"。
SEGMENTATION_REASON_TEXT: dict[str, str] = {
    "secondary_capture_burned_in": (
        "本次未做病灶勾画：该影像是屏幕截图（带烧录标注/标尺），"
        "系统不对截图做自动勾画；如需要请上传设备原始 DICOM"
    ),
    "no_frames": "本次未做病灶勾画：影像不含有效帧，请重新上传含图像的 DICOM",
    "image_bytes_unavailable": (
        "本次未做病灶勾画：系统未取到影像文件，请联系技师重新上传"
    ),
    "no_candidate": "本次未做病灶勾画：没有可用影像，请确认本次检查是否已上传影像",
    "image_not_indexed": (
        "本次未做病灶勾画：影像未登记到本次检查，请确认上传是否成功"
    ),
}


def segmentation_reason_text(reason: str | None) -> str:
    """"未做分割"原因的中文说明。纯函数（守卫钉住）。

    未知原因不臆造解释，统一回退到"未做病灶勾画（原因：<code>）"，
    保证前端永远有可显示的字符串，而不会因为查不到映射显示空白。
    """
    code = str(reason or "").strip()
    if not code:
        return "本次未做病灶勾画"
    return SEGMENTATION_REASON_TEXT.get(code, f"本次未做病灶勾画（原因：{code}）")


def _structure_of_content(content: bytes) -> dict[str, Any]:
    """读 DICOM 头部得到结构要素；**解析失败按"允许"处理但留痕**。

    为什么不 fail-closed：解析失败可能只是 header 不完整（如导出工具生成的次要文件），
    直接拒绝会让原本能出报告的案件变成静默跳过。真正的安全问题（无标定换算 mm）
    由 `_pixel_spacing_from_dicom` 单独把住。
    """
    import io

    try:
        import pydicom

        ds = pydicom.dcmread(io.BytesIO(content), stop_before_pixels=True, force=True)
    except Exception as exc:
        logger.warning(
            "dicom_structure_parse_failed",
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return {"sop_kind": "unknown", "frames": 1, "burned_in": False, "calibration": "unknown"}
    return structure_of(ds)


def _pixel_spacing_from_dicom(content: bytes) -> list[float] | None:
    """从 DICOM 字节读 PixelSpacing（行距, 列距，单位 mm）。

    语义：**缺 PixelSpacing 就不换算 mm²**（返回 None，报告只报像素面积）。
    绝不拿默认间距猜物理尺寸——医生会把那个数字当真实病灶大小。

    容错要点：pydicom 的 DS 可能是 str / MultiValue / None；任何异常都不
    换算，但**必须留痕**（静默 return None 会让"单位缺失"变成查不到原因）。
    """
    import io

    try:
        import pydicom

        ds = pydicom.dcmread(io.BytesIO(content), stop_before_pixels=True)
    except Exception as exc:
        logger.debug("nodule_pixel_spacing_parse_failed", error=str(exc),
                     error_type=type(exc).__name__)
        return None

    # A.81 安全闸：**没有超声区域校准就不换算物理单位**。
    # A.80 实测本库 580 个文件 `SequenceOfUltrasoundRegions` 全为 0——
    # 也就是说 PixelSpacing 里的值**没有区域校准支撑**，乘出来的 mm² 是"看起来准的假数"。
    # 宁可只报像素面积，也不给医生一个无法辩护的物理尺寸。
    structure = structure_of(ds)
    if not measurement_allowed(str(structure.get("calibration") or "")):
        logger.warning(
            "nodule_pixel_spacing_uncalibrated",
            calibration=structure.get("calibration"),
            hint="缺 SequenceOfUltrasoundRegions → 不换算 mm²，报告只给像素面积",
        )
        return None

    ps = getattr(ds, "PixelSpacing", None)
    if ps is None:
        logger.debug("nodule_pixel_spacing_absent")
        return None
    try:
        # pydicom 多值标签是 MultiValue（**不是** list/tuple 子类），
        # 故按"非字符串即可迭代"判定，别用 isinstance(list, tuple)。
        items = list(ps) if not isinstance(ps, str) else [ps]
        values = [float(str(v).strip()) for v in items]
    except (TypeError, ValueError) as exc:
        logger.warning("nodule_pixel_spacing_invalid", value=str(ps), error=str(exc))
        return None
    if len(values) < 2 or any(v <= 0 for v in values[:2]):
        logger.warning("nodule_pixel_spacing_rejected", parsed=values)
        return None
    return [round(values[0], 6), round(values[1], 6)]


def _area_mm2_from_dicom(content: bytes, area_px: int) -> float | None:
    """像素面积 → mm²。缺 PixelSpacing 或参数非法时返回 None（不猜）。"""
    ps = _pixel_spacing_from_dicom(content)
    if ps is None or area_px <= 0:
        return None
    return round(area_px * ps[0] * ps[1], 3)


async def _safe_similar_cases(request_row: Any) -> dict[str, Any]:
    """相似病例旁路（A.82）：**独立输出**，不进 LLM 知识上下文，失败不影响主判读。

    设计要点：
    * 默认 `scope="self"` → 只查**同一病人**的历史影像（合规、无需额外授权）；
    * 输出仅 `image_id / score / organ / date`，**不含 patient_id / session_id**；
    * 任何异常 → `status="unavailable"`，主流程照常出结论。
    """
    from app.config.settings import settings

    if not settings.similar_cases_enabled:
        return {"status": "disabled", "cases": []}

    session_id = str(getattr(request_row, "session_id", "") or "")
    image_ids = [str(x) for x in (getattr(request_row, "image_ids", None) or [])]
    if not image_ids:
        return {"status": "skipped", "reason": "no_image_ids", "cases": []}

    t = time.monotonic()
    try:
        from sqlalchemy import select

        from app.models.database import DiagnosisSession
        from app.services.dicom_storage import get_dicom_storage
        from app.services.similar_cases import find_similar_cases

        async with AsyncSessionLocal() as db:
            patient_id = (
                await db.scalar(
                    select(DiagnosisSession.patient_id).where(
                        DiagnosisSession.session_id == session_id
                    )
                )
            )
            repo = DicomImageRepository(db)
            rows = await repo.filter_existing_in_session(session_id, image_ids[:1])
            if not rows:
                return {"status": "unavailable", "reason": "image_not_indexed", "cases": []}
            storage_path = rows[0].storage_path
            index_image_id = rows[0].image_id

        storage = get_dicom_storage()
        content = await storage.retrieve(storage_path)
        if not content:
            return {"status": "unavailable", "reason": "image_unreadable", "cases": []}

        result = await find_similar_cases(
            image_content=content,
            patient_id=str(patient_id or "") or None,
            organ=str(getattr(request_row, "organ", "") or "") or None,
            scope=settings.similar_cases_scope,
            top_k=settings.similar_cases_top_k,
            exclude_image_id=str(index_image_id),
        )
        logger.info(
            "similar_cases_done",
            request_id=getattr(request_row, "request_id", ""),
            scope=result.get("scope"),
            status=result.get("status"),
            count=len(result.get("cases") or []),
            elapsed_ms=round((time.monotonic() - t) * 1000.0, 1),
        )
        return result
    except Exception as exc:  # 参考信息，失败不阻断判读；但必须留痕
        logger.warning(
            "similar_cases_bypass_failed",
            request_id=getattr(request_row, "request_id", ""),
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return {"status": "unavailable", "reason": type(exc).__name__, "cases": []}


async def _safe_nodule_contour(request_row: Any) -> dict[str, Any]:
    """超声结节/目标分割旁路：把轮廓与面积挂进 findings（2026-10-03）。

    ## 为什么是旁路

    分割是**辅助信息**，不是诊断结论。缺权重/缺影像/解码失败/器官未训练
    统统只写 ``available=False + reason``，**绝不影响诊断状态与置信度**——
    与 :func:`_safe_track_diagnosis` 同一套失败安全哲学。

    ## 取图链路（此前完全断裂）

    ``request.image_ids`` → ``dicom_images`` 索引 → ``storage_path`` →
    :meth:`DicomStorageService.retrieve` → :func:`extract_frame_from_bytes`
    → :meth:`NoduleSegmenter.infer`。三处曾经的断点已修：
      1. ``image_ids`` 收了就丢（0016 迁移补列）；
      2. 返回的 image_id 与落盘文件名是两套命名空间（``save(storage_id=...)``）；
      3. session→image 映射只存在于 audit_logs（0017 迁移建 ``dicom_images``）。

    ## 为什么默认关闭

    分割要读磁盘 + 跑 UNet 推理，是**分钟级 LLM 诊断之外的额外 IO/算力**。
    默认 ``enabled=false``，由配置显式开启——避免在没验证过的部署上
    悄悄给每例诊断加几百毫秒与磁盘读。
    """
    from app.config.settings import settings

    if not bool(getattr(settings, "nodule_contour_in_pipeline_enabled", False)):
        return {"available": False, "reason": "not_enabled", "skipped": True}

    organ_raw = str(getattr(request_row, "organ", "") or "").strip().lower()
    image_ids = list(getattr(request_row, "image_ids", None) or [])
    if not image_ids:
        return {"available": False, "reason": "no_image_ids", "skipped": True}

    t0 = time.monotonic()
    try:
        async with AsyncSessionLocal() as db:
            repo = DicomImageRepository(db)
            rows = await repo.filter_existing_in_session(
                str(getattr(request_row, "session_id", "") or ""), image_ids
            )
            if not rows:
                # 上传时索引写失败/跨 session/客户端自报了不存在的 ID
                return {
                    "available": False,
                    "reason": "image_not_indexed",
                    "reason_text": segmentation_reason_text("image_not_indexed"),
                    "requested_image_ids": image_ids[:5],
                }
            # A.51 多帧分期③：候选帧集（≤3 帧，控制推理成本）。
            # A.81：帧选取口径统一到 `dicom_structure.frame_indices()`（**全系统唯一实现**）。
            # 旧实现是"中间帧 ±2"，而建影像向量时用的是"首/中/末"——同一份 cine loop
            # 在两条链路上取的不是同一批帧，属于口径漂移。
            # 现策略：
            #   ①多张影像：每张取代表帧（首/中/末中的中间帧），至多前 2 张；
            #   ②单张多帧：`frame_indices(total)` 给出首/中/末，至多 3 帧；
            #   ③单帧影像：None（解码默认帧）。
            candidates: list[tuple[Any, int | None]] = []
            if len(rows) > 1:
                for r in rows[:2]:
                    fc = int(getattr(r, "frame_count", 1) or 1)
                    candidates.append((r, fc // 2 if fc > 1 else None))
            else:
                r = rows[0]
                fc = int(getattr(r, "frame_count", 1) or 1)
                if fc > 1:
                    # A.81：用统一口径（首/中/末），与影像向量建库共用同一个函数
                    for idx in frame_indices(fc):
                        candidates.append((r, idx))
                else:
                    candidates.append((r, None))
            candidates = candidates[:3]

        from app.imaging.dicom_pixels import (
            DicomPixelExtractionError,
            extract_frame_from_bytes,
        )
        from app.services.dicom_storage import get_dicom_storage

        storage = get_dicom_storage()
        evaluations: list[dict[str, Any]] = []
        best: dict[str, Any] | None = None
        content_cache: dict[str, bytes | None] = {}
        # A.86：**每个候选为什么被跳过都要记下来**。
        # 原实现用 `continue` 丢掉原因，最后统一报 `image_bytes_unavailable`——
        # 真机后果：医生看到"未做（取不到影像）"，实际是"这是屏幕截图，本来就不该勾画"。
        skips: list[dict[str, str]] = []
        for cand_row, cand_frame_index in candidates:
            content = content_cache.get(cand_row.image_id)
            if content is None:
                content = await storage.retrieve(cand_row.storage_path)
                if not content:
                    skips.append({"image_id": cand_row.image_id, "reason": "image_bytes_unavailable"})
                    continue
                content_cache[cand_row.image_id] = content
            # A.81 结构准入：二次采集（屏幕截图）会把烧录标注/标尺当病灶，
            # 必须先看结构再分割（A.80 实测：580 个文件里 67 个是二次采集）。
            structure = _structure_of_content(content)
            allowed, deny_reason = segmentation_allowed(structure)
            if not allowed:
                logger.info(
                    "nodule_segmentation_skipped_by_structure",
                    request_id=getattr(request_row, "request_id", ""),
                    image_id=cand_row.image_id,
                    reason=deny_reason,
                    sop_kind=structure.get("sop_kind"),
                )
                skips.append({"image_id": cand_row.image_id, "reason": deny_reason})
                continue
            frame = extract_frame_from_bytes(content, frame_index=cand_frame_index)
            result = await asyncio.to_thread(
                NoduleSegmenter.get(organ_raw).infer,
                image_bytes=frame.png_bytes,
            )
            area_px = round(result.lesion_area_ratio * result.width * result.height)
            evaluations.append({
                "image_id": cand_row.image_id,
                "frame_index": frame.frame_index,
                "total_frames": frame.total_frames,
                "mean_prob": round(result.mean_prob, 6),
                "area_px": area_px,
                "infer_latency_ms": result.latency_ms,
            })
            if best is None or result.mean_prob > best["result"].mean_prob:
                best = {
                    "row": cand_row,
                    "content": content,
                    "frame": frame,
                    "result": result,
                    "area_px": area_px,
                }

        if best is None:
            # A.86：用**真实跳过原因**作为 reason（首版一律报 image_bytes_unavailable，
            # 把"这是截图不该勾画"说成"取不到影像"，医生据此会去找技师重传 —— 白跑一趟）。
            reasons: dict[str, int] = {}
            for skip in skips:
                reasons[skip["reason"]] = reasons.get(skip["reason"], 0) + 1
            primary = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else "no_candidate"
            return {
                "available": False,
                "reason": primary,
                "reason_text": segmentation_reason_text(primary),
                "image_id": rows[0].image_id,
                "skipped": skips[:5],
            }

        row = best["row"]
        content = best["content"]
        frame = best["frame"]
        result = best["result"]
        area_px = best["area_px"]
        # mm² 换算：**必须**有 PixelSpacing，否则宁可不换算也不猜。
        # 超声 DICOM 普遍带 PixelSpacing（行距/列距 mm）；缺失时只报像素面积，
        # 由报告层写明"未提供像素间距，无法换算 mm²"。
        mm2 = _area_mm2_from_dicom(content, area_px)
        return {
            "available": True,
            "organ": organ_raw,
            "image_id": row.image_id,
            "frame_index": frame.frame_index,
            "total_frames": frame.total_frames,
            "width": result.width,
            "height": result.height,
            "lesion_area_ratio": round(result.lesion_area_ratio, 6),
            "area_px": area_px,
            "area_mm2": mm2,
            "pixel_spacing_mm": _pixel_spacing_from_dicom(content),
            "bbox": list(result.bbox) if result.bbox else None,
            "mean_prob": round(result.mean_prob, 6),
            "max_prob": round(result.max_prob, 6),
            "threshold": result.threshold,
            "model_dice_official": result.model_dice_official,
            "device": result.device,
            "infer_latency_ms": result.latency_ms,
            "bypass_latency_ms": round((time.monotonic() - t0) * 1000, 1),
            # A.51 多帧分期③：择优帧语义（报告/前端据此呈现"第 X/Y 帧"）
            "selection_policy": "best_mean_prob" if len(evaluations) > 1 else "single_frame",
            "candidates_evaluated": len(evaluations),
            "frames_evaluated": evaluations,
        }
    except SegmentationUnavailableError as exc:
        # 器官未训练 / 无公开数据 / 权重缺失 —— 明确原因，不是静默无结果
        logger.warning("nodule_contour_unavailable", organ=organ_raw, reason=str(exc))
        return {"available": False, "organ": organ_raw, "reason": str(exc)}
    except DicomPixelExtractionError as exc:
        logger.warning("nodule_contour_pixel_extraction_failed", error=str(exc))
        return {"available": False, "organ": organ_raw, "reason": f"dicom_decode_failed: {exc}"}
    except Exception as exc:  # pragma: no cover - 旁路兜底
        logger.warning(
            "nodule_contour_bypass_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return {
            "available": False,
            "organ": organ_raw,
            "reason": f"{type(exc).__name__}: {exc}",
        }


#: A.88 T5-3：幻觉检测严重度 → ``HallucinationSeverity`` 枚举值。
#: 判据是"命中 issue 的**最大** severity"，与
#: ``app/inference/hallucination_detector`` 的既有口径一致
#: （该模块 ``severity_threshold=2`` 判"是否幻觉"，本映射只负责把分级说清）。
_HALLUCINATION_SEVERITY_BY_LEVEL = {3: "severe", 2: "moderate", 1: "mild"}


def _detect_hallucination_safely(
    fusion_result,
    *,
    organ: str | None,
    request_id: str,
    force_enabled: bool = False,
    is_full_tier: bool = False,
) -> dict[str, Any]:
    """对本次**最终响应**做一次幻觉检测，结果供 ``findings.fusion_result`` 落库。

    ## 这条接线补的是什么（A.88 T5-3）

    ``DiagnosisResult.hallucination_severity`` 此前**全仓无生产者**：检测器只在
    ``app/inference/benchmark.py``（评测路径）里被调，而结果接口只能填 ``None``。
    接口侧当时的做法是"无生产者就不填值"（诚实，但能力等于不存在）。
    现在补上生产者，契约才有真实取值可读。

    ## 为什么写入 ``findings.fusion_result`` 而不是单独一个顶层键

    严重度是**对融合后的最终结论**做的质检，语义属于融合层的产出；放进
    ``fusion_result`` 后，读路径与 ``self_rag_passed`` / ``conflict_detected``
    同一处取值，不必再记一条"另一处还有质检字段"。
    键名与 schema 字段同名（``hallucination_severity``），读取侧可直接取。

    ## 为什么"未评估"不能写成 None 了事

    ``None`` 有两种含义：**评估过且干净**、**压根没评估**。二者对临床与运维的
    意义完全不同，故同时写 ``hallucination_checked``（真跑过没有）与
    ``hallucination_reason``（没跑的真实原因，封闭集合）。这是 AGENTS.md
    硬规则 8 的直接要求：不许用一个默认值把不同原因抹平。

    ## 成本与档位

    检测本身是纯正则/词表（无 LLM），实测耗时通常在 1ms 量级；但仍会遍历 5 类
    策略并触发一次 CUSUM/指标上报，所以在 FAST/COMPACT 档默认**不跑**
    （``hallucination_detection_in_pipeline`` 可强制打开）。失败绝不阻断诊断：
    任何异常 → ``checked=False`` + 真实原因。
    """
    if not (force_enabled or is_full_tier):
        return {
            "checked": False,
            "severity": None,
            "reason": "not_full_tier_and_switch_off",
        }

    final_response = getattr(fusion_result, "final_response", None)
    text = str(getattr(final_response, "content", "") or "")
    if final_response is None:
        return {"checked": False, "severity": None, "reason": "no_final_response"}
    if not text.strip():
        return {"checked": False, "severity": None, "reason": "empty_text"}

    organ_value = str(organ or getattr(final_response, "organ", "") or "")
    organ_value = getattr(getattr(final_response, "organ", None), "value", None) or organ_value
    if not organ_value:
        # 检测器按器官取词表；没有器官就没有词表 → 不猜（报"未评估"）。
        return {"checked": False, "severity": None, "reason": "no_organ"}

    t0 = time.monotonic()
    try:
        from app.inference.hallucination_detector import get_default_detector

        result = get_default_detector().detect(text, str(organ_value))
    except Exception as exc:
        logger.warning(
            "pipeline_hallucination_detect_failed",
            request_id=request_id,
            organ=str(organ_value),
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return {
            "checked": False,
            "severity": None,
            "reason": f"detector_failed: {type(exc).__name__}",
        }

    issues = list(getattr(result, "issues", None) or [])
    max_level = max(
        (int(getattr(i, "severity", 0) or 0) for i in issues),
        default=0,
    )
    severity = _HALLUCINATION_SEVERITY_BY_LEVEL.get(max_level)
    payload = {
        "checked": True,
        "severity": severity,
        "is_hallucination": bool(getattr(result, "is_hallucination", False)),
        "confidence": round(float(getattr(result, "confidence", 0.0) or 0.0), 4),
        "issue_count": len(issues),
        "issues": [
            {
                "strategy": getattr(getattr(i, "strategy", None), "value", None),
                "severity": int(getattr(i, "severity", 0) or 0),
                "description": str(getattr(i, "description", ""))[:200],
            }
            for i in issues[:5]
        ],
        "reason": "",
        "elapsed_ms": round((time.monotonic() - t0) * 1000.0, 2),
    }
    logger.info(
        "pipeline_hallucination_detected",
        request_id=request_id,
        organ=str(organ_value),
        severity=severity,
        is_hallucination=payload["is_hallucination"],
        issue_count=payload["issue_count"],
    )
    return payload


async def _safe_t2a_classification(request_row: Any) -> dict[str, Any]:
    """T2a 影像分类旁路（A.88 T5-1）：把 8003 的 T2a 分类器接进生产链。

    ## 这条旁路补的是什么

    此前 ``app/`` 下**没有任何** ``/v1/classify`` 调用点，只有审计脚本
    ``scripts/audit/p29_t2a_aul_holdout.py`` 会调 —— 于是"离线 AUC 0.8950"
    只是**离线模型指标**，不是系统能力（《架构真相》§3.3 第一条）。

    ## 三条红线（写在代码里，不靠口头约定）

    1. **只作辅助参考**：只写 ``findings["t2a_classification"]``，
       **不参与**结论选择 / ``confidence`` / ``status`` / ``needs_review``。
       理由：T2a 是"良性/恶性/正常"三分类，与本系统输出的 TI-RADS /
       LI-RADS / LVEF 分级体系**不是同一套判读语言**，拿它去改结论等于用
       一个未做临床验证的探针覆盖专科分级。
    2. **默认关**（``t2a_classifier_in_pipeline_enabled``，与
       ``nodule_contour_in_pipeline_enabled`` 同风格）：开启后每例多一次磁盘读
       + 一次跨进程 HTTP，在没验证过的部署上不该悄悄发生。
    3. **失败原因必须分开**（AGENTS.md 硬规则 8）："没影影像" / "服务没起" /
       "服务起了但没加载模型" 对医生意味着完全不同的下一步动作。
       故本函数**不做任何"统一 reason"的收敛**，只有"取不到影像字节"这一层
       自己判（``no_image_ids`` / ``image_not_indexed`` / ``no_image_bytes`` /
       ``frame_decode_failed``），其余一律原样透传客户端给的 reason
       （``service_unavailable`` / ``model_missing`` / ``invalid_response``）。

    ## 为什么是"顺序 await"而不是"与 dispatch 并发"

    并发能省下几十到几百毫秒，但要引入 task 生命周期管理（管线中途抛错时
    未 await 的 task 会被销毁并告警）。旁路默认关，开启后也不在 LLM 关键路径上
    （放在融合之后、写库之前），收益不足以换这份复杂度——与 ``_safe_similar_cases``
    的既有取舍一致。
    """
    from app.config.settings import settings

    if not bool(getattr(settings, "t2a_classifier_in_pipeline_enabled", False)):
        return {"available": False, "reason": "disabled", "skipped": True}

    image_ids = [str(x) for x in (getattr(request_row, "image_ids", None) or [])]
    if not image_ids:
        return {"available": False, "reason": "no_image_ids", "skipped": True}

    t0 = time.monotonic()
    try:
        from app.services.dicom_storage import get_dicom_storage

        async with AsyncSessionLocal() as db:
            repo = DicomImageRepository(db)
            rows = await repo.filter_existing_in_session(
                str(getattr(request_row, "session_id", "") or ""), image_ids
            )
            if not rows:
                return {
                    "available": False,
                    "reason": "image_not_indexed",
                    "requested_image_ids": image_ids[:5],
                }
            # 只取第一张（T2a 是"整幅图"级分类，多帧不带来额外信息却成倍加成本）
            row = rows[0]
            storage_path = row.storage_path
            image_id = row.image_id
            frame_count = int(getattr(row, "frame_count", 1) or 1)

        storage = get_dicom_storage()
        content = await storage.retrieve(storage_path)
        if not content:
            return {"available": False, "reason": "no_image_bytes", "image_id": image_id}

        from app.imaging.dicom_pixels import (
            DicomPixelExtractionError,
            extract_frame_from_bytes,
        )
        from app.inference.t2a_classifier import classify_frame

        # 代表帧：多帧取"首/中/末"里的中间帧（与相似病例/影像向量同一口径）
        frame_index = frame_indices(frame_count)[1] if frame_count > 1 else None
        try:
            frame = extract_frame_from_bytes(content, frame_index=frame_index)
        except DicomPixelExtractionError as exc:
            logger.warning(
                "t2a_frame_decode_failed",
                request_id=getattr(request_row, "request_id", ""),
                image_id=image_id,
                error=str(exc)[:160],
            )
            return {
                "available": False,
                "reason": "frame_decode_failed",
                "image_id": image_id,
                "detail": str(exc)[:200],
            }

        result = await classify_frame(
            frame.png_bytes,
            host=settings.ultrasound_clip_host,
            port=int(settings.ultrasound_clip_port),
            timeout_s=float(getattr(settings, "t2a_classifier_timeout_s", 10.0)),
            request_id=getattr(request_row, "request_id", ""),
        )
        payload = {
            **result,
            "image_id": image_id,
            "frame_index": frame.frame_index,
            "total_frames": frame.total_frames,
            "organ": str(getattr(request_row, "organ", "") or ""),
            # 语义标注随结果一起返回：读路径不必再"猜"这块能不能当依据
            "role": "auxiliary_reference",
            "bypass_latency_ms": round((time.monotonic() - t0) * 1000.0, 1),
        }
        logger.info(
            "t2a_classification_done",
            request_id=getattr(request_row, "request_id", ""),
            available=result.get("available"),
            reason=result.get("reason"),
            predicted=result.get("predicted"),
            elapsed_ms=payload["bypass_latency_ms"],
        )
        return payload
    except Exception as exc:  # 旁路：分类失败绝不影响主判读，但必须留痕
        logger.warning(
            "t2a_classification_bypass_failed",
            request_id=getattr(request_row, "request_id", ""),
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return {
            "available": False,
            "reason": "service_unavailable",
            "detail": f"{type(exc).__name__}: {exc}"[:200],
        }


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
    """构造 dispatch 失败时的占位 AgentResponse。

    A.59 可读化 + 转人工：原先 ``content=f"[pipeline] {reason}"`` 会把内部原因串
    （英文标识/异常串）当医生可见正文，且不置 ``needs_review`` —— 原始原因只进
    ``reasoning_trace``（工程侧对账用），正文给可读中文。
    """
    return AgentResponse(
        agent_name="pipeline_fallback",
        content=pipeline_unavailable_text(),
        confidence=0.0,
        needs_review=True,
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


def _field_of(source, name: str):
    """从**对象或 dict** 读一个字段（缺失返回 ``None``）。

    ``_fusion_result_to_dict`` 的两个调用形态不同：写路径拿到 ``FusionResult``
    对象，读路径/测试替身拿到落库后的 dict 或 ``SimpleNamespace``。同一个语义
    不能在两种形态上各写一份判断（``sg_output_data.extract_findings_text``
    已有同款教训），故抽此函数。
    """
    if isinstance(source, dict):
        return source.get(name)
    return getattr(source, name, None)


def _fusion_result_to_dict(fusion_result) -> dict:
    """把 FusionResult 转为可 JSON 序列化的 dict。

    ## A.88 A3：把融合层**真实跑出来的信号**带给对外契约

    此前本函数只写 ``needs_review`` / ``self_rag_passed`` 等少数键，而
    ``conflict_report`` / ``consistency_result`` 里的判定结果整包丢弃 ——
    结果接口只能拿 ``review_reason`` 反推，最终写成硬编码常量
    （``api/v1/diagnosis.py`` 的 ``consistency_flag=CONSISTENT`` 等，见同批 A3）。

    新增键（**只搬运、不推导、不填默认假值**；对象与 dict 两种形态都认，
    因为写路径拿到对象、读路径拿到落库后的 dict）：
      * ``conflict_detected``：冲突检测是否真的跑出冲突；
      * ``conflict_types``：冲突类型值列表（``grading_conflict`` 等）；
      * ``consistency_severity``：一致性严重度（``info``/``warning``/``critical``）；
      * ``compared_with_prior``：**真的**与既往诊断比过（而非"有既往数据"）；
      * ``compared_with_guidelines``：**真的**与指南比过；
      * ``validate_degraded``：validate 超预算被降级（不是失败）。

    缺省语义：字段缺失/None → 不写这些键（"未评估"必须能与"评估通过"区分，
    不能靠 False 假装评估过）。``_fusion_result_to_dict(None)`` 仍返回 ``{}``。
    """
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

    # ── A.88 A3：冲突检测真实信号 ──────────────────────────────────────
    conflict_report = _field_of(fusion_result, "conflict_report")
    if conflict_report is not None:
        has_conflict = _field_of(conflict_report, "has_conflict")
        if has_conflict is not None:
            payload["conflict_detected"] = bool(has_conflict)
        raw_types = _field_of(conflict_report, "conflict_types") or []
        payload["conflict_types"] = [
            getattr(t, "value", t) for t in raw_types
        ]

    # ── A.88 A3：一致性校验真实信号 ────────────────────────────────────
    consistency_result = _field_of(fusion_result, "consistency_result")
    if consistency_result is not None:
        severity = _field_of(consistency_result, "severity")
        if severity is not None:
            payload["consistency_severity"] = getattr(severity, "value", severity)
        # 两个"比过没有"必须是**真实**的布尔：None / 缺失一律不写，
        # 宁可让读路径判"未评估"（→ consistency_flag=None），
        # 也不能把"没比过"写成"比过且一致"。
        compared_prior = _field_of(consistency_result, "compared_with_prior")
        if isinstance(compared_prior, bool):
            payload["compared_with_prior"] = compared_prior
        compared_guidelines = _field_of(
            consistency_result, "compared_with_guidelines"
        )
        if isinstance(compared_guidelines, bool):
            payload["compared_with_guidelines"] = compared_guidelines

    # ── A.88 A3：validate 降级标记（超预算降级 ≠ 失败）──────────────────
    validate_degraded = _field_of(fusion_result, "validate_degraded")
    if isinstance(validate_degraded, bool):
        payload["validate_degraded"] = validate_degraded

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
