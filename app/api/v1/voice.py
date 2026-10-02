
import structlog
from fastapi import APIRouter, Depends

from app.api.v1.errors import BadRequestException, NotFoundException
from app.api.v1.utils import get_actor_id
from app.api.websocket.nav import manager as nav_manager
from app.dependencies import get_audit_service, get_diagnosis_repo, get_session_repo
from app.models.schemas import VoiceConfirmRequest, VoiceConfirmResponse
from app.repositories import DiagnosisRepository, SessionRepository
from app.security.auth import get_current_active_user
from app.services.audit_service import AuditService

logger = structlog.get_logger()
router = APIRouter(tags=["voice"])


@router.post("/confirm", response_model=VoiceConfirmResponse)
async def voice_confirm(
    request: VoiceConfirmRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    diag_repo: DiagnosisRepository = Depends(get_diagnosis_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: dict | None = Depends(get_current_active_user),
):
    session = await session_repo.get_session(request.session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {request.session_id}")

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
                error_message="organ is required for diagnosis_trigger command",
            )
        diag_request = await diag_repo.create_request(
            session_id=request.session_id,
            patient_id=session.patient_id,
            organ=request.organ,
        )
        action_taken = "diagnosis_initiated"
        request_id = diag_request.request_id

    elif request.command_type == "grade_change":
        if not request.grade_target:
            raise BadRequestException(
                error_code="E1001",
                error_message="grade_target is required for grade_change command",
            )
        action_taken = f"grade_changed_to_{request.grade_target}"

    elif request.command_type == "navigation_control":
        if not request.nav_action:
            raise BadRequestException(
                error_code="E1001",
                error_message="nav_action is required for navigation_control command",
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
            error_message=f"Unsupported command type: {request.command_type}",
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
