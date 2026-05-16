"""反馈管理Repository"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid
import structlog

from app.models.database import FeedbackRecord

logger = structlog.get_logger()


class FeedbackRepository:
    """反馈管理Repository"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def _generate_feedback_id(self) -> str:
        """生成反馈ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"fb_{timestamp}_{uuid.uuid4().hex[:8]}"
    
    async def create_feedback(
        self,
        request_id: str,
        session_id: str,
        feedback_type: str,
        feedback_level: str,
        reviewer_id: str,
        content: Dict[str, Any],
    ) -> FeedbackRecord:
        """创建反馈记录"""
        feedback_id = self._generate_feedback_id()
        now = datetime.now(timezone.utc)
        
        feedback = FeedbackRecord(
            feedback_id=feedback_id,
            request_id=request_id,
            session_id=session_id,
            feedback_type=feedback_type,
            feedback_level=feedback_level,
            reviewer_id=reviewer_id,
            content=content,
            created_at=now,
        )
        
        self.db.add(feedback)
        await self.db.flush()
        
        logger.info(
            "feedback_created",
            feedback_id=feedback_id,
            request_id=request_id,
            feedback_type=feedback_type,
            feedback_level=feedback_level,
        )
        
        return feedback
    
    async def get_feedback(self, feedback_id: str) -> Optional[FeedbackRecord]:
        """获取反馈记录"""
        result = await self.db.execute(
            select(FeedbackRecord).where(FeedbackRecord.feedback_id == feedback_id)
        )
        return result.scalar_one_or_none()
    
    async def get_feedbacks_by_request(
        self,
        request_id: str,
        limit: int = 100,
    ) -> list:
        """获取请求的反馈列表"""
        result = await self.db.execute(
            select(FeedbackRecord)
            .where(FeedbackRecord.request_id == request_id)
            .order_by(FeedbackRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
    
    async def get_feedbacks_by_session(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list:
        """获取会话的反馈列表"""
        result = await self.db.execute(
            select(FeedbackRecord)
            .where(FeedbackRecord.session_id == session_id)
            .order_by(FeedbackRecord.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
