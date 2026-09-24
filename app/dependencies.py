"""FastAPI 依赖注入器集合。

集中管理所有 ``Depends()`` 注入的工厂函数。每个函数从 ``app.state`` 中
取出 lifespan 启动期实例化的对象，类型化为对应 ABC。

T-GA-INT-01 新增：dispatcher / fusion_engine / rag_service /
knowledge_agent / monitor_agent / safety_gateway_client 的 DI 注入。
"""
from fastapi import Request, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.protocols import (
    DispatcherAgent,
    KnowledgeAgent,
    MonitorAgent,
)
from app.fusion.protocols import FusionEngine
from app.inference.gpu_monitor import GPUScheduleStrategy
from app.inference.protocols import InferenceEngine
from app.speech.protocols import SpeechEngine
from app.rag.protocols import RAGService
from app.config.database import get_db_session
from app.repositories import (
    SessionRepository,
    ConsentRepository,
    DiagnosisRepository,
    DeviceRepository,
    FeedbackRepository,
    ReportRepository,
)
from app.services.audit_service import AuditService
from app.services.safety_gateway_client import SafetyGatewayClient, safety_gateway_client


# ----------------------------------------------------------------------
# Engine / GPU / Speech
# ----------------------------------------------------------------------


def get_inference_engine(request: Request) -> InferenceEngine:
    return request.app.state.inference_engine


def get_speech_engine(request: Request) -> SpeechEngine:
    return request.app.state.speech_engine


def get_gpu_schedule(request: Request) -> GPUScheduleStrategy:
    return request.app.state.gpu_schedule


# ----------------------------------------------------------------------
# AGT 智能体（T-GA-AGT-01/02/03/07）
# ----------------------------------------------------------------------


def get_dispatcher(request: Request) -> DispatcherAgent:
    """获取中央调度智能体（lifespan 启动期实例化）。"""
    return request.app.state.dispatcher


def get_knowledge_agent(request: Request) -> KnowledgeAgent:
    """获取知识查询智能体。"""
    return request.app.state.knowledge_agent


def get_monitor_agent(request: Request) -> MonitorAgent:
    """获取流程监控智能体。"""
    return request.app.state.monitor_agent


# ----------------------------------------------------------------------
# FUS 融合层（T-GA-FUS-01/02）
# ----------------------------------------------------------------------


def get_fusion_engine(request: Request) -> FusionEngine:
    """获取融合层引擎（T-GA-FUS-01/02）。"""
    return request.app.state.fusion_engine


# ----------------------------------------------------------------------
# RAG 服务（T-GA-RAG-01/02/03）
# ----------------------------------------------------------------------


def get_rag_service(request: Request) -> RAGService:
    """获取 RAG 服务实例（best-effort 初始化，可能为 None）。"""
    return getattr(request.app.state, "rag_service", None)


# ----------------------------------------------------------------------
# SafetyGateway 客户端
# ----------------------------------------------------------------------


def get_safety_gateway_client() -> SafetyGatewayClient:
    """获取 SafetyGateway 客户端单例。"""
    return safety_gateway_client


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
