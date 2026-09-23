"""T-GA-EDG-03 边缘端 WebSocket 客户端（双通道 + 认证 + 重连）单测。"""
import json
from unittest.mock import AsyncMock

import numpy as np
import pytest

from edge.device_auth import HEADER_DEVICE_ID, HEADER_DEVICE_TOKEN, EdgeDeviceAuth
from edge.protocols import Frame, NavLabel, NavResult
from edge.websocket_server import (
    DICOM_CHANNEL_PATH,
    NAV_CHANNEL_PATH,
    EdgeWebSocketClient,
)

pytestmark = pytest.mark.track_edg


class _FakeConnection:
    def __init__(self, fail_on_send=False):
        self.sent = []
        self.closed = False
        self.fail_on_send = fail_on_send

    async def send(self, message):
        if self.fail_on_send:
            raise ConnectionError("broken pipe")
        self.sent.append(message)

    async def close(self):
        self.closed = True


def _auth():
    return EdgeDeviceAuth(device_id="dev_edge_001", api_key="edge-api-key-0123456789abcdef")


def _frame(index=0):
    return Frame(
        frame_id=f"frame_{index}",
        session_id="sess_001",
        index=index,
        pixels=np.zeros((2, 2), dtype=np.uint8),
        received_at_ms=0.0,
    )


def _nav_result():
    return NavResult(
        frame_id="frame_0",
        session_id="sess_001",
        labels=[NavLabel(label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9)],
        infer_latency_ms=12.5,
        backend="fake-trt",
    )


class TestUrlAndAuth:
    def test_url_joins_base_and_channel(self):
        client = EdgeWebSocketClient("ws://backend:8000", _auth())

        assert client.url == f"ws://backend:8000{NAV_CHANNEL_PATH}"

    def test_trailing_slash_normalized(self):
        client = EdgeWebSocketClient("ws://backend:8000/", _auth())

        assert client.url == f"ws://backend:8000{NAV_CHANNEL_PATH}"

    def test_dicom_channel_path(self):
        client = EdgeWebSocketClient("ws://backend:8000", _auth(), channel_path=DICOM_CHANNEL_PATH)

        assert client.url.endswith(DICOM_CHANNEL_PATH)

    @pytest.mark.asyncio
    async def test_connect_passes_device_auth_headers(self):
        captured = {}

        async def _factory(url, headers):
            captured["url"] = url
            captured["headers"] = headers
            return _FakeConnection()

        client = EdgeWebSocketClient("ws://backend:8000", _auth(), connect_factory=_factory)

        assert await client.connect() is True
        assert captured["headers"][HEADER_DEVICE_ID] == "dev_edge_001"
        assert HEADER_DEVICE_TOKEN in captured["headers"]


class TestReconnect:
    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        attempts = {"n": 0}

        async def _factory(url, headers):
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionError("refused")
            return _FakeConnection()

        client = EdgeWebSocketClient(
            "ws://backend:8000",
            _auth(),
            connect_factory=_factory,
            reconnect_base_seconds=0.0,
            reconnect_max_seconds=0.0,
        )

        assert await client.connect() is True
        assert attempts["n"] == 3
        assert client.stats.connect_failures == 2

    @pytest.mark.asyncio
    async def test_gives_up_after_max_attempts(self):
        async def _factory(url, headers):
            raise ConnectionError("refused")

        client = EdgeWebSocketClient(
            "ws://backend:8000",
            _auth(),
            connect_factory=_factory,
            max_reconnect_attempts=2,
            reconnect_base_seconds=0.0,
            reconnect_max_seconds=0.0,
        )

        assert await client.connect() is False
        assert client.stats.connect_failures == 2


class TestSending:
    @pytest.mark.asyncio
    async def test_push_nav_result_serializes_labels(self):
        connection = _FakeConnection()
        client = EdgeWebSocketClient(
            "ws://backend:8000", _auth(), connect_factory=AsyncMock(return_value=connection)
        )
        await client.connect()

        assert await client.push_nav_result(_nav_result()) is True

        payload = json.loads(connection.sent[0])
        assert payload["type"] == "nav_result"
        assert payload["frame_id"] == "frame_0"
        assert payload["backend"] == "fake-trt"
        assert payload["labels"][0]["label"] == "organ_name"
        assert client.stats.sent_nav == 1

    @pytest.mark.asyncio
    async def test_push_frame_includes_payload(self):
        connection = _FakeConnection()
        client = EdgeWebSocketClient(
            "ws://backend:8000", _auth(), connect_factory=AsyncMock(return_value=connection)
        )
        await client.connect()

        assert await client.push_frame(_frame(), {"patient_id": "P******"}) is True

        payload = json.loads(connection.sent[0])
        assert payload["type"] == "dicom_frame"
        assert payload["payload"] == {"patient_id": "P******"}
        assert client.stats.sent_frames == 1

    @pytest.mark.asyncio
    async def test_offline_send_is_queued_and_flushed_on_reconnect(self):
        connection = _FakeConnection()
        client = EdgeWebSocketClient("ws://backend:8000", _auth())

        # 未连接 → 入队
        assert await client.push_nav_result(_nav_result()) is False
        assert len(client._queue) == 1

        # 重连后发送会把积压冲掉
        client._connection = connection
        assert await client.push_nav_result(_nav_result()) is True
        assert len(connection.sent) == 2
        assert len(client._queue) == 0

    @pytest.mark.asyncio
    async def test_queue_drops_oldest_when_full(self):
        client = EdgeWebSocketClient("ws://backend:8000", _auth(), queue_maxlen=2)

        for index in range(3):
            await client.push_frame(_frame(index), {"index": index})

        assert len(client._queue) == 2
        assert client.stats.dropped == 1
        # 最旧的（index=0）被丢弃
        assert json.loads(client._queue[0])["index"] == 1

    @pytest.mark.asyncio
    async def test_send_failure_marks_disconnected_and_requeues(self):
        connection = _FakeConnection(fail_on_send=True)
        client = EdgeWebSocketClient("ws://backend:8000", _auth())
        client._connection = connection

        assert await client.push_nav_result(_nav_result()) is False
        assert client.connected is False
        assert len(client._queue) == 1
        assert client.stats.reconnects == 1


class TestClose:
    @pytest.mark.asyncio
    async def test_close_clears_connection(self):
        connection = _FakeConnection()
        client = EdgeWebSocketClient(
            "ws://backend:8000", _auth(), connect_factory=AsyncMock(return_value=connection)
        )
        await client.connect()

        await client.close()

        assert connection.closed is True
        assert client.connected is False

    @pytest.mark.asyncio
    async def test_close_without_connection_is_noop(self):
        client = EdgeWebSocketClient("ws://backend:8000", _auth())

        await client.close()

        assert client.connected is False
