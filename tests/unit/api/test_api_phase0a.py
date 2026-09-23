from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from app.api.v1.errors import KnowledgeBaseSafetyUpdateException
from app.api.websocket.nav import ConnectionManager
from app.models.schemas import (
    DiagnosisRequest,
    FeedbackContent,
    FeedbackLevel,
    FeedbackSubmission,
    FeedbackType,
    NavigationRevocationMessage,
    ReconnectAckMessage,
    RetryStrategy,
    SessionTerminatedMessage,
    WebSocketMessageType,
)

pytestmark = pytest.mark.track_api


class TestE4006Exception:
    def test_kb_safety_update_exception_defaults(self):
        exc = KnowledgeBaseSafetyUpdateException()
        assert exc.error_code == "E4006"
        assert exc.status_code == 503
        assert "safety update" in exc.error_message

    def test_kb_safety_update_exception_custom_message(self):
        exc = KnowledgeBaseSafetyUpdateException(error_message="自定义消息")
        assert exc.error_message == "自定义消息"
        assert exc.error_code == "E4006"


class TestRetryStrategyEnum:
    def test_full_retrieval(self):
        assert RetryStrategy.FULL_RETRIEVAL == "full_retrieval"

    def test_generation_only(self):
        assert RetryStrategy.GENERATION_ONLY == "generation_only"

    def test_quality_check_only(self):
        assert RetryStrategy.QUALITY_CHECK_ONLY == "quality_check_only"


class TestDiagnosisRequestFields:
    def test_request_id_not_in_request(self):
        req = DiagnosisRequest(
            session_id="sess_001",
            target_organ="thyroid",
            image_ids=[],
        )
        assert not hasattr(req, "request_id")

    def test_request_id_ignored_if_provided(self):
        req = DiagnosisRequest(
            session_id="sess_001",
            target_organ="thyroid",
            image_ids=[],
        )
        assert not hasattr(req, "request_id")

    def test_retry_strategy_optional(self):
        req = DiagnosisRequest(
            session_id="sess_001",
            target_organ="thyroid",
            image_ids=[],
            retry=True,
        )
        assert req.retry_strategy is None

    def test_retry_strategy_provided(self):
        req = DiagnosisRequest(
            session_id="sess_001",
            target_organ="thyroid",
            image_ids=[],
            retry=True,
            retry_strategy=RetryStrategy.GENERATION_ONLY,
        )
        assert req.retry_strategy == RetryStrategy.GENERATION_ONLY


class TestFeedbackIdempotency:
    def test_idempotency_key_optional(self):
        fb = FeedbackSubmission(
            request_id="req_001",
            session_id="sess_001",
            feedback_type=FeedbackType.CORRECTION,
            feedback_level=FeedbackLevel.L2,
            content=FeedbackContent(
                field="diagnosis",
                original_value="A",
                corrected_value="B",
                reason="test",
            ),
        )
        assert fb.idempotency_key is None

    def test_idempotency_key_provided(self):
        fb = FeedbackSubmission(
            request_id="req_001",
            session_id="sess_001",
            feedback_type=FeedbackType.CORRECTION,
            feedback_level=FeedbackLevel.L2,
            content=FeedbackContent(
                field="diagnosis",
                original_value="A",
                corrected_value="B",
                reason="test",
            ),
            idempotency_key="fb_key_123",
        )
        assert fb.idempotency_key == "fb_key_123"

    def test_feedback_idempotency_ttl_24h(self):
        from app.api.v1.feedback import FEEDBACK_IDEMPOTENCY_TTL_SECONDS
        assert FEEDBACK_IDEMPOTENCY_TTL_SECONDS == 86400


class TestWebSocketNewMessageTypes:
    def test_session_terminated_message(self):
        msg = SessionTerminatedMessage(
            timestamp=1000,
            reason="safety_critical_update",
            affected_organs=["thyroid"],
            kb_version="kb_v2.0.0",
        )
        assert msg.type == WebSocketMessageType.SESSION_TERMINATED
        assert msg.reason == "safety_critical_update"
        assert msg.affected_organs == ["thyroid"]
        assert msg.kb_version == "kb_v2.0.0"

    def test_navigation_revocation_message(self):
        msg = NavigationRevocationMessage(
            timestamp=1000,
            frame_id="frame_001",
            reason="safety_check_failed",
        )
        assert msg.type == WebSocketMessageType.NAVIGATION_REVOCATION
        assert msg.frame_id == "frame_001"
        assert msg.reason == "safety_check_failed"

    def test_reconnect_ack_message(self):
        msg = ReconnectAckMessage(
            timestamp=1000,
            session_id="sess_001",
            session_ttl_remaining_ms=600000,
        )
        assert msg.type == WebSocketMessageType.RECONNECT_ACK
        assert msg.session_id == "sess_001"
        assert msg.session_ttl_remaining_ms == 600000

    def test_reconnect_ack_message_no_ttl(self):
        msg = ReconnectAckMessage(
            timestamp=1000,
            session_id="sess_001",
        )
        assert msg.session_ttl_remaining_ms is None


class TestConnectionManagerNewMethods:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    async def test_send_session_terminated(self, manager):
        mock_ws = AsyncMock()
        key = ("sess_001", "device_001")
        manager.active_connections[key] = mock_ws
        manager.session_index["sess_001"] = [key]
        await manager.send_session_terminated(
            "sess_001",
            reason="safety_critical_update",
            affected_organs=["thyroid"],
            kb_version="kb_v2.0.0",
        )
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "session_terminated"
        assert call_args["reason"] == "safety_critical_update"
        assert call_args["affected_organs"] == ["thyroid"]
        assert call_args["kb_version"] == "kb_v2.0.0"

    async def test_send_navigation_revocation(self, manager):
        mock_ws = AsyncMock()
        key = ("sess_001", "device_001")
        manager.active_connections[key] = mock_ws
        manager.session_index["sess_001"] = [key]
        await manager.send_navigation_revocation(
            "sess_001",
            frame_id="frame_001",
            reason="safety_check_failed",
        )
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "navigation_revocation"
        assert call_args["frame_id"] == "frame_001"
        assert call_args["reason"] == "safety_check_failed"

    async def test_send_reconnect_ack(self, manager):
        mock_ws = AsyncMock()
        key = ("sess_001", "device_001")
        manager.active_connections[key] = mock_ws
        manager.session_index["sess_001"] = [key]
        await manager.send_reconnect_ack(
            "sess_001",
            session_ttl_remaining_ms=600000,
        )
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "reconnect_ack"
        assert call_args["session_id"] == "sess_001"
        assert call_args["session_ttl_remaining_ms"] == 600000

    async def test_send_session_terminated_no_connection(self, manager):
        result = await manager.send_session_terminated("nonexistent", reason="test")
        assert result is None

    async def test_send_navigation_revocation_no_connection(self, manager):
        result = await manager.send_navigation_revocation("nonexistent", frame_id="f1", reason="test")
        assert result is None

    async def test_send_reconnect_ack_no_connection(self, manager):
        result = await manager.send_reconnect_ack("nonexistent")
        assert result is None


class TestSessionRenew:
    async def test_renew_session(self):
        from app.models.database import DiagnosisSession
        from app.repositories.session_repository import SessionRepository

        mock_db = AsyncMock()
        now = datetime.now(timezone.utc)
        mock_session = Mock(spec=DiagnosisSession)
        mock_session.session_id = "sess_001"
        mock_session.updated_at = now

        repo = SessionRepository(mock_db)
        repo.get_session = AsyncMock(return_value=mock_session)
        mock_db.flush = AsyncMock()

        result = await repo.renew_session("sess_001")
        assert result is not None
        assert result.updated_at >= now
        mock_db.flush.assert_called_once()

    async def test_renew_session_not_found(self):
        from app.repositories.session_repository import SessionRepository

        mock_db = AsyncMock()
        repo = SessionRepository(mock_db)
        repo.get_session = AsyncMock(return_value=None)

        result = await repo.renew_session("nonexistent")
        assert result is None


class TestSessionTTLRemaining:
    def test_calc_ttl_remaining_fresh(self):
        from app.api.v1.session import _calc_ttl_remaining, SESSION_TTL_SECONDS
        now = datetime.now(timezone.utc)
        ttl = _calc_ttl_remaining(now)
        assert ttl is not None
        assert ttl > SESSION_TTL_SECONDS - 5
        assert ttl <= SESSION_TTL_SECONDS

    def test_calc_ttl_remaining_none(self):
        from app.api.v1.session import _calc_ttl_remaining
        assert _calc_ttl_remaining(None) is None

    def test_calc_ttl_remaining_expired(self):
        from app.api.v1.session import _calc_ttl_remaining
        old_time = datetime.now(timezone.utc) - timedelta(hours=3)
        ttl = _calc_ttl_remaining(old_time)
        assert ttl == 0

    def test_session_ttl_is_7200(self):
        from app.api.v1.session import SESSION_TTL_SECONDS
        assert SESSION_TTL_SECONDS == 7200


class TestRequestIdGeneration:
    def test_generate_request_id_format(self):
        from app.api.v1.diagnosis import _generate_request_id
        rid = _generate_request_id()
        assert rid.startswith("req_")
        parts = rid.split("_")
        assert len(parts) == 3
        assert len(parts[1]) == 8
        assert len(parts[2]) == 8

    def test_generate_request_id_unique(self):
        from app.api.v1.diagnosis import _generate_request_id
        ids = {_generate_request_id() for _ in range(100)}
        assert len(ids) == 100


class TestSessionResponseTTLField:
    def test_session_response_has_ttl_remaining(self):
        from app.models.schemas import SessionResponse
        fields = SessionResponse.model_fields
        assert "ttl_remaining_seconds" in fields


class TestWebSocketMessageTypeEnum:
    def test_session_terminated_type(self):
        assert WebSocketMessageType.SESSION_TERMINATED == "session_terminated"

    def test_navigation_revocation_type(self):
        assert WebSocketMessageType.NAVIGATION_REVOCATION == "navigation_revocation"

    def test_reconnect_ack_type(self):
        assert WebSocketMessageType.RECONNECT_ACK == "reconnect_ack"
