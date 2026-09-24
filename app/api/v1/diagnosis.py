from fastapi import APIRouter, Header, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone
from typing import Optional
import uuid
import json
import time
import structlog

from app.models.schemas import (
    DiagnosisRequest,
    DiagnosisRequestResponse,
    DiagnosisResult,
    DiagnosisEvidence,
    DiagnosisQualityControl,
    MultiOrganDiagnosisRequest,
    MultiOrganDiagnosisResponse,
    ResponseTier,
    ConsentStatus,
    FusionStatus,
    DesensitizationStatus,
    ConsistencyFlag,
    RetryStrategy,
)
from app.api.v1.errors import (
    BadRequestException,
    NotFoundException,
    SafetyGatewayUnavailableException,
)
from app.config.database import get_db_session
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.rate_limit import check_rate_limit, _get_user_id_from_request
from app.config.settings import settings
from app.dependencies import get_diagnosis_repo, get_consent_repo, get_session_repo, get_audit_service
from app.api.v1.utils import get_actor_id, check_consent_status, try_renew_session
from app.services.diagnosis_worker import enqueue_diagnosis
from app.services.multi_organ_orchestrator import (
    SessionNotFoundError,
    run_multi_organ_diagnosis,
)
from app.services.safety_gateway_client import safety_gateway_client
from app.security.auth import get_current_active_user
from app.api.websocket.nav import manager as nav_manager

router = APIRouter(tags=["diagnosis"])
logger = structlog.get_logger()

IDEMPOTENCY_TTL_SECONDS = 600
RESULT_TIMEOUT_SECONDS = 30


def _generate_request_id() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d")
    return f"req_{ts}_{uuid.uuid4().hex[:8]}"


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
    current_user: Optional[dict] = Depends(get_current_active_user),
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

    created_id = await diagnosis_repo.create_request(
        session_id=request.session_id,
        organ=request.target_organ,
        response_tier=request.response_tier,
        patient_context=request.patient_context.model_dump() if request.patient_context else None,
        image_ids=request.image_ids,
        clinical_text=request.clinical_text,
    )

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


@router.post("/multi-organ", response_model=MultiOrganDiagnosisResponse)
async def submit_multi_organ_diagnosis(
    request: MultiOrganDiagnosisRequest,
    fastapi_request: Request,
    session_repo=Depends(get_session_repo),
    consent_repo=Depends(get_consent_repo),
    audit_service=Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
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
    current_user: Optional[dict] = Depends(get_current_active_user),
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

    sg_result = await safety_gateway_client.check_output(
        session_id=result.session_id,
        patient_id=patient_id,
        output_data=result.findings or {},
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

    diagnosis_result = DiagnosisResult(
        request_id=request_id,
        status="completed" if result_status == "completed" else result_status,
        consistency_flag=ConsistencyFlag.CONSISTENT,
        desensitization_status=desensitization_status,
        consent_status_at_output=consent_status_at_output,
        fusion_status=FusionStatus.NORMAL,
        diagnosis=diagnosis_payload,
        evidence=DiagnosisEvidence(
            knowledge_ids=result.knowledge_ids if hasattr(result, "knowledge_ids") and result.knowledge_ids else None,
            content=result.findings if result.findings else None,
        ),
        quality_control=DiagnosisQualityControl(
            confidence_score=result.confidence if hasattr(result, "confidence") and result.confidence else None,
            self_rag_passed=False,
            consistency_check_passed=True,
        ),
        requires_manual_review=requires_manual_review,
        review_reason=review_reason,
        ai_diagnosis_failed=False,
        hallucination_severity=None,
        version_compatibility="compatible",
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
