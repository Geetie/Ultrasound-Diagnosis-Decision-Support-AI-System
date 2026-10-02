"""FastAPI 依赖注入器集合。

集中管理所有 ``Depends()`` 注入的工厂函数。每个函数从 ``app.state`` 中
取出 lifespan 启动期实例化的对象，类型化为对应 ABC。

wiring-audit P1-4（2026-10-02）：删除了 8 个全库零 ``Depends`` 引用的
死工厂（get_inference_engine/get_gpu_schedule/get_dispatcher/
get_knowledge_agent/get_monitor_agent/get_fusion_engine/get_rag_service/
get_safety_gateway_client）——业务层实际经
``diagnosis_pipeline`` 的 ``getattr(app.state, ...)`` 兜底获取这些对象。
若未来需要恢复 DI，请连同调用方一起接线，不要只留无人消费的工厂。
"""
from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db_session
from app.repositories import (
    ConsentRepository,
    DeviceRepository,
    DiagnosisRepository,
    FeedbackRepository,
    ReportRepository,
    SessionRepository,
)
from app.services.audit_service import AuditService
from app.speech.protocols import SpeechEngine

# ----------------------------------------------------------------------
# Engine / Speech
# ----------------------------------------------------------------------


def get_speech_engine(request: Request) -> SpeechEngine:
    return request.app.state.speech_engine


# ----------------------------------------------------------------------
# Repository / Service
# ----------------------------------------------------------------------


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


def get_report_repo(db: AsyncSession = Depends(get_db_session)) -> ReportRepository:
    """获取诊断报告仓储（T-GB-API-02）。"""
    return ReportRepository(db)


def get_audit_service(db: AsyncSession = Depends(get_db_session)) -> AuditService:
    return AuditService(db)
