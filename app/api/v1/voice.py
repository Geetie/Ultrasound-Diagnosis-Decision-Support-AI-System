
import structlog
from fastapi import APIRouter, Depends

from app.api.v1.errors import BadRequestException, NotFoundException
from app.api.v1.utils import check_consent_status, get_actor_id
from app.api.websocket.nav import manager as nav_manager
from app.dependencies import (
    get_audit_service,
    get_consent_repo,
    get_diagnosis_repo,
    get_session_repo,
)
from app.models.schemas import ResponseTier, VoiceConfirmRequest, VoiceConfirmResponse
from app.repositories import DiagnosisRepository, SessionRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService
from app.services.diagnosis_worker import enqueue_diagnosis

logger = structlog.get_logger()
router = APIRouter(tags=["voice"])


@router.post("/confirm", response_model=VoiceConfirmResponse)
async def voice_confirm(
    request: VoiceConfirmRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    diag_repo: DiagnosisRepository = Depends(get_diagnosis_repo),
    consent_repo=Depends(get_consent_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"检查会话不存在：{request.session_id}")

    if not request.confirmed:
        actor_id = get_actor_id(current_user)
        await audit_service.log_action(
            action="voice_confirm_rejected",
            actor=actor_id,
            session_id=request.session_id,
            target_type="voice_command",
            target_id=request.command_type,
            detail={
                "transcript": request.transcript,
                "command_type": request.command_type,
                "confirmed": False,
            },
        )

        return VoiceConfirmResponse(
            session_id=request.session_id,
            executed=False,
            action_taken="rejected_by_user",
        )

    action_taken = None
    request_id = None

    if request.command_type == "diagnosis_trigger":
        if not request.organ:
            raise BadRequestException(
                error_code="E1001",
                error_message="语音指令「发起判读」必须指定检查部位（organ）",
            )
        # 语音触发与 POST /diagnosis/request 同一不变量（A.49 摸排发现两处断裂）：
        # ①患者同意门必须先过（原实现完全绕开 consent 校验）；
        # ②create_request 签名是 (session_id, organ, response_tier, ...)——原实现
        #   传了不存在的 patient_id 形参，语音触发判读 100% TypeError→500；
        # ③创建后必须 commit + 入队（create_request 只 flush，worker 用另一个
        #   会话读库，不提交就是 pipeline_request_not_found / 永久 processing，
        #   见 diagnosis.py L229-240 的既有教训注释）。
        consent_record = await consent_repo.get_latest_consent(session.patient_id)
        check_consent_status(consent_record, session.patient_id)
        request_id = await diag_repo.create_request(
            session_id=request.session_id,
            organ=request.organ,
            response_tier=ResponseTier.FULL,
        )
        # create_request 返回 request_id；入队前先提交，行对 worker 可见
        await diag_repo.db.commit()
        try:
            await enqueue_diagnosis(request_id)
        except Exception as exc:
            # 入队失败不阻塞端点——worker 池有 fallback，与主端点同语义
            logger.warning(
                "voice_diagnosis_enqueue_failed",
                request_id=request_id,
                error=str(exc),
            )
        action_taken = "diagnosis_initiated"

    elif request.command_type == "grade_change":
        if not request.grade_target:
            raise BadRequestException(
                error_code="E1001",
                error_message="语音指令「修改分级」必须指定分级目标（grade_target）",
            )
        action_taken = f"grade_changed_to_{request.grade_target}"

    elif request.command_type == "navigation_control":
        if not request.nav_action:
            raise BadRequestException(
                error_code="E1001",
                error_message="语音指令「导航控制」必须指定动作（nav_action）",
            )
        try:
            await nav_manager.send_personal_message(
                {
                    "type": "navigation_control",
                    "action": request.nav_action,
                    "session_id": request.session_id,
                },
                request.session_id,
            )
        except Exception as e:
            logger.warning("voice_navigation_control_send_failed", error=str(e), session_id=request.session_id)
        action_taken = f"navigation_{request.nav_action}"

    else:
        raise BadRequestException(
            error_code="E1001",
            error_message=f"不支持的语音指令类型：{request.command_type}",
        )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="voice_confirm_accepted",
        actor=actor_id,
        session_id=request.session_id,
        target_type="voice_command",
        target_id=request.command_type,
        detail={
            "transcript": request.transcript,
            "command_type": request.command_type,
            "confirmed": True,
            "action_taken": action_taken,
            "request_id": request_id,
        },
    )

    logger.info(
        "Voice command confirmed",
        session_id=request.session_id,
        command_type=request.command_type,
        action_taken=action_taken,
    )

    return VoiceConfirmResponse(
        session_id=request.session_id,
        executed=True,
        action_taken=action_taken,
        request_id=request_id,
    )
