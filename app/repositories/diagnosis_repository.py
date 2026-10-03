"""诊断结果Repository"""
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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

    async def get_result(self, request_id: str) -> DiagnosisResult | None:
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
        patient_context: dict[str, Any] | None = None,
        image_ids: list | None = None,
        clinical_text: str | None = None,
        request_id: str | None = None,
    ) -> str:
        """
        创建诊断请求（在队列系统中实际使用）

        Args:
            request_id: 由调用方指定的请求 ID。**必须与入队、响应、后续查询用的是
                同一个 ID** —— 不传时本方法自行生成（仅适合脱离端点的调用）。

        Returns:
            str: request_id

        ## 为什么要有这个参数（2026-09-28 真机链路冒烟暴露的阻断级缺陷）

        端点在调用本方法**之前**就用 `_generate_request_id()` 生成了 `request_id`，
        并把它用于"入队 + 响应 + 后续按 id 查询"；而本方法内部**又生成了一个**并落库。
        两个 ID 从此分叉：

            22:47:02 [error] pipeline_request_not_found  request_id=req_..._bb033915
            22:47:02 [info]  diagnosis_request_created    request_id=req_..._e3e87f1d

        队列里传的是 ①，库里存的是 ② → worker 永远找不到请求行 →
        请求永久停在 `status=processing`、结果接口一直 404。
        （端点里 `created_id` 从未被使用，正是这个分叉留下的痕迹。）
        """
        if request_id is None:
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
            # 患者上下文必须落库：后台管线据此把 `conditions` 转发给安全网关做禁忌判定。
            # 此前这个入参**收了就丢**（模型无对应列），导致禁忌判定永远拿不到条件数据
            # → 每次诊断都保守判"命中禁忌" → 恒 requires_manual_review。
            patient_context=patient_context,
            # 影像标识必须落库：结节分割等影像侧增强要用它找回 DICOM。
            # 此前同样是"收了就丢"（模型连列都没有），管线完全拿不到影像。
            image_ids=list(image_ids) if image_ids else None,
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
        findings: dict[str, Any],
        recommendations: str | None = None,
        knowledge_ids: list | None = None,
    ) -> DiagnosisResult | None:
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
        findings: dict[str, Any],
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
