"""会话管理Repository"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import Optional, List
from datetime import datetime, timezone
import uuid
import structlog

from app.models.database import DiagnosisSession, Patient
from app.models.schemas import SessionStatus, OrganStatus, ConsentStatus

logger = structlog.get_logger()


class SessionRepository:
    """会话管理Repository"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def _generate_session_id(self) -> str:
        """生成会话ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"sess_{timestamp}_{uuid.uuid4().hex[:8]}"
    
    async def get_session(self, session_id: str) -> Optional[DiagnosisSession]:
        """根据会话ID获取会话"""
        result = await self.db.execute(
            select(DiagnosisSession).where(DiagnosisSession.session_id == session_id)
        )
        return result.scalar_one_or_none()
    
    async def create_session(
        self,
        patient_id: str,
        device_id: str,
        target_organs: List[str],
        consent_status: ConsentStatus,
        knowledge_base_version: str = "kb_v1.0.0",
    ) -> DiagnosisSession:
        """创建新的诊断会话"""
        now = datetime.now(timezone.utc)
        session_id = self._generate_session_id()
        
        # 初始化器官状态
        organ_statuses = {
            organ: OrganStatus.PENDING.value for organ in target_organs
        }
        
        session = DiagnosisSession(
            session_id=session_id,
            patient_id=patient_id,
            device_id=device_id,
            status=SessionStatus.PENDING.value,
            target_organs=target_organs,
            organ_statuses=organ_statuses,
            knowledge_base_version=knowledge_base_version,
            consent_status_at_creation=consent_status.value,
            created_at=now,
            updated_at=now,
        )
        
        self.db.add(session)
        await self.db.flush()
        
        logger.info(
            "session_created",
            session_id=session_id,
            patient_id=patient_id,
            device_id=device_id,
        )
        
        return session
    
    async def update_session(
        self,
        session_id: str,
        status: Optional[SessionStatus] = None,
        organ_statuses: Optional[dict] = None,
    ) -> Optional[DiagnosisSession]:
        """更新会话状态"""
        session = await self.get_session(session_id)
        if not session:
            return None
        
        now = datetime.now(timezone.utc)
        
        if status:
            session.status = status.value
        
        if organ_statuses:
            session.organ_statuses = organ_statuses
        
        session.updated_at = now
        
        await self.db.flush()
        
        logger.info(
            "session_updated",
            session_id=session_id,
            status=status.value if status else None,
        )
        
        return session
    
    async def get_sessions_by_patient(
        self,
        patient_id: str,
        limit: int = 100,
    ) -> List[DiagnosisSession]:
        """获取患者的会话列表"""
        result = await self.db.execute(
            select(DiagnosisSession)
            .where(DiagnosisSession.patient_id == patient_id)
            .order_by(DiagnosisSession.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_sessions(
        self,
        statuses: Optional[List[str]] = None,
        limit: int = 20,
    ) -> List[dict]:
        """工作列表：按创建时间倒序取最近会话，联表 patients 带出人口学信息。

        设计稿 01_工作列表需要「患者标识 / 部位 / 时间 / 状态 / 剩余分钟」，
        人口学信息在 `patients` 表，必须 JOIN 才能拿到。

        返回 dict（含 patient_* 字段）而不是 ORM 对象 —— 列表行要做脱敏与派生
        （年龄、剩余分钟），不适合直接序列化模型。

        `statuses` 为空表示不过滤。注意默认**不含** terminated/archived 类终态，
        由调用方显式传入 —— 工作列表只显示待处理与进行中。
        """
        stmt = (
            select(
                DiagnosisSession.session_id,
                DiagnosisSession.patient_id,
                DiagnosisSession.status,
                DiagnosisSession.target_organs,
                DiagnosisSession.organ_statuses,
                DiagnosisSession.created_at,
                DiagnosisSession.updated_at,
                Patient.name,
                Patient.gender,
                Patient.birth_date,
            )
            .select_from(DiagnosisSession)
            .outerjoin(Patient, Patient.patient_id == DiagnosisSession.patient_id)
            .order_by(DiagnosisSession.created_at.desc())
            .limit(limit)
        )
        if statuses:
            stmt = stmt.where(DiagnosisSession.status.in_(statuses))

        result = await self.db.execute(stmt)
        keys = [
            "session_id", "patient_id", "status", "target_organs",
            "organ_statuses", "created_at", "updated_at",
            "name", "gender", "birth_date",
        ]
        return [dict(zip(keys, row)) for row in result.all()]

    async def renew_session(self, session_id: str) -> Optional[DiagnosisSession]:
        """续期会话TTL（更新updated_at时间戳）"""
        session = await self.get_session(session_id)
        if not session:
            return None
        session.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        logger.info("session_renewed", session_id=session_id)
        return session
