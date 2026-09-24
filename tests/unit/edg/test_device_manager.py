"""T-GB-EDG-02 边缘端设备管理（心跳 + 健康探测 + 隔离恢复）单测。"""
from typing import Any

import pytest

from edge.device_auth import EdgeDeviceAuth
from edge.device_manager import DeviceState, EdgeDeviceManager

pytestmark = pytest.mark.track_edg


class _FakeTransport:
    """记录调用；可切换为持续失败。"""

    def __init__(self, fail=False):
        self.calls: list[dict[str, Any]] = []
        self.fail = fail

    async def post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"path": path, "payload": payload})
        if self.fail:
            raise ConnectionError("backend unreachable")
        return {"status": "ok"}


class _FakeClock:
    def __init__(self, value: float = 0.0):
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _manager(transport, clock=None, **kwargs):
    return EdgeDeviceManager(
        EdgeDeviceAuth(device_id="dev_edge_001", api_key="edge-api-key-0123456789abcdef"),
        transport,
        clock=clock or _FakeClock(),
        **kwargs,
    )


class TestRegistration:
    @pytest.mark.asyncio
    async def test_register_success_moves_to_online(self):
        transport = _FakeTransport()
        manager = _manager(transport)

        assert await manager.register() is True
        assert manager.state == DeviceState.ONLINE
        assert manager.should_uplink is True
        assert transport.calls[0]["path"] == "/api/v1/admin/device/register"
        assert transport.calls[0]["payload"]["device_id"] == "dev_edge_001"

    @pytest.mark.asyncio
    async def test_register_failure_degrades_but_stays_usable(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport)

        assert await manager.register() is False
        assert manager.state == DeviceState.DEGRADED
        assert manager.should_uplink is True  # 偶发失败不隔离


class TestIsolation:
    @pytest.mark.asyncio
    async def test_three_consecutive_failures_isolate(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport)

        await manager.register()
        await manager.send_heartbeat()
        await manager.health_probe()

        assert manager.state == DeviceState.ISOLATED
        assert manager.is_isolated is True
        assert manager.should_uplink is False
        assert manager.health.consecutive_failures == 3
        assert manager.health.isolated_reason is not None

    @pytest.mark.asyncio
    async def test_custom_failure_threshold(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport, failure_threshold=1)

        await manager.register()

        assert manager.state == DeviceState.ISOLATED

    @pytest.mark.asyncio
    async def test_recovery_requires_two_successes(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport)
        await manager.register()
        await manager.send_heartbeat()
        await manager.health_probe()
        assert manager.is_isolated is True

        # 半开：第一次成功只进入 RECOVERING，仍不上行
        transport.fail = False
        assert await manager.health_probe() is True
        assert manager.state == DeviceState.RECOVERING
        assert manager.should_uplink is False

        # 第二次成功才恢复上行
        assert await manager.health_probe() is True
        assert manager.state == DeviceState.ONLINE
        assert manager.should_uplink is True
        assert manager.health.isolated_reason is None

    @pytest.mark.asyncio
    async def test_failure_during_recovery_reisolates(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport)
        await manager.register()
        await manager.send_heartbeat()
        await manager.health_probe()

        transport.fail = False
        await manager.health_probe()
        assert manager.state == DeviceState.RECOVERING

        transport.fail = True
        await manager.health_probe()

        assert manager.state == DeviceState.ISOLATED


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_first_heartbeat_sent_then_throttled(self):
        transport = _FakeTransport()
        clock = _FakeClock()
        manager = _manager(transport, clock=clock)

        assert await manager.maybe_heartbeat() is True
        assert await manager.maybe_heartbeat() is False  # 15s 内不重发

        clock.advance(15.0)
        assert await manager.maybe_heartbeat() is True

    @pytest.mark.asyncio
    async def test_heartbeat_interval_configurable(self):
        transport = _FakeTransport()
        clock = _FakeClock()
        manager = _manager(transport, clock=clock, heartbeat_interval_seconds=5.0)

        await manager.maybe_heartbeat()
        clock.advance(5.0)

        assert await manager.maybe_heartbeat() is True

    @pytest.mark.asyncio
    async def test_heartbeat_payload_carries_state(self):
        transport = _FakeTransport()
        manager = _manager(transport)

        await manager.send_heartbeat()

        assert transport.calls[0]["path"] == "/api/v1/admin/device/heartbeat"
        assert transport.calls[0]["payload"]["state"] == "registered"

    @pytest.mark.asyncio
    async def test_heartbeat_interval_default_is_15s(self):
        manager = _manager(_FakeTransport())

        assert manager.heartbeat_interval_seconds == 15.0
        assert manager.health_check_interval_seconds == 1.0


class TestHealthProbe:
    @pytest.mark.asyncio
    async def test_probe_success_resets_failures(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport)
        await manager.register()
        assert manager.health.consecutive_failures == 1

        transport.fail = False
        assert await manager.health_probe() is True
        assert manager.health.consecutive_failures == 0
        assert manager.state == DeviceState.ONLINE


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_clears_state(self):
        transport = _FakeTransport(fail=True)
        manager = _manager(transport, failure_threshold=1)
        await manager.register()
        assert manager.is_isolated is True

        manager.reset()

        assert manager.state == DeviceState.REGISTERED
        assert manager.health.consecutive_failures == 0
        assert manager.health.isolated_reason is None
        assert manager.should_uplink is False


class TestHealthSnapshot:
    @pytest.mark.asyncio
    async def test_as_dict_is_serializable(self):
        manager = _manager(_FakeTransport())
        await manager.register()

        payload = manager.health.as_dict()

        assert payload["state"] == "online"
        assert payload["should_uplink"] is True
