import asyncio
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

logger = structlog.get_logger()

from app.config.redis_keys import RedisKey

CONSENT_WITHDRAWAL_STREAM = RedisKey.CONSENT_WITHDRAWAL_STREAM
CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM = RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM


def _decode_field(fields: dict, key: str, default: str = "") -> str:
    raw = fields.get(key.encode(), fields.get(key, default))
    if isinstance(raw, bytes):
        return raw.decode()
    return str(raw)


@dataclass
class ConsentWithdrawalEvent:
    patient_id: str
    operator_id: str
    withdrawn_at: datetime
    consent_version: str
    event_id: str


class ConsentWithdrawalHandler(ABC):
    @abstractmethod
    async def handle_withdrawal(self, event: ConsentWithdrawalEvent) -> None:
        ...

    @abstractmethod
    def get_handler_name(self) -> str:
        ...


class SafetyActionHandler(ConsentWithdrawalHandler):
    async def handle_withdrawal(self, event: ConsentWithdrawalEvent) -> None:
        logger.info("safety_action_handler_processing_withdrawal",
                    patient_id=event.patient_id, event_id=event.event_id)

        try:
            from app.config.redis import RedisPool
            redis_client = await RedisPool.get_client()
        except Exception as e:
            logger.error("failed_to_get_redis_client",
                        patient_id=event.patient_id, error=str(e))
            redis_client = None

        if redis_client is not None:
            try:
                from app.config.database import get_session
                from app.models.database import DiagnosisSession
                from sqlalchemy import select, update

                async for db_session in get_session():
                    result = await db_session.execute(
                        select(DiagnosisSession)
                        .where(DiagnosisSession.patient_id == event.patient_id)
                        .where(DiagnosisSession.status.in_(["pending", "in_progress"]))
                    )
                    sessions = result.scalars().all()

                    for session in sessions:
                        session.consent_status_at_creation = "withdrawn"
                        await redis_client.set(
                            RedisKey.consent_withdrawn_session(session.session_id),
                            "true",
                            ex=86400,
                        )
                    await db_session.commit()

                    logger.info("sessions_marked_withdrawn",
                               patient_id=event.patient_id,
                               session_count=len(sessions))
            except Exception as e:
                logger.error("failed_to_mark_sessions_withdrawn",
                            patient_id=event.patient_id, error=str(e))

            try:
                await redis_client.publish(
                    RedisKey.CONSENT_WITHDRAWAL_NOTIFICATION,
                    event.patient_id,
                )
                logger.info("consent_withdrawal_notification_published",
                           patient_id=event.patient_id)
            except Exception as e:
                logger.error("failed_to_publish_withdrawal_notification",
                            patient_id=event.patient_id, error=str(e))

            try:
                cursor = 0
                while True:
                    cursor, keys = await redis_client.scan(
                        cursor=cursor, match=RedisKey.edge_session_pattern(event.patient_id), count=100
                    )
                    for key in keys:
                        key_str = key.decode() if isinstance(key, bytes) else key
                        session_id = key_str.split(":")[-1]
                        await redis_client.publish(
                            RedisKey.edge_stop_ai_annotation(session_id),
                            event.patient_id,
                        )
                        logger.info("edge_stop_ai_annotation_published",
                                   session_id=session_id, patient_id=event.patient_id)
                    if cursor == 0:
                        break
                    await asyncio.sleep(0)
            except Exception as e:
                logger.error("failed_to_notify_edge_stop_annotation",
                            patient_id=event.patient_id, error=str(e))

        try:
            from app.services.audit_service import AuditService
            from app.config.database import get_session
            async for _db in get_session():
                audit_svc = AuditService(_db)
                await audit_svc.log_action(
                    action="CONSENT_WITHDRAWAL_SAFETY_ACTION",
                    actor=event.operator_id,
                    target_type="patient",
                    target_id=event.patient_id,
                    detail={
                        "withdrawn_at": event.withdrawn_at.isoformat(),
                        "consent_version": event.consent_version,
                        "event_id": event.event_id,
                    },
                )
                await _db.commit()
                break
        except Exception as e:
            logger.warning("failed_to_audit_withdrawal_safety_action", error=str(e))

    def get_handler_name(self) -> str:
        return "safety_action"


class SchedulingAgentHandler(ConsentWithdrawalHandler):
    async def handle_withdrawal(self, event: ConsentWithdrawalEvent) -> None:
        logger.info("scheduling_agent_handler_withdrawal_received",
                    patient_id=event.patient_id,
                    event_id=event.event_id,
                    status="integration_pending")
        raise NotImplementedError("SchedulingAgentHandler must be implemented before production use")

    def get_handler_name(self) -> str:
        return "scheduling_agent_pending"


class DataCleanupHandler(ConsentWithdrawalHandler):
    async def handle_withdrawal(self, event: ConsentWithdrawalEvent) -> None:
        logger.info("data_cleanup_handler_processing_withdrawal",
                    patient_id=event.patient_id, event_id=event.event_id)

        try:
            from app.config.redis import RedisPool
            from app.config.redis_keys import RedisKey
            redis_client = await RedisPool.get_client()

            cursor = 0
            while True:
                cursor, keys = await redis_client.scan(
                    cursor=cursor, match=RedisKey.desens_session("*", event.patient_id), count=100
                )
                for key in keys:
                    parts = key.decode().split(":") if isinstance(key, bytes) else key.split(":")
                    session_id = parts[-2]
                    await redis_client.delete(key)
                    logger.info("desensitization_mapping_cache_cleared",
                               session_id=session_id, patient_id=event.patient_id)
                if cursor == 0:
                    break
                await asyncio.sleep(0)
        except Exception as e:
            logger.error("failed_to_clear_desensitization_cache",
                        patient_id=event.patient_id, error=str(e))

        try:
            from app.config.database import get_session
            from app.models.database import DiagnosisSession
            from sqlalchemy import select, update

            async for db_session in get_session():
                result = await db_session.execute(
                    select(DiagnosisSession)
                    .where(DiagnosisSession.patient_id == event.patient_id)
                    .where(DiagnosisSession.status.in_(["completed", "needs_review"]))
                )
                sessions = result.scalars().all()

                for session in sessions:
                    session.consent_status_at_creation = "withdrawn"

                if sessions:
                    await db_session.commit()
                    logger.info("completed_sessions_marked_withdrawn",
                               patient_id=event.patient_id,
                               session_count=len(sessions))
        except Exception as e:
            logger.error("failed_to_mark_completed_sessions_withdrawn",
                        patient_id=event.patient_id, error=str(e))

        try:
            from app.services.audit_service import AuditService
            from app.config.database import get_session
            async for _db in get_session():
                audit_svc = AuditService(_db)
                await audit_svc.log_action(
                    action="CONSENT_WITHDRAWAL_DATA_CLEANUP",
                    actor=event.operator_id,
                    target_type="patient",
                    target_id=event.patient_id,
                    detail={
                        "withdrawn_at": event.withdrawn_at.isoformat(),
                        "event_id": event.event_id,
                    },
                )
                await _db.commit()
                break
        except Exception as e:
            logger.warning("failed_to_audit_data_cleanup", error=str(e))

    def get_handler_name(self) -> str:
        return "data_cleanup"


class ConsentWithdrawalConsumer:
    def __init__(self, handlers: List[ConsentWithdrawalHandler]) -> None:
        self._handlers = handlers
        self._running = False
        self._consumer_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        if self._running:
            return

        self._running = True
        try:
            from app.config.redis import RedisPool
            from app.config.settings import settings
            redis_client = await RedisPool.get_client()

            try:
                await redis_client.xgroup_create(
                    RedisKey.CONSENT_WITHDRAWAL_STREAM,
                    settings.consent_withdrawal_consumer_group,
                    id="0",
                    mkstream=True,
                )
            except Exception as e:
                err_str = str(e)
                if "BUSYGROUP" in err_str:
                    logger.info(
                        "consent_withdrawal_consumer_group_already_exists",
                        group=settings.consent_withdrawal_consumer_group,
                        stream=RedisKey.CONSENT_WITHDRAWAL_STREAM,
                    )
                else:
                    logger.warning(
                        "consent_withdrawal_xgroup_create_error",
                        error=err_str,
                    )

            self._consumer_task = asyncio.create_task(self._consume_loop())
            logger.info("consent_withdrawal_consumer_started",
                       handlers=[h.get_handler_name() for h in self._handlers])
        except Exception as e:
            logger.error("failed_to_start_consent_withdrawal_consumer", error=str(e))
            self._running = False

    async def stop(self) -> None:
        self._running = False
        if self._consumer_task:
            self._consumer_task.cancel()
            try:
                await self._consumer_task
            except asyncio.CancelledError:
                pass
        logger.info("consent_withdrawal_consumer_stopped")

    async def _consume_loop(self) -> None:
        from app.config.redis import RedisPool
        from app.config.settings import settings

        while self._running:
            try:
                redis_client = await RedisPool.get_client()
                messages = await redis_client.xreadgroup(
                    groupname=settings.consent_withdrawal_consumer_group,
                    consumername=settings.consent_withdrawal_consumer_name,
                    streams={RedisKey.CONSENT_WITHDRAWAL_STREAM: ">"},
                    count=settings.consent_withdrawal_batch_size,
                    block=settings.consent_withdrawal_poll_timeout_ms,
                )

                if messages:
                    for stream, msg_list in messages:
                        for msg_id, fields in msg_list:
                            try:
                                await self._process_event(msg_id, fields)
                                await redis_client.xack(
                                    RedisKey.CONSENT_WITHDRAWAL_STREAM,
                                    settings.consent_withdrawal_consumer_group,
                                    msg_id,
                                )
                            except Exception as e:
                                logger.error("consent_withdrawal_event_ack_skipped",
                                           msg_id=msg_id, error=str(e))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("consent_withdrawal_consume_error", error=str(e))
                await asyncio.sleep(1)

    async def _process_event(self, msg_id: bytes, fields: dict) -> None:
        try:
            event = ConsentWithdrawalEvent(
                patient_id=_decode_field(fields, "patient_id"),
                operator_id=_decode_field(fields, "operator_id"),
                withdrawn_at=datetime.fromisoformat(_decode_field(fields, "withdrawn_at")),
                consent_version=_decode_field(fields, "consent_version"),
                event_id=msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
            )

            for handler in self._handlers:
                try:
                    await handler.handle_withdrawal(event)
                except Exception as e:
                    logger.error("consent_withdrawal_handler_error",
                                handler=handler.get_handler_name(),
                                patient_id=event.patient_id,
                                error=str(e))

            logger.info("consent_withdrawal_event_processed",
                       patient_id=event.patient_id, event_id=event.event_id)

        except Exception as e:
            logger.error("consent_withdrawal_event_processing_failed",
                        msg_id=msg_id, error=str(e))
            await self._handle_processing_error(msg_id, fields, e)

    async def _handle_processing_error(
        self, msg_id: bytes, fields: dict, error: Exception
    ) -> None:
        try:
            from app.config.redis import RedisPool
            redis_client = await RedisPool.get_client()
            dead_letter_data = {
                "original_msg_id": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                "error": str(error),
                "failed_at": datetime.now(timezone.utc).isoformat(),
            }
            for k, v in fields.items():
                key = k.decode() if isinstance(k, bytes) else str(k)
                val = v.decode() if isinstance(v, bytes) else str(v)
                dead_letter_data[key] = val

            await redis_client.xadd(RedisKey.CONSENT_WITHDRAWAL_DEAD_LETTER_STREAM, dead_letter_data, maxlen=1000)
            logger.warning("consent_withdrawal_event_moved_to_dead_letter",
                          msg_id=msg_id)
        except Exception as e:
            logger.critical("failed_to_move_event_to_dead_letter",
                          msg_id=msg_id, error=str(e))


async def create_consent_withdrawal_consumer() -> ConsentWithdrawalConsumer:
    from app.config.settings import settings

    handlers: List[ConsentWithdrawalHandler] = []
    handler_names = getattr(settings, 'consent_withdrawal_handlers', None)
    if handler_names is None:
        handler_names = ["safety_action", "data_cleanup"]

    for name in handler_names:
        if name == "safety_action":
            handlers.append(SafetyActionHandler())
        elif name == "scheduling_agent":
            handlers.append(SchedulingAgentHandler())
        elif name == "data_cleanup":
            handlers.append(DataCleanupHandler())

    if not handlers:
        handlers.append(SafetyActionHandler())

    return ConsentWithdrawalConsumer(handlers)
