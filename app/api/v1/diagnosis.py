import json
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.routing import RoutingDecision
from app.api.v1.errors import (
    BadRequestException,
    NotFoundException,
    SafetyGatewayUnavailableException,
)
from app.api.v1.utils import check_consent_status, get_actor_id, try_renew_session
from app.api.websocket.nav import manager as nav_manager
from app.config.database import get_db_session
from app.config.rate_limit import _get_user_id_from_request, check_rate_limit
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.settings import settings
from app.config.validator import check_version_compatibility
from app.dependencies import get_audit_service, get_consent_repo, get_diagnosis_repo, get_session_repo
from app.models.schemas import (
    ClinicalAction,
    ConsentStatus,
    ConsistencyFlag,
    CrossModalityHint,
    DesensitizationStatus,
    DiagnosisEvidence,
    DiagnosisQualityControl,
    DiagnosisRequest,
    DiagnosisRequestResponse,
    DiagnosisResult,
    DifferentialDiagnosis,
    FusionStatus,
    MalignancyRisk,
    Measurement,
    MultiOrganDiagnosisRequest,
    MultiOrganDiagnosisResponse,
    PatientExplanation,
    ResearchMetrics,
    ResponseTier,
    RetryStrategy,
    RoutingConfirmationRequest,
    RoutingConfirmationResponse,
)
from app.security.auth import get_current_active_user
from app.services.diagnosis_pipeline import get_routing_router
from app.services.diagnosis_worker import enqueue_diagnosis
from app.services.multi_organ_orchestrator import (
    SessionNotFoundError,
    run_multi_organ_diagnosis,
)
from app.services.safety_gateway_client import safety_gateway_client
from app.services.sg_output_data import build_sg_output_data, extract_findings_text

router = APIRouter(tags=["diagnosis"])
logger = structlog.get_logger()

IDEMPOTENCY_TTL_SECONDS = 600
RESULT_TIMEOUT_SECONDS = 30


def _generate_request_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"req_{ts}_{uuid.uuid4().hex[:8]}"


def clinical_attribute_fields(diagnosis_payload: dict | None) -> dict:
    """从 findings 的 ``clinical_attributes`` 映射出对外契约字段（M1~M12）。

    管线把专科响应里的结构化临床属性写进 findings 的 ``clinical_attributes``
    子字典，本函数是把它转成**类型化契约字段**的唯一出口（写库结构不进 API
    契约，避免 DB 结构变化直接泄漏到前端）。

    容错策略：逐字段独立解析，单个字段结构异常不影响其它字段——诊断结果页
    能展示多少就展示多少，不能因为一个测量值格式不对就让整个结果接口 500
    （这是医生正在看的页面）。
    """
    clinical = (diagnosis_payload or {}).get("clinical_attributes")
    if not isinstance(clinical, dict):
        # 始终返回完整键集（值为默认空），调用方可以直接 **展开 而不必
        # 关心"有没有 clinical_attributes"
        clinical = {}

    def _one(key: str, model):
        value = clinical.get(key)
        if not value:
            return None
        try:
            return model.model_validate(value)
        except Exception as exc:
            logger.warning(
                "clinical_attribute_parse_failed",
                field=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def _many(key: str, model) -> list:
        values = clinical.get(key)
        if not isinstance(values, list):
            return []
        parsed = []
        for item in values:
            try:
                parsed.append(model.model_validate(item))
            except Exception as exc:
                logger.warning(
                    "clinical_attribute_parse_failed",
                    field=key,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        return parsed

    return {
        "measurement": _one("measurement", Measurement),
        # M5：恶性概率必须与置信度分开展示（语义不同）
        "malignancy_risk": _one("malignancy_risk", MalignancyRisk),
        "patient_explanation": _one("patient_explanation", PatientExplanation),
        "research_metrics": _one("research_metrics", ResearchMetrics),
        "differential_diagnoses": _many("differential_diagnoses", DifferentialDiagnosis),
        "clinical_actions": _many("clinical_actions", ClinicalAction),
        "cross_modality_hints": _many("cross_modality_hints", CrossModalityHint),
        "posterior_acoustic": clinical.get("posterior_acoustic") or None,
    }


async def _push_diagnosis_result(result, request_id: str, db: AsyncSession):
    try:
        await nav_manager.send_diagnosis_result(result.session_id, request_id)
        result.result_pushed = True
        await db.flush()
    except Exception as e:
        logger.warning("Failed to push diagnosis result via WebSocket", error=str(e))


@router.post("/request", response_model=DiagnosisRequestResponse)
async def submit_diagnosis_request(
    request: DiagnosisRequest,
    fastapi_request: Request,
    x_priority: int = Header(default=1, description="优先级: 0=急诊, 1=常规, 2=教学"),
    diagnosis_repo=Depends(get_diagnosis_repo),
    consent_repo=Depends(get_consent_repo),
    session_repo=Depends(get_session_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    client_ip = fastapi_request.client.host if fastapi_request.client else "unknown"
    user_id = _get_user_id_from_request(fastapi_request)
    is_emergency = x_priority == 0

    skip_rate_limit = request.retry

    rate_limit_exc = await check_rate_limit(
        path="/api/v1/diagnosis/request",
        client_ip=client_ip,
        user_id=user_id,
        is_emergency=is_emergency,
        skip=skip_rate_limit,
        dedup_key=f"{request.session_id}",
    )
    if rate_limit_exc:
        raise rate_limit_exc

    if request.idempotency_key:
        cache_key = RedisKey.diagnosis_idempotency(request.idempotency_key)
        idempotency_result = await RedisPool.get_with_error(cache_key)
        if idempotency_result.error:
            logger.warning("idempotency_check_redis_error", key=cache_key)
        elif idempotency_result.value:
            raw = idempotency_result.decode_value()
            cached_data = json.loads(raw)
            cached_request_id = cached_data.get("request_id")
            if cached_request_id:
                result_cache_key = RedisKey.diagnosis_result(cached_request_id)
                result_cached = await RedisPool.get_with_error(result_cache_key)
                if result_cached.error:
                    logger.warning("idempotency_result_cache_redis_error", key=result_cache_key)
                elif result_cached.value:
                    result_data = json.loads(result_cached.decode_value())
                    if result_data.get("status") == "completed":
                        from fastapi.responses import JSONResponse
                        return JSONResponse(
                            status_code=200,
                            content=result_data,
                            headers={"X-Idempotent-Replay": "true"},
                        )
                    return DiagnosisRequestResponse(**result_data) if "session_id" in result_data else DiagnosisRequestResponse(**cached_data)
            return DiagnosisRequestResponse(**cached_data)

    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {request.session_id}")

    consent_record = await consent_repo.get_latest_consent(session.patient_id)
    check_consent_status(consent_record, session.patient_id)

    request_id = _generate_request_id()

    # request_id 必须由端点生成并**贯穿全链**（落库 / 入队 / 响应 / 结果查询）。
    # 若让 `create_request` 自己再生成一个，库里存的与队列里传的就是两个不同 id
    # → worker 永远 `pipeline_request_not_found`、请求永久停在 processing（真机实测）。
    await diagnosis_repo.create_request(
        session_id=request.session_id,
        organ=request.target_organ,
        response_tier=request.response_tier,
        patient_context=request.patient_context.model_dump() if request.patient_context else None,
        image_ids=request.image_ids,
        clinical_text=request.clinical_text,
        request_id=request_id,
    )

    # 必须先**提交**再入队（2026-09-28 真机链路冒烟暴露的阻断级缺陷）：
    # `create_request` 只 `flush()` 不提交，而 worker 一旦 BLPOP 到 request_id，
    # 是用**另一个会话**去读库 —— 未提交的行对它不可见，于是报
    #   pipeline_request_not_found → 请求永久停在 `status=processing`、
    #   结果接口一直 404，且**没有任何重试**能把它救回来。
    # 顺序不能颠倒：先提交（行可见）→ 再入队（worker 才可能读到）。
    await diagnosis_repo.db.commit()

    # T-GA-INT-01: 把 request_id 入队到后台 worker 池
    # 入队失败不阻塞端点返回 — worker 池有 fallback 处理
    try:
        enqueued = await enqueue_diagnosis(request_id)
        if not enqueued:
            logger.warning(
                "diagnosis_enqueue_failed_endpoint",
                request_id=request_id,
            )
    except Exception as exc:
        logger.warning(
            "diagnosis_enqueue_exception_endpoint",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    if request.retry and not request.retry_strategy:
        request.retry_strategy = RetryStrategy.FULL_RETRIEVAL

    if request.retry:
        retry_flag_key = f"ultrasound:diagnosis:retry_skip_self_rag:{request_id}"
        try:
            await RedisPool.setex(
                retry_flag_key,
                IDEMPOTENCY_TTL_SECONDS,
                json.dumps({
                    "skip_self_rag": True,
                    "retry_strategy": request.retry_strategy.value if request.retry_strategy else None,
                }),
            )
        except Exception as e:
            logger.warning("retry_flag_set_failed", request_id=request_id, error=str(e))

    estimated_time_ms = 3000 if request.response_tier == ResponseTier.FULL else 2000

    response_data = DiagnosisRequestResponse(
        request_id=request_id,
        session_id=request.session_id,
        status="processing",
        estimated_time_ms=estimated_time_ms,
        response_tier=request.response_tier,
    )

    if request.idempotency_key:
        cache_key = RedisKey.diagnosis_idempotency(request.idempotency_key)
        await RedisPool.setex(
            cache_key,
            IDEMPOTENCY_TTL_SECONDS,
            json.dumps(response_data.model_dump())
        )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="diagnosis_request",
        actor=actor_id,
        session_id=request.session_id,
        target_type="diagnosis",
        target_id=request_id,
        detail={
            "organ": request.target_organ,
            "response_tier": request.response_tier.value,
            "priority": x_priority,
            "retry": request.retry,
            "retry_strategy": request.retry_strategy.value if request.retry_strategy else None,
        },
    )

    await try_renew_session(session_repo, request.session_id)

    return response_data


RESULT_CACHE_TTL_SECONDS = 600


@router.post(
    "/{request_id}/confirm-upgrade", response_model=RoutingConfirmationResponse
)
async def confirm_routing_upgrade(
    request_id: str,
    payload: RoutingConfirmationRequest,
    diagnosis_repo=Depends(get_diagnosis_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """T-GB-AGT-02：医生对"是否升级到全量检索档位"的确认回写。

    铁律「升级决策权交医生」的落地：

    - 只有"多特征组合推理"的判定会带 ``requires_confirmation=True``；
    - 医生确认 → 请求档位改为 ``full`` 并重新入队（管线的路由判定会
      直接给出全量路径，``reasons`` 含 ``doctor_requested_full``）；
    - 医生拒绝 → 保持原档位，仅记录确认结果（**不可降级**：跨器官 /
      置信度不足两类情形从一开始就走全量，不产生待确认项）。
    """
    result = await diagnosis_repo.get_result(request_id)
    if not result:
        raise NotFoundException(error_message=f"Diagnosis result not found: {request_id}")

    findings = dict(result.findings) if isinstance(result.findings, dict) else {}
    decision = RoutingDecision.from_dict(findings.get("routing"))
    if decision is None:
        raise BadRequestException(
            error_message="诊断结果中不存在可确认的路由判定（findings.routing 缺失或非法）"
        )
    if not decision.pending_upgrade:
        raise BadRequestException(
            error_message="当前路由判定不存在待确认的档位升级"
        )

    router = get_routing_router()
    final = router.apply_confirmation(decision, payload.confirmed)

    actor_id = get_actor_id(current_user)
    routing_record = final.as_dict()
    routing_record["confirmation"] = {
        "confirmed": payload.confirmed,
        "confirmed_by": actor_id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "reason": payload.reason,
    }

    previous_status = result.status
    rerun_enqueued = False

    if payload.confirmed:
        # 用全量档位重跑：改 tier → 管线按 doctor_requested_full 走全量路径。
        await diagnosis_repo.update_tier(request_id, ResponseTier.FULL.value)
        merged_findings = {**findings, "routing": routing_record}
        await diagnosis_repo.update_result(
            request_id=request_id,
            status="processing",
            confidence=result.confidence,
            findings=merged_findings,
            recommendations=result.recommendations,
            knowledge_ids=result.knowledge_ids,
        )
        enqueued = await enqueue_diagnosis(request_id)
        if enqueued:
            rerun_enqueued = True
        else:
            # 入队失败（Redis 与进程内队列均不可用）：回滚为原状态，避免
            # 结果永久停在 processing 而让前端一直轮询到 timeout。
            logger.warning(
                "routing_upgrade_rerun_enqueue_failed",
                request_id=request_id,
            )
            await diagnosis_repo.update_result(
                request_id=request_id,
                status=previous_status,
                confidence=result.confidence,
                findings=merged_findings,
                recommendations=result.recommendations,
                knowledge_ids=result.knowledge_ids,
            )
    else:
        await diagnosis_repo.update_findings(
            request_id, {**findings, "routing": routing_record}
        )

    try:
        await diagnosis_repo.db.commit()
    except Exception as exc:
        logger.warning("routing_confirmation_commit_failed", error=str(exc))

    await audit_service.log_action(
        action="routing_upgrade_confirmation",
        actor=actor_id,
        session_id=getattr(result, "session_id", None),
        target_type="diagnosis",
        target_id=request_id,
        detail={
            "confirmed": payload.confirmed,
            "effective_tier": final.effective_tier.value,
            "requested_tier": final.requested_tier.value,
            "reasons": [r.value for r in final.reasons],
            "rerun_enqueued": rerun_enqueued,
            "reason": payload.reason,
        },
    )

    return RoutingConfirmationResponse(
        request_id=request_id,
        confirmed=payload.confirmed,
        effective_tier=final.effective_tier,
        requires_confirmation=final.requires_confirmation,
        rerun_enqueued=rerun_enqueued,
        routing=routing_record,
    )


@router.post("/multi-organ", response_model=MultiOrganDiagnosisResponse)
async def submit_multi_organ_diagnosis(
    request: MultiOrganDiagnosisRequest,
    fastapi_request: Request,
    session_repo=Depends(get_session_repo),
    consent_repo=Depends(get_consent_repo),
    audit_service=Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    """T-GB-AGT-01：会话级多器官联合诊断（三器官串行 + 部分完成）。

    与单器官 ``POST /request`` 的差异：
    - 一次调用按规格顺序串行处理会话的全部目标器官；
    - 单器官超时/失败不阻断其余器官，响应中返回各器官状态与
      ``partial`` 标记；
    - 会话 ``organ_statuses`` 与状态同步更新，前端可据此渲染进度
      （WebSocket 侧推送 ``organ_switching``）。
    """
    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {request.session_id}")

    consent_record = await consent_repo.get_latest_consent(session.patient_id)
    check_consent_status(consent_record, session.patient_id)

    try:
        outcome = await run_multi_organ_diagnosis(
            session_id=request.session_id,
            organs=request.organs,
            per_organ_timeout_s=request.per_organ_timeout_seconds,
            total_timeout_s=request.total_timeout_seconds,
            response_tier=request.response_tier,
        )
    except SessionNotFoundError as exc:
        raise NotFoundException(error_message=str(exc))

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="diagnosis_multi_organ",
        actor=actor_id,
        session_id=request.session_id,
        target_type="diagnosis",
        target_id=request.session_id,
        detail={
            "organs": outcome.organs,
            "session_status": outcome.session_status,
            "partial": outcome.partial,
            "total_ms": round(outcome.total_ms, 2),
            "organ_statuses": outcome.organ_statuses,
        },
    )

    await try_renew_session(session_repo, request.session_id)

    return MultiOrganDiagnosisResponse(
        session_id=outcome.session_id,
        session_status=outcome.session_status,
        organs=outcome.organs,
        organ_statuses=outcome.organ_statuses,
        organ_results=[r.as_dict() for r in outcome.organ_results],
        completed_organs=outcome.completed_organs,
        failed_organs=outcome.failed_organs,
        skipped_organs=outcome.skipped_organs,
        needs_review_organs=outcome.needs_review_organs,
        partial=outcome.partial,
        total_ms=outcome.total_ms,
    )


@router.get("/result/{request_id}", response_model=DiagnosisResult)
async def get_diagnosis_result(
    request_id: str,
    response: Response,
    db: AsyncSession = Depends(get_db_session),
    diagnosis_repo=Depends(get_diagnosis_repo),
    consent_repo=Depends(get_consent_repo),
    session_repo=Depends(get_session_repo),
    current_user: dict | None = Depends(get_current_active_user),
):
    cache_key = RedisKey.diagnosis_result(request_id)
    cached = await RedisPool.get_with_error(cache_key)
    if cached.error:
        logger.warning("result_cache_redis_error", key=cache_key)
    elif cached.value:
        raw = cached.decode_value()
        return DiagnosisResult.model_validate(json.loads(raw))

    result = await diagnosis_repo.get_result(request_id)
    if not result:
        raise NotFoundException(error_message=f"Diagnosis result not found: {request_id}")

    session = await session_repo.get_session(result.session_id)
    patient_id = session.patient_id if session else "unknown"
    consent_record = await consent_repo.get_latest_consent(patient_id)
    consent_status = check_consent_status(consent_record, patient_id)

    # 读路径的载荷必须与写路径（app/services/diagnosis_pipeline.py）**同一个构造函数**。
    # 2026-09-29 真机暴露：这里原先直接把 worker 落库的整包 findings
    # （organ/fusion_result/sg_result/timings_ms）当 output_data，且**不带患者条件** ——
    # 于是同一份诊断在写处 pass、在读处被判"检测到禁忌"，
    # 响应里的 `requires_manual_review` 恒为真、系统永远给不出自动结论。
    _stored = result.findings if isinstance(result.findings, dict) else {}
    sg_result = await safety_gateway_client.check_output(
        session_id=result.session_id,
        patient_id=patient_id,
        output_data=build_sg_output_data(
            organ=result.organ,
            findings=extract_findings_text(_stored.get("fusion_result")),
            patient_context=getattr(result, "patient_context", None),
        ),
        consent_status_at_input=consent_status.value,
    )

    requires_manual_review = False
    review_reason = None

    if not sg_result.get("passed", sg_result.get("allowed", False)):
        if sg_result.get("service_unavailable", False):
            raise SafetyGatewayUnavailableException()
        desensitization_status = DesensitizationStatus.DEGRADED
        consent_status_at_output = ConsentStatus.DENIED
        requires_manual_review = True
        review_reason = sg_result.get("message", "安全校验未通过")
    else:
        consent_status_at_output = ConsentStatus.GRANTED
        sg_consent = sg_result.get("consent_status_at_output", "granted")
        if sg_consent == "withdrawn":
            consent_status_at_output = ConsentStatus.WITHDRAWN
        elif sg_consent in ("unknown_degraded", "unknown_blocked"):
            consent_status_at_output = ConsentStatus.DENIED

        desensitization_status = DesensitizationStatus.FULL
        if sg_result.get("degraded_mode"):
            desensitization_status = DesensitizationStatus.DEGRADED

    result_status = result.status
    if result_status == "processing" and result.created_at:
        elapsed = (datetime.now(timezone.utc) - result.created_at).total_seconds()
        if elapsed > RESULT_TIMEOUT_SECONDS:
            result_status = "timeout"

    # 后台 worker（app/services/diagnosis_pipeline.py）写回的 findings 即
    # 真实诊断结论，其结构为 organ / fusion_result / sg_result / timings_ms。
    # 只有 worker 尚未写入（status=processing/timeout）时才给占位 payload。
    diagnosis_payload = result.findings if isinstance(result.findings, dict) and result.findings else None
    if diagnosis_payload:
        timings = diagnosis_payload.get("timings_ms") or {}
        processing_time_ms = int(timings.get("pipeline.total_ms") or 0) or settings.phase0a_mock_processing_time_ms
        model_version = diagnosis_payload.get("model_version") or settings.phase0a_mock_model_version
        knowledge_base_version = (
            diagnosis_payload.get("knowledge_base_version") or settings.phase0a_mock_kb_version
        )
    else:
        diagnosis_payload = {
            "organ": result.organ,
            "findings": "Waiting for diagnosis system completion",
            "classification": "N/A",
            "recommendation": "N/A",
            "pending": True,
        }
        processing_time_ms = settings.phase0a_mock_processing_time_ms
        model_version = settings.phase0a_mock_model_version
        knowledge_base_version = settings.phase0a_mock_kb_version

    # 2026-09-28 接线审计修复：`quality_control` 与 `ai_diagnosis_failed` 此前是**硬编码占位**
    # （`self_rag_passed=False`、`consistency_check_passed=True`、`ai_diagnosis_failed=False`），
    # 会造成两个方向的误导：把"未评估"写成"未通过"、把"没检查过"写成"检查通过"。
    # 现在一律从管线写回的真实信号推导（`diagnosis_pipeline.py` 的 fusion_result）。
    #
    # 契约限制：`DiagnosisQualityControl` 的两个字段是**非可选 bool**（`schemas.py:206-207`），
    # 因此无法表达"未评估"这一第三态；本实现按"未评估 → 不得声称通过（False 保守）"处理。
    # 如需真正的三态语义，需要把契约改成 `bool | None`（影响面较大，另行排期）。
    fusion_payload = diagnosis_payload.get("fusion_result")
    if isinstance(fusion_payload, dict) and fusion_payload:
        self_rag_passed = bool(fusion_payload.get("self_rag_passed"))
        # 真实信号：融合层在一致性 CRITICAL 时置 review_reason="consistency_failed"
        # （见 app/fusion/layered_validator.py）。没有该 reason 才认为一致性未失败。
        consistency_check_passed = (
            fusion_payload.get("review_reason") != "consistency_failed"
        )
    else:
        # 没有融合结果（worker 未写入 / 占位 payload）→ 不得声称质检通过
        self_rag_passed = False
        consistency_check_passed = False
    # 真实失败语义：与 MonitorAgent._is_failure 的判据保持一致（monitor.py:138-142），
    # 否则 R3"同器官连续失败"窗口永远不会被写入。
    ai_diagnosis_failed = result_status in ("failed", "timeout")

    diagnosis_result = DiagnosisResult(
        request_id=request_id,
        status="completed" if result_status == "completed" else result_status,
        consistency_flag=ConsistencyFlag.CONSISTENT,
        desensitization_status=desensitization_status,
        consent_status_at_output=consent_status_at_output,
        fusion_status=FusionStatus.NORMAL,
        diagnosis=diagnosis_payload,
        **clinical_attribute_fields(diagnosis_payload),
        evidence=DiagnosisEvidence(
            knowledge_ids=result.knowledge_ids if hasattr(result, "knowledge_ids") and result.knowledge_ids else None,
            content=result.findings if result.findings else None,
        ),
        quality_control=DiagnosisQualityControl(
            confidence_score=result.confidence if hasattr(result, "confidence") and result.confidence else None,
            self_rag_passed=self_rag_passed,
            consistency_check_passed=consistency_check_passed,
        ),
        requires_manual_review=requires_manual_review,
        review_reason=review_reason,
        ai_diagnosis_failed=ai_diagnosis_failed,
        hallucination_severity=None,
        # T-G1-DATA-02：按兼容矩阵真实计算，不再是恒为 "compatible" 的占位值
        version_compatibility=check_version_compatibility(
            model_version, knowledge_base_version
        ),
        processing_time_ms=processing_time_ms,
        model_version=model_version,
        knowledge_base_version=knowledge_base_version,
    )

    if result.status == "completed" and not result.result_pushed:
        await _push_diagnosis_result(result, request_id, db)

    try:
        if diagnosis_result.status == "completed":
            await RedisPool.setex(
                cache_key,
                RESULT_CACHE_TTL_SECONDS,
                json.dumps(diagnosis_result.model_dump()),
            )
        else:
            await RedisPool.setex(
                cache_key,
                30,
                json.dumps(diagnosis_result.model_dump()),
            )
    except Exception as e:
        logger.warning("result_cache_set_failed", key=cache_key, error=str(e))

    if diagnosis_result.status == "processing":
        response.headers["Retry-After"] = "2"

    return diagnosis_result
