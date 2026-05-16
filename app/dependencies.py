from fastapi import Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.inference.gpu_monitor import GPUScheduleStrategy
from app.inference.protocols import InferenceEngine
from app.speech.protocols import SpeechEngine
from app.config.database import get_db_session
from app.repositories import SessionRepository, ConsentRepository, DiagnosisRepository, DeviceRepository, FeedbackRepository
from app.services.audit_service import AuditService


def get_inference_engine(request: Request) -> InferenceEngine:
    return request.app.state.inference_engine


def get_speech_engine(request: Request) -> SpeechEngine:
    return request.app.state.speech_engine


def get_gpu_schedule(request: Request) -> GPUScheduleStrategy:
    return request.app.state.gpu_schedule


def get_session_repo(db: AsyncSession = Depends(get_db_session)) -> SessionRepository:
    return SessionRepository(db)


def get_consent_repo(db: AsyncSession = Depends(get_db_session)) -> ConsentRepository:
    return ConsentRepository(db)


def get_diagnosis_repo(db: AsyncSession = Depends(get_db_session)) -> DiagnosisRepository:
    return DiagnosisRepository(db)


def get_device_repo(db: AsyncSession = Depends(get_db_session)) -> DeviceRepository:
    return DeviceRepository(db)


def get_feedback_repo(db: AsyncSession = Depends(get_db_session)) -> FeedbackRepository:
    return FeedbackRepository(db)


def get_audit_service(db: AsyncSession = Depends(get_db_session)) -> AuditService:
    return AuditService(db)
