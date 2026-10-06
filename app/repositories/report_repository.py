"""T-GB-API-02 — 诊断报告仓储。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import DiagnosisReport

logger = structlog.get_logger("app.repositories.report")


class ReportRepository:
    """报告读写（草稿 / 签名 / PACS 推送状态）。"""

    def __init__(self, db: AsyncSession):
        self.db = db

    @staticmethod
    def _generate_report_id() -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d")
        return f"rep_{timestamp}_{uuid.uuid4().hex[:8]}"

    async def create_report(
        self,
        *,
        request_id: str,
        content: dict[str, Any],
        rendered: str,
        content_digest: str,
        template_version: str,
        session_id: str | None = None,
        patient_id: str | None = None,
        organ: str | None = None,
        report_id: str | None = None,
    ) -> DiagnosisReport:
        """落一份草稿报告。"""
        report = DiagnosisReport(
            report_id=report_id or self._generate_report_id(),
            request_id=request_id,
            session_id=session_id,
            patient_id=patient_id,
            organ=organ,
            status="draft",
            content=content,
            rendered=rendered,
            content_digest=content_digest,
            template_version=template_version,
            pacs_status="pending",
            pacs_attempts=0,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(report)
        await self.db.flush()
        logger.info(
            "report_draft_created",
            report_id=report.report_id,
            request_id=request_id,
        )
        return report

    async def get_report(self, report_id: str) -> DiagnosisReport | None:
        result = await self.db.execute(
            select(DiagnosisReport).where(DiagnosisReport.report_id == report_id)
        )
        return result.scalar_one_or_none()

    async def get_latest_by_request(self, request_id: str) -> DiagnosisReport | None:
        result = await self.db.execute(
            select(DiagnosisReport)
            .where(DiagnosisReport.request_id == request_id)
            .order_by(DiagnosisReport.created_at.desc(), DiagnosisReport.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_by_request(
        self, request_id: str, limit: int = 50
    ) -> list[DiagnosisReport]:
        result = await self.db.execute(
            select(DiagnosisReport)
            .where(DiagnosisReport.request_id == request_id)
            .order_by(DiagnosisReport.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def update_sections(
        self,
        report_id: str,
        *,
        content: dict[str, Any],
        rendered: str,
        content_digest: str,
    ) -> DiagnosisReport | None:
        """医生补录后更新草稿正文（A.32）。

        只在仓储层做字段赋值与落库，**不校验**状态/键白名单——那些是
        服务层的职责（谁能改、改哪些键）；仓储保持"给什么写什么"的窄语义。
        """
        report = await self.get_report(report_id)
        if report is None:
            return None
        report.content = content
        report.rendered = rendered
        report.content_digest = content_digest
        await self.db.flush()
        logger.info("report_sections_updated", report_id=report_id)
        return report

    async def mark_signed(
        self,
        report_id: str,
        *,
        signature: str,
        signing_key_name: str,
        signed_by: str | None = None,
    ) -> DiagnosisReport | None:
        """写入数字签名并置为 signed。"""
        report = await self.get_report(report_id)
        if report is None:
            return None
        report.signature = signature
        report.signing_key_name = signing_key_name
        report.signed_at = datetime.now(timezone.utc)
        report.signed_by = signed_by
        report.status = "signed"
        await self.db.flush()
        logger.info("report_signed", report_id=report_id, key=signing_key_name)
        return report

    async def mark_pacs_result(
        self,
        report_id: str,
        *,
        adapter: str,
        success: bool,
        message: str = "",
        attempts: int = 1,
        skipped: bool = False,
    ) -> DiagnosisReport | None:
        """写入 PACS/HIS 推送结果。

        推送失败**不改变**报告本身的 signed 状态（签名已生效，重推即可），
        仅记录 ``pacs_status`` / ``pacs_message`` / ``pacs_attempts``。
        """
        report = await self.get_report(report_id)
        if report is None:
            return None
        report.pacs_adapter = adapter
        report.pacs_attempts = int(report.pacs_attempts or 0) + max(1, attempts)
        if skipped:
            report.pacs_status = "skipped"
        else:
            report.pacs_status = "success" if success else "failed"
        report.pacs_message = message[:2000] if message else None
        if success and not skipped:
            report.pushed_at = datetime.now(timezone.utc)
            report.status = "pushed"
        await self.db.flush()
        logger.info(
            "report_pacs_result_recorded",
            report_id=report_id,
            adapter=adapter,
            success=success,
            skipped=skipped,
            attempts=attempts,
        )
        return report

    async def mark_failed(
        self, report_id: str, message: str = ""
    ) -> DiagnosisReport | None:
        report = await self.get_report(report_id)
        if report is None:
            return None
        report.status = "failed"
        report.pacs_message = message[:2000] if message else report.pacs_message
        await self.db.flush()
        return report


__all__ = ["ReportRepository"]
