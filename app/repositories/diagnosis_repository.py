"""诊断结果Repository"""
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid
import structlog

from app.models.database import DiagnosisResult
from app.models.schemas import ResponseTier

logger = structlog.get_logger()


class DiagnosisRepository:
    """诊断结果Repository"""
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    def _generate_request_id(self) -> str:
        """生成请求ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"req_{timestamp}_{uuid.uuid4().hex[:8]}"
    
    async def get_result(self, request_id: str) -> Optional[DiagnosisResult]:
        """根据请求ID获取诊断结果"""
        result = await self.db.execute(
            select(DiagnosisResult).where(DiagnosisResult.request_id == request_id)
        )
        return result.scalar_one_or_none()
    
    async def create_request(
        self,
        session_id: str,
        organ: str,
        response_tier: ResponseTier,
        patient_context: Optional[Dict[str, Any]] = None,
        image_ids: Optional[list] = None,
        clinical_text: Optional[str] = None,
    ) -> str:
        """
        创建诊断请求（在队列系统中实际使用）
        
        这里先只生成request_id，实际的诊断处理会在异步任务中完成
        
        Returns:
            str: request_id
        """
        request_id = self._generate_request_id()
        
        # 创建一个初步的诊断结果记录（状态为processing）
        diagnosis_result = DiagnosisResult(
            request_id=request_id,
            session_id=session_id,
            organ=organ,
            tier=response_tier.value,
            # 临床文本必须落库：后台管线以它作为 AgentContext.user_query，
            # 此前只写 findings={"status": "processing"} 导致文本丢失。
            clinical_text=clinical_text,
            confidence=0.0,  # 初始置信度
            findings={"status": "processing"},
            status="processing",
            created_at=datetime.now(timezone.utc),
        )
        
        self.db.add(diagnosis_result)
        await self.db.flush()
        
        logger.info(
            "diagnosis_request_created",
            request_id=request_id,
            session_id=session_id,
            organ=organ,
        )
        
        return request_id
    
    async def update_result(
        self,
        request_id: str,
        status: str,
        confidence: float,
        findings: Dict[str, Any],
        recommendations: Optional[str] = None,
        knowledge_ids: Optional[list] = None,
    ) -> Optional[DiagnosisResult]:
        """更新诊断结果"""
        result = await self.get_result(request_id)
        if not result:
            return None
        
        result.status = status
        result.confidence = confidence
        result.findings = findings
        result.recommendations = recommendations
        result.knowledge_ids = knowledge_ids
        
        await self.db.flush()
        
        logger.info(
            "diagnosis_result_updated",
            request_id=request_id,
            status=status,
            confidence=confidence,
        )
        
        return result
    
    async def update_findings(
        self,
        request_id: str,
        findings: Dict[str, Any],
    ) -> bool:
        """仅更新 ``findings``（不动 status / confidence）。

        T-GB-AGT-02 医生确认回写用：确认结果需要合并进既有 findings
        （内含 ``routing``），但不得改变诊断本身的 status 与置信度。
        """
        result = await self.get_result(request_id)
        if not result:
            return False
        result.findings = findings
        await self.db.flush()
        logger.info("diagnosis_findings_updated", request_id=request_id)
        return True

    async def update_tier(self, request_id: str, tier: str) -> bool:
        """更新请求档位（T-GB-AGT-02 医生确认升级后按全量档位重跑）。"""
        result = await self.get_result(request_id)
        if not result:
            return False
        result.tier = tier
        await self.db.flush()
        logger.info("diagnosis_tier_updated", request_id=request_id, tier=tier)
        return True

    async def get_results_by_session(
        self,
        session_id: str,
        limit: int = 100,
    ) -> list:
        """获取会话的诊断结果列表"""
        result = await self.db.execute(
            select(DiagnosisResult)
            .where(DiagnosisResult.session_id == session_id)
            .order_by(DiagnosisResult.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
