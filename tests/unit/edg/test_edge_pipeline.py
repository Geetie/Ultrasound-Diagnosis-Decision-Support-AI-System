"""边缘端主流程编排（推理 + 温度门控 + 隔离门控 + 上行）单测。"""
from typing import Any, Dict, List

import numpy as np
import pytest

from edge.device_auth import EdgeDeviceAuth
from edge.device_manager import DeviceState, EdgeDeviceManager
from edge.navigation import NavigationInferenceService
from edge.pipeline import EdgePipeline
from edge.protocols import Frame, NavLabel

pytestmark = pytest.mark.track_edg


class _FakeBackend:
    name = "fake-trt"

    def __init__(self):
        self.infer_calls = 0

    def load(self):
        pass

    def infer(self, frame):
        self.infer_calls += 1
        return [
            NavLabel(
                label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9
            )
        ]

    def close(self):
        pass


class _FakeUplink:
    def __init__(self, fail=False):
        self.pushed: List[Any] = []
        self.fail = fail

    async def push_nav_result(self, result) -> bool:
        if self.fail:
            raise ConnectionError("broken pipe")
        self.pushed.append(result)
        return True


class _FakeTransport:
    def __init__(self, fail=False):
        self.fail = fail

    async def post(self, path: str, payload: Dict[str, Any]):
        if self.fail:
            raise ConnectionError("backend unreachable")
        return {"status": "ok"}


def _frame(index: int = 0) -> Frame:
    return Frame(
        frame_id=f"frame_{index}",
        session_id="sess_001",
        index=index,
        pixels=np.zeros((8, 8), dtype=np.uint8),
        received_at_ms=0.0,
    )


def _pipeline(uplink=None, device=None, backend=None):
    service = NavigationInferenceService(backend or _FakeBackend())
    return EdgePipeline(service, uplink=uplink, device_manager=device)


class TestHappyPath:
    @pytest.mark.asyncio
    async def test_frame_produces_and_pushes_result(self):
        uplink = _FakeUplink()
        pipeline = _pipeline(uplink=uplink)

        result = await pipeline.on_frame(_frame())

        assert result is not None
        assert len(uplink.pushed) == 1
        status = pipeline.status()
        assert status["frames_seen"] == 1
        assert status["results_produced"] == 1
        assert status["results_pushed"] == 1

    @pytest.mark.asyncio
    async def test_works_without_uplink(self):
        pipeline = _pipeline()

        result = await pipeline.on_frame(_frame())

        assert result is not None
        assert pipeline.status()["results_pushed"] == 0

    @pytest.mark.asyncio
    async def test_uplink_failure_does_not_break_local_result(self):
        uplink = _FakeUplink(fail=True)
        pipeline = _pipeline(uplink=uplink)

        result = await pipeline.on_frame(_frame())

        # 上行是旁路，本地结果必须照常产出
        assert result is not None
        assert pipeline.status()["results_pushed"] == 0


class TestThermalGating:
    @pytest.mark.asyncio
    async def test_thermal_skip_yields_none_and_no_push(self):
        uplink = _FakeUplink()
        pipeline = _pipeline(uplink=uplink)
        decision = pipeline.on_temperature(82.0)  # 80-85°C → 隔帧
        assert decision.frame_skip == 1

        first = await pipeline.on_frame(_frame(0), now=0.0)
        second = await pipeline.on_frame(_frame(1), now=1.0)

        assert first is not None
        assert second is None
        assert len(uplink.pushed) == 1

    @pytest.mark.asyncio
    async def test_nav_paused_stops_both_inference_and_push(self):
        backend = _FakeBackend()
        uplink = _FakeUplink()
        pipeline = _pipeline(uplink=uplink, backend=backend)
        pipeline.on_temperature(87.0)

        result = await pipeline.on_frame(_frame())

        assert result is None
        assert backend.infer_calls == 0
        assert uplink.pushed == []


class TestIsolationGating:
    @pytest.mark.asyncio
    async def test_isolated_device_infers_locally_but_suppresses_uplink(self):
        backend = _FakeBackend()
        uplink = _FakeUplink()
        device = EdgeDeviceManager(
            EdgeDeviceAuth(device_id="dev_a", api_key="k" * 16),
            _FakeTransport(fail=True),
            failure_threshold=1,
        )
        await device.register()
        assert device.state == DeviceState.ISOLATED

        pipeline = _pipeline(uplink=uplink, device=device, backend=backend)
        result = await pipeline.on_frame(_frame())

        assert result is not None  # 本地仍出结果
        assert backend.infer_calls == 1
        assert uplink.pushed == []  # 但不上行
        assert pipeline.status()["push_suppressed"] == 1
        assert pipeline.should_uplink is False

    @pytest.mark.asyncio
    async def test_uplink_resumes_after_recovery(self):
        uplink = _FakeUplink()
        transport = _FakeTransport(fail=True)
        device = EdgeDeviceManager(
            EdgeDeviceAuth(device_id="dev_a", api_key="k" * 16),
            transport,
            failure_threshold=1,
        )
        await device.register()
        pipeline = _pipeline(uplink=uplink, device=device)

        await pipeline.on_frame(_frame(0), now=0.0)
        assert uplink.pushed == []

        # 隔离 → 恢复需要连续两次成功
        transport.fail = False
        await device.health_probe()
        await device.health_probe()
        assert device.state == DeviceState.ONLINE

        await pipeline.on_frame(_frame(1), now=1.0)
        assert len(uplink.pushed) == 1

    @pytest.mark.asyncio
    async def test_without_device_manager_uplink_always_allowed(self):
        pipeline = _pipeline(uplink=_FakeUplink())

        assert pipeline.should_uplink is True
        assert pipeline.status()["device"] is None


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_aggregates_nav_and_device(self):
        device = EdgeDeviceManager(
            EdgeDeviceAuth(device_id="dev_a", api_key="k" * 16), _FakeTransport()
        )
        await device.register()
        pipeline = _pipeline(uplink=_FakeUplink(), device=device)

        await pipeline.on_frame(_frame())

        status = pipeline.status()
        assert status["navigation"]["frames_in"] == 1
        assert status["device"]["state"] == "online"
        assert status["should_uplink"] is True
