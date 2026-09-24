import json
import uuid
from datetime import datetime, timezone

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.redis_keys import RedisKey
from app.models.database import ConsentRecord, SafetyEvent
from app.models.schemas import ConsentStatus, SafetyEventType, SafetySeverity
from app.services.audit_service import AuditService
from app.services.safety_service import SafetyService

logger = structlog.get_logger()


async def record_hsm_degradation_if_needed(db: AsyncSession) -> bool:
    from app.security.hsm import hsm_manager

    if not hsm_manager.is_degraded():
        return False

    degraded_at = hsm_manager.get_degraded_at()
    if degraded_at is None:
        return False

    result = await db.execute(
        select(SafetyEvent)
        .where(SafetyEvent.event_type == SafetyEventType.HSM_DEGRADED.value)
        .where(SafetyEvent.created_at >= degraded_at)
        .limit(1)
    )
    last_event = result.scalar_one_or_none()

    if last_event:
        return False

    now = datetime.now(timezone.utc)
    event_id = int(now.timestamp() * 1000000)

    safety_event = SafetyEvent(
        event_id=event_id,
        event_type=SafetyEventType.HSM_DEGRADED.value,
        severity=SafetySeverity.IMPORTANT.value,
        patient_id=None,
        device_id=None,
        kb_version=None,
        payload={
            "hsm_mode": hsm_manager.get_hsm_mode(),
            "degraded_at": degraded_at.isoformat(),
        },
        created_at=now,
        acknowledged=False,
    )

    db.add(safety_event)
    logger.info("hsm_degradation_event_recorded", event_type=SafetyEventType.HSM_DEGRADED.value)

    return True


class ConsentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_latest_consent(self, patient_id: str) -> ConsentRecord | None:
        """获取患者最新的知情同意记录"""
        result = await self.db.execute(
            select(ConsentRecord)
            .where(ConsentRecord.patient_id == patient_id)
            .order_by(ConsentRecord.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_consent_by_id(self, record_id: int) -> ConsentRecord | None:
        """根据记录ID获取知情同意"""
        result = await self.db.execute(
            select(ConsentRecord).where(ConsentRecord.id == record_id)
        )
        return result.scalar_one_or_none()

    async def create_consent(
        self,
        patient_id: str,
        status: ConsentStatus,
        consent_version: str,
        operator_id: str,
    ) -> ConsentRecord:
        """创建新的知情同意记录"""
        now = datetime.now(timezone.utc)

        record_id = uuid.uuid4().int % (2**63)

        consent_record = ConsentRecord(
            record_id=record_id,
            patient_id=patient_id,
            status=status.value,
            granted_at=now if status == ConsentStatus.GRANTED else None,
            withdrawn_at=now if status == ConsentStatus.WITHDRAWN else None,
            consent_version=consent_version,
            created_at=now,
        )

        self.db.add(consent_record)
        await self.db.flush()

        audit_service = AuditService(self.db)
        await audit_service.log_action(
            action=f"consent_{status.value}",
            actor=operator_id,
            target_type="patient",
            target_id=patient_id,
            detail={"status": status.value, "version": consent_version},
        )

        if status == ConsentStatus.WITHDRAWN:
            safety_service = SafetyService(self.db)
            await safety_service.log_event(
                event_type=SafetyEventType.CONSENT_WITHDRAWN,
                severity=SafetySeverity.IMPORTANT,
                patient_id=patient_id,
                payload={"operator_id": operator_id},
            )
            await self._publish_consent_withdrawal(
                patient_id=patient_id,
                operator_id=operator_id,
                withdrawn_at=now,
                consent_version=consent_version,
            )

        logger.info(
            "consent_record_created",
            patient_id=patient_id,
            status=status.value,
            operator_id=operator_id,
        )

        return consent_record

    async def _publish_consent_withdrawal(
        self,
        patient_id: str,
        operator_id: str,
        withdrawn_at: datetime,
        consent_version: str,
    ) -> None:
        """通过Redis Streams广播同意撤回事件，通知在途诊断任务取消"""
        max_retries = 3
        message = {
            "patient_id": patient_id,
            "operator_id": operator_id,
            "withdrawn_at": withdrawn_at.isoformat(),
            "consent_version": consent_version,
        }
        stream_data = {k: json.dumps(v) if isinstance(v, (dict, list)) else str(v) for k, v in message.items()}

        for attempt in range(max_retries):
            try:
                from app.config.redis import RedisPool
                redis_client = await RedisPool.get_client()

                await redis_client.xadd(RedisKey.CONSENT_WITHDRAWAL_STREAM, stream_data, maxlen=10000)

                logger.info(
                    "consent_withdrawal_published",
                    stream=RedisKey.CONSENT_WITHDRAWAL_STREAM,
                    patient_id=patient_id,
                    attempt=attempt + 1,
                )
                return
            except Exception as e:
                if attempt < max_retries - 1:
                    import asyncio
                    await asyncio.sleep(0.1 * (2 ** attempt))
                    logger.warning(
                        "consent_withdrawal_publish_retry",
                        stream=RedisKey.CONSENT_WITHDRAWAL_STREAM,
                        patient_id=patient_id,
                        attempt=attempt + 1,
                        error=str(e),
                    )
                else:
                    logger.critical(
                        "consent_withdrawal_broadcast_failed_all_retries",
                        stream=RedisKey.CONSENT_WITHDRAWAL_STREAM,
                        patient_id=patient_id,
                        attempts=max_retries,
                        error=str(e),
                    )
                    pending_event = SafetyEvent(
                        event_id=int(datetime.now(timezone.utc).timestamp() * 1000000),
                        event_type=SafetyEventType.CONSENT_WITHDRAWN.value,
                        severity=SafetySeverity.SAFETY_CRITICAL.value,
                        patient_id=patient_id,
                        device_id=None,
                        kb_version=None,
                        payload={
                            "operator_id": operator_id,
                            "withdrawn_at": withdrawn_at.isoformat(),
                            "consent_version": consent_version,
                            "broadcast_failed": True,
                            "error": str(e),
                        },
                        created_at=datetime.now(timezone.utc),
                        acknowledged=False,
                    )
                    self.db.add(pending_event)
                    logger.critical(
                        "consent_withdrawal_broadcast_all_retries_failed",
                        patient_id=patient_id,
                        operator_id=operator_id,
                        action_required="Data Security Officer must be notified immediately",
                        broadcast_retries=3,
                    )

