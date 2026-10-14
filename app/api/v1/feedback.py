import json

import structlog
from fastapi import APIRouter, Depends

from app.api.v1.errors import BadRequestException, NotFoundException
from app.api.v1.utils import get_actor_id, try_renew_session
from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.dependencies import get_audit_service, get_diagnosis_repo, get_feedback_repo, get_session_repo
from app.models.schemas import (
    FeedbackLevel,
    FeedbackSubmission,
    FeedbackSubmitResponse,
    FeedbackType,
)
from app.repositories import DiagnosisRepository, FeedbackRepository, SessionRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService

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
    current_user: dict | None = Depends(get_current_active_user),
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

    # A.62 批 3（医生视角联调踩到）：反馈**必须挂在真实判读上**（评测集按 request_id 关联「正确口径」），
    # 但客户端给的 `request_id` 可能不是判读 id —— 历史前端用 `generateFeedbackId()` 自造 `fb_…`，
    # 于是医生点「提交反馈」在真机上必得 404 `E1002 判读请求不存在`（我实测复现，见 16 号 A.62.13）。
    #
    # 解析顺序（每一步都留痕，不做静默替换）：
    #   ① 给了 request_id → 按它查判读；
    #   ② 查不到（或没给）→ 按 **会话** 取最近一次判读（医生刚看过的就是它）；
    #   ③ 两者都拿不到 → 404：这条会话里确实没有判读结果可挂。
    diagnosis = None
    resolved_by_session = False
    if request.request_id:
        diagnosis = await diagnosis_repo.get_result(request.request_id)
    if not diagnosis and request.session_id:
        # 只认「真的返回了列表且首项确有 request_id」的结果 —— 兼容测试替身/旧签名，
        # 不让 Mock 的返回值被当成"查到了一次判读"（那会把 404 悄悄变成 200）。
        candidates = await diagnosis_repo.get_results_by_session(request.session_id, limit=1)
        if (
            isinstance(candidates, (list, tuple))
            and candidates
            and getattr(candidates[0], "request_id", None)
        ):
            diagnosis = candidates[0]
            resolved_by_session = True
    if not diagnosis:
        raise NotFoundException(
            error_message=(
                f"判读请求不存在：{request.request_id or '（未提供）'}"
                f"（会话 {request.session_id or '-'} 也没有判读结果可关联）"
            )
        )

    effective_request_id = str(getattr(diagnosis, "request_id", "") or request.request_id)
    if resolved_by_session:
        logger.warning(
            "feedback_request_id_resolved_by_session",
            given_request_id=request.request_id,
            session_id=request.session_id,
            resolved_request_id=effective_request_id,
        )

    if request.feedback_type == FeedbackType.RULE_CHANGE and request.feedback_level != FeedbackLevel.L3:
        raise BadRequestException(
            error_code="E1001",
            error_message="rule_change类型反馈必须使用L3级别",
        )

    reviewer_id = get_actor_id(current_user)

    feedback = await feedback_repo.create_feedback(
        request_id=effective_request_id,
        session_id=request.session_id,
        feedback_type=request.feedback_type.value,
        feedback_level=request.feedback_level.value,
        reviewer_id=reviewer_id,
        content=request.content.model_dump(),
    )

    response_data = FeedbackSubmitResponse(
        status="accepted",
        message="反馈已提交",
        # 回传**实际挂载**的判读 id（客户端据此显示"已关联判读 xxx"，不静默替换）
        request_id=effective_request_id,
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
