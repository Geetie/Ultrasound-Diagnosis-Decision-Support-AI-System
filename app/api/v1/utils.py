
import structlog

from app.api.v1.errors import PatientConsentWithdrawnException, PatientNotConsentedException
from app.models.schemas import ConsentStatus

logger = structlog.get_logger()


def get_actor_id(current_user: dict | None) -> str:
    if not current_user:
        return "system"
    return current_user.get("user_id", "system")


def check_consent_status(consent_record, patient_id: str) -> ConsentStatus:
    consent_status = (
        ConsentStatus(consent_record.status)
        if consent_record
        else ConsentStatus.DENIED
    )

    if consent_status == ConsentStatus.WITHDRAWN:
        raise PatientConsentWithdrawnException(
            error_message=f"患者 {patient_id} 已撤回知情同意",
            detail="consent_withdrawn",
        )

    if consent_status != ConsentStatus.GRANTED:
        raise PatientNotConsentedException(
            error_message=f"患者 {patient_id} 未签署知情同意书",
            detail=f"consent_{consent_status.value}",
        )

    return consent_status


async def try_renew_session(session_repo, session_id: str) -> None:
    try:
        await session_repo.renew_session(session_id)
    except Exception as e:
        logger.warning("session_renew_failed", session_id=session_id, error=str(e))
