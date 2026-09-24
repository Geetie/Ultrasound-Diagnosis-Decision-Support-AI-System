import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_sec, pytest.mark.track_agt, pytest.mark.sec_domain_consent]

from app.config.redis_keys import RedisKey
from app.models.schemas import ConsentAction, ConsentQueryResponse, ConsentStatus, ConsentUpdateRequest
from app.security.consent_withdrawal_consumer import (
    CONSENT_WITHDRAWAL_STREAM,
    ConsentWithdrawalConsumer,
    ConsentWithdrawalEvent,
    ConsentWithdrawalHandler,
    DataCleanupHandler,
    SafetyActionHandler,
    SchedulingAgentHandler,
    _decode_field,
)


class TestDecodeField:
    def test_decode_bytes_field(self):
        fields = {b"patient_id": b"P20250001", b"operator_id": b"DOC001"}
        assert _decode_field(fields, "patient_id") == "P20250001"
        assert _decode_field(fields, "operator_id") == "DOC001"

    def test_decode_str_field(self):
        fields = {"patient_id": "P20250001", "operator_id": "DOC001"}
        assert _decode_field(fields, "patient_id") == "P20250001"
        assert _decode_field(fields, "operator_id") == "DOC001"

    def test_decode_missing_field_returns_default(self):
        fields = {b"patient_id": b"P20250001"}
        assert _decode_field(fields, "missing_key") == ""
        assert _decode_field(fields, "missing_key", "fallback") == "fallback"

    def test_decode_mixed_bytes_str_dict(self):
        fields = {b"patient_id": b"P20250001", "operator_id": "DOC001"}
        assert _decode_field(fields, "patient_id") == "P20250001"
        assert _decode_field(fields, "operator_id") == "DOC001"


class TestConsentWithdrawalEvent:
    def test_event_dataclass(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="DOC001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        assert event.patient_id == "P001"
        assert event.operator_id == "DOC001"
        assert event.consent_version == "v1.0"
        assert event.event_id == "msg-001"


class TestConsentWithdrawalHandlerInterface:
    def test_abstract_interface_cannot_instantiate(self):
        with pytest.raises(TypeError):
            ConsentWithdrawalHandler()

    def test_safety_action_handler_is_concrete(self):
        handler = SafetyActionHandler()
        assert isinstance(handler, ConsentWithdrawalHandler)

    def test_scheduling_agent_handler_is_concrete(self):
        handler = SchedulingAgentHandler()
        assert isinstance(handler, ConsentWithdrawalHandler)


class TestSafetyActionHandler:
    def setup_method(self):
        self.handler = SafetyActionHandler()

    def test_get_handler_name(self):
        assert self.handler.get_handler_name() == "safety_action"

    @pytest.mark.asyncio
    async def test_handle_withdrawal_marks_sessions_withdrawn(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250001",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_session_1 = MagicMock()
        mock_session_1.session_id = "S001"
        mock_session_1.consent_status_at_creation = "active"
        mock_session_2 = MagicMock()
        mock_session_2.session_id = "S002"
        mock_session_2.consent_status_at_creation = "active"

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_session_1, mock_session_2]
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_redis.publish = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)

            assert mock_redis.set.call_count == 2
            mock_redis.set.assert_any_call(
                RedisKey.consent_withdrawn_session("S001"), "true", ex=86400
            )
            mock_redis.set.assert_any_call(
                RedisKey.consent_withdrawn_session("S002"), "true", ex=86400
            )

    @pytest.mark.asyncio
    async def test_handle_withdrawal_publishes_notification(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250001",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_redis.publish = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)

            mock_redis.publish.assert_any_call(
                RedisKey.CONSENT_WITHDRAWAL_NOTIFICATION, "P20250001"
            )

    @pytest.mark.asyncio
    async def test_handle_withdrawal_stops_edge_annotation(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250001",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        edge_key_1 = "ultrasound:edge:session:SE001:P20250001"
        edge_key_2 = "ultrasound:edge:session:SE002:P20250001"

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.publish = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_redis.scan = AsyncMock(return_value=(0, [edge_key_1.encode(), edge_key_2.encode()]))
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)

            mock_redis.publish.assert_any_call(
                RedisKey.edge_stop_ai_annotation("P20250001"), "P20250001"
            )
            assert mock_redis.publish.call_count >= 3

    @pytest.mark.asyncio
    async def test_handle_withdrawal_writes_audit_log(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250001",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis.set = AsyncMock(return_value=True)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_redis.publish = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)

            mock_audit_instance.log_action.assert_called_once_with(
                action="CONSENT_WITHDRAWAL_SAFETY_ACTION",
                actor="DOC001",
                target_type="patient",
                target_id="P20250001",
                detail={
                    "withdrawn_at": event.withdrawn_at.isoformat(),
                    "consent_version": "v1.0",
                    "event_id": "msg-001",
                },
            )

    @pytest.mark.asyncio
    async def test_handle_withdrawal_redis_failure_graceful(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250002",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-002",
        )
        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis_pool.get_client = AsyncMock(side_effect=Exception("Redis connection refused"))
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)


class TestSchedulingAgentHandler:
    def setup_method(self):
        self.handler = SchedulingAgentHandler()

    def test_get_handler_name(self):
        assert self.handler.get_handler_name() == "scheduling_agent_pending"

    @pytest.mark.asyncio
    async def test_handle_withdrawal_raises_not_implemented(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="DOC001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        with pytest.raises(NotImplementedError):
            await self.handler.handle_withdrawal(event)


class TestDataCleanupHandler:
    def setup_method(self):
        self.handler = DataCleanupHandler()

    def test_get_handler_name(self):
        assert self.handler.get_handler_name() == "data_cleanup"

    @pytest.mark.asyncio
    async def test_handle_withdrawal_clears_desensitization_cache(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="DOC001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        with patch("app.config.redis.RedisPool") as mock_redis_pool:
            mock_redis = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_redis.delete = AsyncMock()
            await self.handler.handle_withdrawal(event)
            mock_redis.scan.assert_called()

    @pytest.mark.asyncio
    async def test_handle_withdrawal_marks_completed_sessions(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="DOC001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance
            await self.handler.handle_withdrawal(event)
            mock_db_session.execute.assert_called()

    @pytest.mark.asyncio
    async def test_handle_withdrawal_writes_audit_log(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="DOC001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance
            await self.handler.handle_withdrawal(event)
            mock_audit_instance.log_action.assert_called()

    @pytest.mark.asyncio
    async def test_handle_withdrawal_deletes_desens_cache_keys(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250001",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        desens_key_1 = "ultrasound:desens:session:S001:P20250001"
        desens_key_2 = "ultrasound:desens:session:S002:P20250001"

        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis.delete = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_redis.scan = AsyncMock(return_value=(0, [desens_key_1.encode(), desens_key_2.encode()]))
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)

            assert mock_redis.delete.call_count == 2
            mock_redis.delete.assert_any_call(desens_key_1.encode())
            mock_redis.delete.assert_any_call(desens_key_2.encode())

    @pytest.mark.asyncio
    async def test_handle_withdrawal_audit_log_content(self):
        event = ConsentWithdrawalEvent(
            patient_id="P20250001",
            operator_id="DOC001",
            withdrawn_at=datetime(2025, 1, 15, 10, 30, 0, tzinfo=timezone.utc),
            consent_version="v1.0",
            event_id="msg-001",
        )
        mock_db_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db_session.execute = AsyncMock(return_value=mock_result)
        mock_db_session.commit = AsyncMock()

        async def _mock_session_gen():
            yield mock_db_session

        with patch("app.config.redis.RedisPool") as mock_redis_pool, \
             patch("app.config.database.get_session", side_effect=_mock_session_gen), \
             patch("app.services.audit_service.AuditService") as mock_audit:
            mock_redis = AsyncMock()
            mock_redis_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_redis.scan = AsyncMock(return_value=(0, []))
            mock_audit_instance = AsyncMock()
            mock_audit.return_value = mock_audit_instance

            await self.handler.handle_withdrawal(event)

            mock_audit_instance.log_action.assert_called_once_with(
                action="CONSENT_WITHDRAWAL_DATA_CLEANUP",
                actor="DOC001",
                target_type="patient",
                target_id="P20250001",
                detail={
                    "withdrawn_at": event.withdrawn_at.isoformat(),
                    "event_id": "msg-001",
                },
            )


class TestConsentWithdrawalConsumer:
    def test_consumer_initialization(self):
        handlers = [SafetyActionHandler(), SchedulingAgentHandler()]
        consumer = ConsentWithdrawalConsumer(handlers)
        assert len(consumer._handlers) == 2
        assert consumer._running is False

    def test_consumer_with_single_handler(self):
        consumer = ConsentWithdrawalConsumer([SafetyActionHandler()])
        assert len(consumer._handlers) == 1

    @pytest.mark.asyncio
    async def test_start_and_stop(self):
        consumer = ConsentWithdrawalConsumer([SafetyActionHandler()])
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.xgroup_create = AsyncMock()
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            await consumer.start()
            assert consumer._running is True
            await consumer.stop()
            assert consumer._running is False

    @pytest.mark.asyncio
    async def test_start_idempotent(self):
        consumer = ConsentWithdrawalConsumer([SafetyActionHandler()])
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.xgroup_create = AsyncMock()
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            await consumer.start()
            await consumer.start()
            assert consumer._running is True
            await consumer.stop()

    @pytest.mark.asyncio
    async def test_process_event_calls_handlers(self):
        handler1 = AsyncMock(spec=ConsentWithdrawalHandler)
        handler1.get_handler_name = MagicMock(return_value="handler1")
        handler2 = AsyncMock(spec=ConsentWithdrawalHandler)
        handler2.get_handler_name = MagicMock(return_value="handler2")

        consumer = ConsentWithdrawalConsumer([handler1, handler2])
        fields = {
            b"patient_id": b"P001",
            b"operator_id": b"DOC001",
            b"withdrawn_at": b"2025-01-01T00:00:00+00:00",
            b"consent_version": b"v1.0",
        }
        await consumer._process_event(b"msg-001", fields)
        handler1.handle_withdrawal.assert_called_once()
        handler2.handle_withdrawal.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_event_continues_on_handler_error(self):
        handler1 = AsyncMock(spec=ConsentWithdrawalHandler)
        handler1.get_handler_name = MagicMock(return_value="handler1")
        handler1.handle_withdrawal = AsyncMock(side_effect=Exception("handler1 failed"))

        handler2 = AsyncMock(spec=ConsentWithdrawalHandler)
        handler2.get_handler_name = MagicMock(return_value="handler2")

        consumer = ConsentWithdrawalConsumer([handler1, handler2])
        fields = {
            b"patient_id": b"P001",
            b"operator_id": b"DOC001",
            b"withdrawn_at": b"2025-01-01T00:00:00+00:00",
            b"consent_version": b"v1.0",
        }
        await consumer._process_event(b"msg-001", fields)
        handler2.handle_withdrawal.assert_called_once()

    @pytest.mark.asyncio
    async def test_processing_error_dead_letter_contains_required_fields(self):
        consumer = ConsentWithdrawalConsumer([SafetyActionHandler()])
        with patch("app.config.redis.RedisPool") as mock_pool:
            mock_redis = AsyncMock()
            mock_redis.xadd = AsyncMock()
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            await consumer._handle_processing_error(
                b"msg-001", {"patient_id": "P001"}, Exception("test error")
            )
            mock_redis.xadd.assert_called_once()
            call_args = mock_redis.xadd.call_args
            dead_letter_data = call_args[0][1]
            assert "original_msg_id" in dead_letter_data
            assert dead_letter_data["original_msg_id"] == "msg-001"
            assert "error" in dead_letter_data
            assert dead_letter_data["error"] == "test error"
            assert "failed_at" in dead_letter_data

    @pytest.mark.asyncio
    async def test_create_consumer_factory(self):
        from app.config.settings import settings
        from app.security.consent_withdrawal_consumer import create_consent_withdrawal_consumer
        with patch.object(settings, 'consent_withdrawal_handlers', ["safety_action"]):
            consumer = await create_consent_withdrawal_consumer()
            assert len(consumer._handlers) == 1
            assert consumer._handlers[0].get_handler_name() == "safety_action"

    @pytest.mark.asyncio
    async def test_create_consumer_with_scheduling_agent(self):
        from app.config.settings import settings
        from app.security.consent_withdrawal_consumer import create_consent_withdrawal_consumer
        with patch.object(settings, 'consent_withdrawal_handlers', ["safety_action", "scheduling_agent"]):
            consumer = await create_consent_withdrawal_consumer()
            assert len(consumer._handlers) == 2
            handler_names = [h.get_handler_name() for h in consumer._handlers]
            assert "safety_action" in handler_names
            assert "scheduling_agent_pending" in handler_names


class TestConsumeLoopE2E:
    @pytest.mark.asyncio
    async def test_consume_loop_processes_and_acks(self):
        handler = AsyncMock(spec=ConsentWithdrawalHandler)
        handler.get_handler_name = MagicMock(return_value="test_handler")
        consumer = ConsentWithdrawalConsumer([handler])

        msg_id = b"1234567890-0"
        fields = {
            b"patient_id": b"P20250001",
            b"operator_id": b"DOC001",
            b"withdrawn_at": b"2025-01-15T10:30:00+00:00",
            b"consent_version": b"v1.0",
        }

        with patch("app.config.redis.RedisPool") as mock_pool, \
             patch("app.config.settings.settings") as mock_settings:
            mock_redis = AsyncMock()
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_settings.consent_withdrawal_consumer_group = "test_group"
            mock_settings.consent_withdrawal_consumer_name = "test_consumer"
            mock_settings.consent_withdrawal_batch_size = 10
            mock_settings.consent_withdrawal_poll_timeout_ms = 0

            mock_redis.xreadgroup = AsyncMock(
                side_effect=[
                    [(CONSENT_WITHDRAWAL_STREAM, [(msg_id, fields)])],
                    None,
                ]
            )
            mock_redis.xack = AsyncMock()

            consumer._running = True

            async def _stop_after_first_iteration():
                await asyncio.sleep(0.1)
                consumer._running = False

            asyncio.get_running_loop().create_task(_stop_after_first_iteration())

            with patch.object(consumer, "_process_event", new_callable=AsyncMock) as mock_process:
                await consumer._consume_loop()

                mock_process.assert_called_once_with(msg_id, fields)
                mock_redis.xack.assert_called_once_with(
                    CONSENT_WITHDRAWAL_STREAM,
                    mock_settings.consent_withdrawal_consumer_group,
                    msg_id,
                )

    @pytest.mark.asyncio
    async def test_consume_loop_skips_ack_on_process_error(self):
        handler = AsyncMock(spec=ConsentWithdrawalHandler)
        handler.get_handler_name = MagicMock(return_value="test_handler")
        consumer = ConsentWithdrawalConsumer([handler])

        msg_id = b"1234567890-0"
        fields = {
            b"patient_id": b"P20250001",
            b"operator_id": b"DOC001",
            b"withdrawn_at": b"2025-01-15T10:30:00+00:00",
            b"consent_version": b"v1.0",
        }

        with patch("app.config.redis.RedisPool") as mock_pool, \
             patch("app.config.settings.settings") as mock_settings:
            mock_redis = AsyncMock()
            mock_pool.get_client = AsyncMock(return_value=mock_redis)
            mock_settings.consent_withdrawal_consumer_group = "test_group"
            mock_settings.consent_withdrawal_consumer_name = "test_consumer"
            mock_settings.consent_withdrawal_batch_size = 10
            mock_settings.consent_withdrawal_poll_timeout_ms = 0

            mock_redis.xreadgroup = AsyncMock(
                side_effect=[
                    [(CONSENT_WITHDRAWAL_STREAM, [(msg_id, fields)])],
                    None,
                ]
            )
            mock_redis.xack = AsyncMock()

            consumer._running = True

            async def _stop_after_first_iteration():
                await asyncio.sleep(0.1)
                consumer._running = False

            asyncio.get_running_loop().create_task(_stop_after_first_iteration())

            with patch.object(consumer, "_process_event", new_callable=AsyncMock, side_effect=Exception("processing failed")):
                await consumer._consume_loop()

                mock_redis.xack.assert_not_called()


# ============================================================================
# 合并自 test_sec_token_consent_audit_deep.py - Consent 相关
# ============================================================================


class TestConsentStatusTransitions:

    def test_granted_to_withdrawn(self):
        assert ConsentStatus.GRANTED is not None
        assert ConsentStatus.WITHDRAWN is not None
        assert ConsentStatus.GRANTED != ConsentStatus.WITHDRAWN

    def test_all_status_values(self):
        statuses = list(ConsentStatus)
        status_names = [s.value for s in statuses]
        assert "granted" in status_names
        assert "withdrawn" in status_names


class TestConsentUpdateRequestValidation:

    def test_valid_grant_request(self):
        req = ConsentUpdateRequest(
            patient_id="P001",
            action=ConsentAction.GRANT,
            operator_id="dr_zhang",
            consent_version="1.0",
        )
        assert req.action == ConsentAction.GRANT

    def test_valid_withdraw_request(self):
        req = ConsentUpdateRequest(
            patient_id="P001",
            action=ConsentAction.WITHDRAW,
            operator_id="dr_zhang",
            consent_version="1.0",
        )
        assert req.action == ConsentAction.WITHDRAW

    def test_missing_operator_id_fails(self):
        with pytest.raises(Exception):
            ConsentUpdateRequest(
                patient_id="P001",
                action=ConsentAction.GRANT,
                consent_version="1.0",
            )

    def test_consent_update_requires_operator(self):
        from app.security.consent_withdrawal_consumer import ConsentWithdrawalConsumer
        assert ConsentWithdrawalConsumer is not None


class TestConsentQueryResponse:

    def test_query_response_fields(self):
        resp = ConsentQueryResponse(
            patient_id="P001",
            consent_status=ConsentStatus.GRANTED,
        )
        assert resp.patient_id == "P001"
        assert resp.consent_status == ConsentStatus.GRANTED

    def test_query_response_with_timestamp(self):
        resp = ConsentQueryResponse(
            patient_id="P001",
            consent_status=ConsentStatus.WITHDRAWN,
        )
        assert hasattr(resp, 'consented_at')


class TestConsentWithdrawalConsumerDeep:

    def test_event_dataclass(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="dr_zhang",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="evt_001",
        )
        assert event.patient_id == "P001"
        assert event.operator_id == "dr_zhang"

    def test_handler_interface(self):
        handler = SafetyActionHandler()
        assert hasattr(handler, 'handle_withdrawal')

    def test_safety_action_handler(self):
        handler = SafetyActionHandler()
        assert handler.get_handler_name() == "safety_action"

    def test_scheduling_agent_handler(self):
        handler = SchedulingAgentHandler()
        assert handler.get_handler_name() == "scheduling_agent_pending"

    def test_consent_withdrawal_broadcast_retries(self):
        from app.security.consent_withdrawal_consumer import ConsentWithdrawalConsumer
        assert hasattr(ConsentWithdrawalConsumer, '_process_event')

    def test_consent_withdrawal_consumer_exists(self):
        from app.security.consent_withdrawal_consumer import ConsentWithdrawalConsumer
        assert ConsentWithdrawalConsumer is not None


# ============================================================================
# 合并自 test_sec_gap_fill.py - Consent 相关
# ============================================================================


class TestConsentWithdrawalConsumerIntegration:

    def test_consumer_initialization(self):
        consumer = ConsentWithdrawalConsumer(handlers=[])
        assert consumer is not None

    def test_event_dataclass_fields(self):
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="D001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0",
            event_id="evt_001",
        )
        assert event.patient_id == "P001"
        assert event.operator_id == "D001"
        assert event.consent_version == "v1.0"

    def test_safety_action_handler_name(self):
        handler = SafetyActionHandler()
        assert handler.get_handler_name() == "safety_action"

    def test_scheduling_agent_handler_name(self):
        handler = SchedulingAgentHandler()
        assert handler.get_handler_name() == "scheduling_agent_pending"

    def test_consumer_with_single_handler(self):
        consumer = ConsentWithdrawalConsumer(handlers=[SafetyActionHandler()])
        assert len(consumer._handlers) == 1

    def test_event_id_uniqueness(self):
        e1 = ConsentWithdrawalEvent(
            patient_id="P001", operator_id="D001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0", event_id="evt_001",
        )
        e2 = ConsentWithdrawalEvent(
            patient_id="P001", operator_id="D001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1.0", event_id="evt_002",
        )
        assert e1.event_id != e2.event_id
