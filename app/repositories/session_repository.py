"""会话管理Repository"""
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import DiagnosisSession
from app.models.schemas import ConsentStatus, OrganStatus, SessionStatus

logger = structlog.get_logger()


class SessionRepository:
    """会话管理Repository"""

    def __init__(self, db: AsyncSession):
        self.db = db

    def _generate_session_id(self) -> str:
        """生成会话ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"sess_{timestamp}_{uuid.uuid4().hex[:8]}"

    async def get_session(self, session_id: str) -> DiagnosisSession | None:
        """根据会话ID获取会话"""
        result = await self.db.execute(
            select(DiagnosisSession).where(DiagnosisSession.session_id == session_id)
        )
        return result.scalar_one_or_none()

    async def create_session(
        self,
        patient_id: str,
        device_id: str,
        target_organs: list[str],
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
        status: SessionStatus | None = None,
        organ_statuses: dict | None = None,
    ) -> DiagnosisSession | None:
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
    ) -> list[DiagnosisSession]:
        """获取患者的会话列表"""
        result = await self.db.execute(
            select(DiagnosisSession)
            .where(DiagnosisSession.patient_id == patient_id)
            .order_by(DiagnosisSession.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def renew_session(self, session_id: str) -> DiagnosisSession | None:
        """续期会话TTL（更新updated_at时间戳）"""
        session = await self.get_session(session_id)
        if not session:
            return None
        session.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        logger.info("session_renewed", session_id=session_id)
        return session
