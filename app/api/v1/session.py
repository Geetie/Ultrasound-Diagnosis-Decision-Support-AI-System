from fastapi import APIRouter, Depends
from datetime import datetime, timezone
from typing import Optional

from app.models.schemas import (
    SessionCreateRequest,
    SessionResponse,
    SessionUpdateRequest,
    SessionStatus,
    OrganStatus,
    ConsentStatus,
)
from app.api.v1.errors import NotFoundException
from app.dependencies import get_session_repo, get_consent_repo, get_audit_service
from app.api.v1.utils import get_actor_id, check_consent_status
from app.repositories import SessionRepository, ConsentRepository
from app.services.audit_service import AuditService
from app.security.auth import get_current_active_user

router = APIRouter(tags=["sessions"])

SESSION_TTL_SECONDS = 7200


def _calc_ttl_remaining(updated_at: Optional[datetime]) -> Optional[int]:
    if not updated_at:
        return None
    elapsed = (datetime.now(timezone.utc) - updated_at).total_seconds()
    return max(0, int(SESSION_TTL_SECONDS - elapsed))


def _build_session_response(session) -> SessionResponse:
    return SessionResponse(
        session_id=session.session_id,
        patient_id=session.patient_id,
        device_id=session.device_id,
        status=SessionStatus(session.status),
        target_organs=session.target_organs,
        organ_statuses={k: OrganStatus(v) for k, v in session.organ_statuses.items()},
        knowledge_base_version=session.knowledge_base_version,
        created_at=session.created_at,
        updated_at=session.updated_at,
        consent_status_at_creation=ConsentStatus(session.consent_status_at_creation),
        ttl_remaining_seconds=_calc_ttl_remaining(session.updated_at),
    )


@router.post("", response_model=SessionResponse)
async def create_session(
    request: SessionCreateRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    consent_repo: ConsentRepository = Depends(get_consent_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    consent_record = await consent_repo.get_latest_consent(request.patient_id)
    consent_status = check_consent_status(consent_record, request.patient_id)

    session = await session_repo.create_session(
        patient_id=request.patient_id,
        device_id=request.device_id,
        target_organs=request.target_organs,
        consent_status=consent_status,
    )

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="session_create",
        actor=actor_id,
        session_id=session.session_id,
        target_type="patient",
        target_id=request.patient_id,
        detail={
            "device_id": request.device_id,
            "target_organs": request.target_organs,
            "consent_status": consent_status.value,
        },
    )

    return _build_session_response(session)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: str,
    session_repo: SessionRepository = Depends(get_session_repo),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    session = await session_repo.get_session(session_id)
    if not session:
        raise NotFoundException(error_message=f"Session not found: {session_id}")

    return _build_session_response(session)


@router.patch("/{session_id}", response_model=SessionResponse)
async def update_session(
    session_id: str,
    request: SessionUpdateRequest,
    session_repo: SessionRepository = Depends(get_session_repo),
    audit_service: AuditService = Depends(get_audit_service),
    current_user: Optional[dict] = Depends(get_current_active_user),
):
    session = await session_repo.update_session(
        session_id=session_id,
        status=request.status,
        organ_statuses=request.organ_statuses,
    )

    if not session:
        raise NotFoundException(error_message=f"Session not found: {session_id}")

    actor_id = get_actor_id(current_user)
    await audit_service.log_action(
        action="session_update",
        actor=actor_id,
        session_id=session_id,
        detail={
            "status": request.status.value if request.status else None,
            "organ_statuses": request.organ_statuses,
        },
    )

    return _build_session_response(session)
