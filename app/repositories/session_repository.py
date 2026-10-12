"""会话管理Repository"""
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import DiagnosisSession, Patient
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
        knowledge_base_version: str | None = None,
    ) -> DiagnosisSession:
        # A.52 口径修复：此前默认硬编码 "kb_v1.0.0"（Phase 0A 占位），KB 切到
        # 生产版本后仍写旧值 → 会话/报告标注的知识库版本与实际检索来源不符。
        # 现缺省取 settings.rag_kb_version（生产真值），调用方显式传入时以其为准。
        """创建新的诊断会话"""
        if not knowledge_base_version:
            # 局部导入：保持仓储层对配置的依赖只在此一处、且不参与模块导入期
            from app.config.settings import settings

            knowledge_base_version = settings.rag_kb_version

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

    async def get_patient_demographics(self, patient_id: str) -> dict | None:
        """取患者脱敏源数据（原始姓名/性别/出生日期）——脱敏在 API 层做。

        A.36 P0：工作区身份必须数据驱动。patients 表无行时返回 None
        （演练患者常只有 consent 行），前端显示『患者信息不可用』，不猜不占位。
        """
        stmt = select(Patient).where(Patient.patient_id == patient_id)
        result = await self.db.execute(stmt)
        patient = result.scalar_one_or_none()
        if not patient:
            return None
        return {
            "name": patient.name,
            "gender": patient.gender,
            "birth_date": patient.birth_date,
        }

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

    async def list_sessions(self, limit: int = 20) -> list[dict]:
        """工作列表：按创建时间倒序取最近会话，联表 patients 带出人口学信息。

        设计稿 01_工作列表需要「患者标识 / 部位 / 时间 / 状态 / 剩余分钟」，
        人口学在patients 表，必须 JOIN。返回 dict 而非 ORM 对象——
        列表行要做脱敏与派生（年龄、剩余分钟），不适合直接序列化模型。

        不在此处按状态过滤：分组（进行中 / 今日待处理）由 API 层做，
        契约 P2 要求只分两组、不按七种状态切页。
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

        result = await self.db.execute(stmt)
        keys = [
            "session_id", "patient_id", "status", "target_organs",
            "organ_statuses", "created_at", "updated_at",
            "name", "gender", "birth_date",
        ]
        return [dict(zip(keys, row)) for row in result.all()]

    async def renew_session(self, session_id: str) -> DiagnosisSession | None:
        """续期会话TTL（更新updated_at时间戳）"""
        session = await self.get_session(session_id)
        if not session:
            return None
        session.updated_at = datetime.now(timezone.utc)
        await self.db.flush()
        logger.info("session_renewed", session_id=session_id)
        return session
