from fastapi import APIRouter, Depends
from typing import Optional
import json
import structlog

from app.models.schemas import (
    FeedbackSubmission,
    FeedbackType,
    FeedbackLevel,
    FeedbackSubmitResponse,
)
from app.api.v1.errors import BadRequestException, NotFoundException
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.dependencies import get_feedback_repo, get_diagnosis_repo, get_session_repo, get_audit_service
from app.api.v1.utils import get_actor_id, try_renew_session
from app.repositories import FeedbackRepository, DiagnosisRepository, SessionRepository
from app.services.audit_service import AuditService
from app.security.auth import get_current_active_user

router = APIRouter(tags=["feedback"])
logger = structlog.get_logger()

FEEDBACK_IDEMPOTENCY_TTL_SECONDS = 86400


@router.post("", response_model=FeedbackSubmitResponse)
async def submit_feedback(
    request: FeedbackSubmission,
    feedback_repo: FeedbackRepository = Depends(get_feedback_repo),
    diagnosis_repo: DiagnosisRepository = Depends(get_diagnosis_repo),
    session_repo: SessionRepository = Depends(get_session_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
):

    if request.idempotency_key:
        cache_key = RedisKey.feedback_idempotency(request.idempotency_key)
        result = await RedisPool.get_with_error(cache_key)
        if result.error:
            logger.warning("feedback_idempotency_check_redis_error", key=cache_key)
        elif result.value:
            raw = result.decode_value()
            cached_data = json.loads(raw)
            return cached_data

    diagnosis = await diagnosis_repo.get_result(request.request_id)
    if not diagnosis:
        raise NotFoundException(error_message=f"Diagnosis request not found: {request.request_id}")

    if request.feedback_type == FeedbackType.RULE_CHANGE and request.feedback_level != FeedbackLevel.L3:
        raise BadRequestException(
            error_code="E1001",
            error_message="rule_change类型反馈必须使用L3级别",
        )

    reviewer_id = get_actor_id(current_user)

    feedback = await feedback_repo.create_feedback(
        request_id=request.request_id,
        session_id=request.session_id,
        feedback_type=request.feedback_type.value,
        feedback_level=request.feedback_level.value,
        reviewer_id=reviewer_id,
        content=request.content.model_dump(),
    )

    response_data = FeedbackSubmitResponse(
        status="accepted",
        message="反馈已提交",
        request_id=request.request_id,
        feedback_id=feedback.feedback_id,
    )

    if request.idempotency_key:
        cache_key = RedisKey.feedback_idempotency(request.idempotency_key)
        await RedisPool.setex(
            cache_key,
            FEEDBACK_IDEMPOTENCY_TTL_SECONDS,
            json.dumps(response_data.model_dump()),
        )

    await audit_service.log_action(
        action="feedback_submit",
        actor=reviewer_id,
        session_id=request.session_id,
        target_type="feedback",
        target_id=feedback.feedback_id,
        detail={
            "feedback_type": request.feedback_type.value,
            "feedback_level": request.feedback_level.value,
            "reviewer_id": request.reviewer_id,
        },
    )

    await try_renew_session(session_repo, request.session_id)

    return response_data
