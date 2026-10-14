import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.database import AuditLog, DiagnosisSession, SessionArchive
from app.services.audit_service import AuditService
from app.services.cold_archive import ColdArchiveService

logger = structlog.get_logger()

_retention_task: asyncio.Task | None = None
_retention_running = False


class DataRetentionService:

    def __init__(self, db: AsyncSession):
        self.db = db
        self.audit_service = AuditService(db)

    async def archive_expired_sessions(self, batch_size: int = 500) -> int:
        archive_threshold = datetime.now(timezone.utc) - timedelta(days=settings.session_archive_after_days)
        archived_count = 0

        while True:
            result = await self.db.execute(
                select(DiagnosisSession).where(
                    and_(
                        DiagnosisSession.created_at < archive_threshold,
                        DiagnosisSession.status.in_(["completed", "failed", "terminated"])
                    )
                ).limit(batch_size)
            )
            sessions = result.scalars().all()

            if not sessions:
                break

            for session in sessions:
                archive = SessionArchive(
                    session_id=session.session_id,
                    patient_id=session.patient_id,
                    device_id=session.device_id,
                    status=session.status,
                    target_organs=getattr(session, 'target_organs', []),
                    organ_statuses=getattr(session, 'organ_statuses', {}),
                    knowledge_base_version=getattr(session, 'knowledge_base_version', 'unknown'),
                    consent_status_at_creation=getattr(session, 'consent_status_at_creation', 'UNKNOWN'),
                    patient_type=getattr(session, 'patient_type', 'outpatient'),
                    archive_timestamp=datetime.now(timezone.utc),
                    created_at=session.created_at,
                )
                self.db.add(archive)
                await self.db.delete(session)

            await self.db.flush()
            archived_count += len(sessions)

            if len(sessions) < batch_size:
                break

        if archived_count > 0:
            await self.audit_service.log_action(
                action="sessions_archived",
                actor="system",
                detail={"archived_count": archived_count, "threshold_days": settings.session_archive_after_days},
            )

        logger.info("data_retention_archive_completed", count=archived_count)
        return archived_count

    async def purge_expired_archives(self, patient_type: str = "outpatient") -> int:
        if patient_type == "inpatient":
            retention_years = settings.data_retention_inpatient_years
        else:
            retention_years = settings.data_retention_outpatient_years

        expiry_date = datetime.now(timezone.utc) - timedelta(days=int(retention_years * 365.25))

        result = await self.db.execute(
            delete(SessionArchive).where(
                and_(
                    SessionArchive.archived_at < expiry_date,
                    SessionArchive.patient_type == patient_type,
                )
            )
        )

        purged_count = result.rowcount

        if purged_count > 0:
            await self.audit_service.log_action(
                action="archives_purged",
                actor="system",
                detail={"purged_count": purged_count, "retention_years": retention_years, "patient_type": patient_type},
            )
            await self.db.flush()

        logger.info("data_retention_purge_completed", count=purged_count, patient_type=patient_type)
        return purged_count

    async def purge_expired_operation_logs(self) -> int:
        expiry_date = datetime.now(timezone.utc) - timedelta(days=int(settings.data_retention_operation_years * 365.25))

        purgeable_actions = [
            "consent_granted", "consent_withdrawn", "consent_rejected",
            "session_created", "session_completed", "session_failed", "session_terminated",
            "device_registered", "device_updated", "device_deactivated",
            "user_login", "user_logout", "user_created", "user_updated",
            "feedback_submitted", "feedback_acknowledged",
            "hsm_degraded", "hsm_recovered", "hsm_key_rotated",
            "emergency_signing_enabled", "emergency_signing_disabled",
            "sessions_archived", "archives_purged", "operation_logs_purged",
            "retention_compliance_violation",
            "consent_withdrawal_safety_action", "consent_withdrawal_data_cleanup",
        ]

        result = await self.db.execute(
            delete(AuditLog).where(
                and_(
                    AuditLog.created_at < expiry_date,
                    AuditLog.action.in_(purgeable_actions)
                )
            )
        )

        purged_count = result.rowcount

        if purged_count > 0:
            await self.audit_service.log_action(
                action="operation_logs_purged",
                actor="system",
                detail={"purged_count": purged_count, "retention_years": settings.data_retention_operation_years},
            )
            await self.db.flush()

        logger.info("data_retention_operation_purge_completed", count=purged_count)
        return purged_count

    # ------------------------------------------------------------------
    # T-GB-SEC-02：冷热分层（先冷归档，再清理热层）
    # ------------------------------------------------------------------

    async def archive_and_purge_audit_logs(
        self,
        *,
        before: datetime | None = None,
        storage=None,
    ) -> dict[str, Any]:
        """把超期审计日志移入对象存储冷层（PG 仅保留创世锚点）。

        冷层不可用时**不删除任何热数据**（返回结果中 ``failed``/``pending``
        体现），下次任务自动重试。
        """
        service = ColdArchiveService(self.db, storage=storage)
        result = await service.archive_audit_logs(before=before)
        logger.info("audit_logs_cold_tier_completed", **result.as_dict())
        return result.as_dict()

    async def archive_and_purge_session_archives(
        self,
        patient_type: str | None = None,
        *,
        before: datetime | None = None,
        storage=None,
    ) -> dict[str, Any]:
        """到期的会话归档先入冷层再从 PG 清理（门诊 15 年 / 住院 30 年）。"""
        service = ColdArchiveService(self.db, storage=storage)
        result = await service.archive_session_archives(
            before=before, patient_type=patient_type
        )
        logger.info("session_archives_cold_tier_completed", **result.as_dict())
        return result.as_dict()

    async def archive_audit_manifest_stats(self) -> dict[str, Any]:
        """冷归档清单总览（合规留痕/运维面板用）。"""
        service = ColdArchiveService(self.db)
        return await service.stats()

    async def verify_retention_compliance(self) -> dict[str, Any]:
        compliance_report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "config": {
                "outpatient_retention_years": settings.data_retention_outpatient_years,
                "inpatient_retention_years": settings.data_retention_inpatient_years,
                "audit_retention_years": settings.data_retention_audit_years,
                "operation_retention_years": settings.data_retention_operation_years,
                "session_archive_after_days": settings.session_archive_after_days,
            },
            "checks": {},
        }

        archive_threshold = datetime.now(timezone.utc) - timedelta(days=settings.session_archive_after_days)
        unarchived_result = await self.db.execute(
            select(func.count()).select_from(DiagnosisSession).where(
                and_(
                    DiagnosisSession.created_at < archive_threshold,
                    DiagnosisSession.status.in_(["completed", "failed", "terminated"])
                )
            )
        )
        unarchived_count = unarchived_result.scalar()
        compliance_report["checks"]["unarchived_expired_sessions"] = {
            "count": unarchived_count,
            "compliant": unarchived_count == 0,
        }

        outpatient_expiry = datetime.now(timezone.utc) - timedelta(days=int(settings.data_retention_outpatient_years * 365.25))
        overdue_outpatient = await self.db.execute(
            select(func.count()).select_from(SessionArchive).where(
                and_(
                    SessionArchive.archived_at < outpatient_expiry,
                    SessionArchive.patient_type == "outpatient",
                )
            )
        )
        overdue_count = overdue_outpatient.scalar()
        compliance_report["checks"]["overdue_archive_purge"] = {
            "count": overdue_count,
            "compliant": overdue_count == 0,
        }

        inpatient_expiry = datetime.now(timezone.utc) - timedelta(days=int(settings.data_retention_inpatient_years * 365.25))
        overdue_inpatient = await self.db.execute(
            select(func.count()).select_from(SessionArchive).where(
                and_(
                    SessionArchive.archived_at < inpatient_expiry,
                    SessionArchive.patient_type == "inpatient",
                )
            )
        )
        overdue_inpatient_count = overdue_inpatient.scalar()
        compliance_report["checks"]["overdue_inpatient_archive_purge"] = {
            "count": overdue_inpatient_count,
            "compliant": overdue_inpatient_count == 0,
        }

        all_compliant = all(check["compliant"] for check in compliance_report["checks"].values())
        compliance_report["overall_compliant"] = all_compliant

        if not all_compliant:
            await self.audit_service.log_action(
                action="retention_compliance_violation",
                actor="system",
                detail={"checks": compliance_report["checks"]},
            )
            await self.db.flush()

        logger.info("data_retention_compliance_check", compliant=all_compliant, checks=compliance_report["checks"])
        return compliance_report


async def start_data_retention_periodic_task(
    db_session_factory,
    interval_hours: int | None = None,
) -> None:
    global _retention_task, _retention_running
    if _retention_running:
        return
    _retention_running = True
    hours = interval_hours or settings.data_retention_task_interval_hours

    async def _run():
        while _retention_running:
            await asyncio.sleep(hours * 3600)
            try:
                async for db in db_session_factory():
                    service = DataRetentionService(db)
                    # 1) 热层：过期会话 → sessions_archive
                    archived = await service.archive_expired_sessions()
                    # 2) 冷层（T-GB-SEC-02）：审计日志与到期会话归档先进对象
                    #    存储，成功后才从 PG 清理；冷层不可用时保留热数据。
                    cold_audit = await service.archive_and_purge_audit_logs()
                    cold_outpatient = await service.archive_and_purge_session_archives(
                        "outpatient"
                    )
                    cold_inpatient = await service.archive_and_purge_session_archives(
                        "inpatient"
                    )
                    # 3) 仅在未部署对象存储时（显式配置）才退化为直接清理，
                    #    否则"清理"会把本应入冷层的数据直接删掉。
                    if settings.data_retention_purge_without_cold_archive:
                        await service.purge_expired_archives("outpatient")
                        await service.purge_expired_archives("inpatient")
                    # 4) 操作日志（5 年）
                    purged_logs = await service.purge_expired_operation_logs()
                    compliance = await service.verify_retention_compliance()
                    logger.info(
                        "data_retention_periodic_task_completed",
                        archived=archived,
                        cold_audit=cold_audit.get("archived", 0),
                        cold_outpatient=cold_outpatient.get("archived", 0),
                        cold_inpatient=cold_inpatient.get("archived", 0),
                        purged_logs=purged_logs,
                        compliant=compliance.get("overall_compliant", False),
                    )
                    break
            except Exception as e:
                logger.error("data_retention_periodic_task_failed", error=str(e))

    _retention_task = asyncio.create_task(_run())
    logger.info("data_retention_periodic_task_started", interval_hours=hours)


async def stop_data_retention_periodic_task() -> None:
    global _retention_running, _retention_task
    _retention_running = False
    if _retention_task:
        _retention_task.cancel()
        try:
            await _retention_task
        except asyncio.CancelledError:  # silent-ok: 停机时 await 被取消属正常控制流
            pass
        _retention_task = None
    logger.info("data_retention_periodic_task_stopped")
