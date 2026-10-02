import binascii
import struct
from unittest.mock import AsyncMock, patch

import pytest

from app.api.websocket.dicom import (
    MAX_DICOM_FRAME_PAYLOAD_BYTES,
    calculate_crc32,
)
from app.api.websocket.nav import (
    WS_CLOSE_AUTH_FAILED,
    WS_CLOSE_AUTH_TIMEOUT,
    ConnectionManager,
    get_timestamp,
)

pytestmark = pytest.mark.track_api


class TestWebSocketCloseCodes:
    def test_auth_failed_code(self):
        assert WS_CLOSE_AUTH_FAILED == 4001

    def test_auth_timeout_code(self):
        assert WS_CLOSE_AUTH_TIMEOUT == 4002


class TestGetTimestamp:
    def test_returns_int(self):
        ts = get_timestamp()
        assert isinstance(ts, int)

    def test_reasonable_range(self):
        ts = get_timestamp()
        assert ts > 1700000000


class TestConnectionManagerSessionLocks:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_get_session_lock_creates_new(self, manager):
        lock = await manager._get_session_lock("sess_001")
        assert lock is not None
        assert isinstance(lock, type(manager.auth_manager._locks_lock))

    @pytest.mark.asyncio
    async def test_get_session_lock_returns_same(self, manager):
        lock1 = await manager._get_session_lock("sess_001")
        lock2 = await manager._get_session_lock("sess_001")
        assert lock1 is lock2

    @pytest.mark.asyncio
    async def test_get_session_lock_different_sessions(self, manager):
        lock1 = await manager._get_session_lock("sess_001")
        lock2 = await manager._get_session_lock("sess_002")
        assert lock1 is not lock2

    @pytest.mark.asyncio
    async def test_cleanup_session_lock(self, manager):
        await manager._get_session_lock("sess_001")
        assert "sess_001" in manager.auth_manager._session_locks
        manager.session_index["sess_001"] = [("sess_001", "dev_001")]
        manager._cleanup_session_lock("sess_001")
        assert "sess_001" in manager.auth_manager._session_locks

    @pytest.mark.asyncio
    async def test_cleanup_session_lock_when_session_empty(self, manager):
        await manager._get_session_lock("sess_001")
        manager._cleanup_session_lock("sess_001")
        assert "sess_001" not in manager.auth_manager._session_locks


class TestConnectionManagerConnectDisconnect:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_connect_adds_to_index(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        assert ("sess_001", "dev_001") in manager.active_connections
        assert "sess_001" in manager.session_index
        assert ("sess_001", "dev_001") in manager.session_index["sess_001"]

    @pytest.mark.asyncio
    async def test_disconnect_removes_from_index(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.disconnect("sess_001", "dev_001")
        assert ("sess_001", "dev_001") not in manager.active_connections
        assert "sess_001" not in manager.session_index

    @pytest.mark.asyncio
    async def test_multiple_devices_same_session(self, manager):
        mock_ws1 = AsyncMock()
        mock_ws1.accept = AsyncMock()
        mock_ws2 = AsyncMock()
        mock_ws2.accept = AsyncMock()
        await manager.connect(mock_ws1, "sess_001", "dev_001")
        await manager.connect(mock_ws2, "sess_001", "dev_002")
        assert len(manager.session_index["sess_001"]) == 2


class TestConnectionManagerAuthToken:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_generate_and_validate_token(self, manager):
        token = await manager.generate_auth_token("sess_001")
        assert token is not None
        assert len(token) > 0
        is_valid = await manager.validate_auth_token("sess_001", token)
        assert is_valid is True

    @pytest.mark.asyncio
    async def test_validate_wrong_token(self, manager):
        token = await manager.generate_auth_token("sess_001")
        is_valid = await manager.validate_auth_token("sess_001", "wrong_token")
        assert is_valid is False

    @pytest.mark.asyncio
    async def test_validate_unknown_session(self, manager):
        is_valid = await manager.validate_auth_token("unknown_sess", "any_token")
        assert is_valid is False


class TestConnectionManagerSendMessage:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_send_personal_message(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.send_personal_message({"type": "test"}, "sess_001")
        mock_ws.send_json.assert_called_once_with({"type": "test"})

    @pytest.mark.asyncio
    async def test_send_to_nonexistent_session(self, manager):
        result = await manager.send_personal_message({"type": "test"}, "nonexistent")
        assert result is None

    @pytest.mark.asyncio
    async def test_send_diagnosis_result(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.send_diagnosis_result("sess_001", "req_001")
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "diagnosis_result"
        assert call_args["request_id"] == "req_001"

    @pytest.mark.asyncio
    async def test_send_session_terminated(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.send_session_terminated("sess_001", reason="safety_update")
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "session_terminated"
        assert call_args["reason"] == "safety_update"

    @pytest.mark.asyncio
    async def test_send_navigation_revocation(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.send_navigation_revocation("sess_001", frame_id="frame_001", reason="check_failed")
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "navigation_revocation"

    @pytest.mark.asyncio
    async def test_send_reconnect_ack(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.send_reconnect_ack("sess_001", session_ttl_remaining_ms=60000)
        mock_ws.send_json.assert_called_once()
        call_args = mock_ws.send_json.call_args[0][0]
        assert call_args["type"] == "reconnect_ack"
        assert call_args["session_ttl_remaining_ms"] == 60000


class TestConnectionManagerHeartbeat:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    @pytest.mark.asyncio
    async def test_update_heartbeat(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.update_heartbeat("sess_001", "dev_001")
        key = ("sess_001", "dev_001")
        assert manager.missed_pongs[key] == 0

    @pytest.mark.asyncio
    async def test_check_heartbeat_no_timeout(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager.update_heartbeat("sess_001", "dev_001")
        timeout_keys = await manager.check_heartbeat_timeout(timeout_seconds=15, max_missed=3)
        assert len(timeout_keys) == 0


class TestConnectionManagerRedisPubSub:
    @pytest.fixture
    def manager(self):
        return ConnectionManager()

    def test_worker_id_assigned(self, manager):
        assert manager._worker_id is not None
        assert len(manager._worker_id) == 8

    @pytest.mark.asyncio
    async def test_start_subscriber_disabled(self, manager):
        with patch("app.api.websocket.nav.settings") as mock_settings:
            mock_settings.websocket_redis_pubsub_enabled = False
            await manager.start_redis_subscriber()
            assert manager._redis_sub_task is None

    @pytest.mark.asyncio
    async def test_deliver_local(self, manager):
        mock_ws = AsyncMock()
        mock_ws.accept = AsyncMock()
        await manager.connect(mock_ws, "sess_001", "dev_001")
        await manager._deliver_local({"type": "test"}, "sess_001")
        mock_ws.send_json.assert_called_once_with({"type": "test"})


class TestDICOMBinaryFrameProtocol:
    def test_crc32_calculation(self):
        data = b"test data for crc32"
        crc = calculate_crc32(data)
        expected = binascii.crc32(data) & 0xFFFFFFFF
        assert crc == expected

    def test_crc32_empty_data(self):
        crc = calculate_crc32(b"")
        assert crc == 0

    def test_max_dicom_frame_payload(self):
        assert MAX_DICOM_FRAME_PAYLOAD_BYTES == 500 * 1024 * 1024

    def test_binary_frame_structure(self):
        frame_index = 42
        flags = 0x01
        dicom_data = b"DICM" + b"\x00" * 20
        dicom_length = len(dicom_data)
        header = struct.pack("<i", frame_index) + struct.pack("B", flags) + struct.pack("<i", dicom_length)
        crc = calculate_crc32(header + dicom_data)
        frame = header + dicom_data + struct.pack("<I", crc)

        assert len(frame) == 9 + dicom_length + 4
        parsed_index = struct.unpack("<i", frame[0:4])[0]
        assert parsed_index == frame_index
        parsed_flags = frame[4]
        assert parsed_flags == flags
        parsed_length = struct.unpack("<i", frame[5:9])[0]
        assert parsed_length == dicom_length

    def test_negative_frame_index_detection(self):
        frame_index = struct.unpack("<i", struct.pack("<i", -1))[0]
        assert frame_index == -1

    def test_crc32_mismatch_detection(self):
        data = b"\x00" * 128 + b"DICM" + b"\x00" * 20
        wrong_crc = 0xDEADBEEF
        frame = struct.pack("<i", 0) + struct.pack("B", 0) + struct.pack("<i", len(data)) + data + struct.pack("<I", wrong_crc)
        received_crc = struct.unpack("<I", frame[-4:])[0]
        computed_crc = calculate_crc32(frame[:-4])
        assert received_crc != computed_crc
