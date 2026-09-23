"""边缘端常驻服务（配置 + 组装 + 生命周期 + 降级）单测。"""
import asyncio
from unittest.mock import AsyncMock

import numpy as np
import pytest

from edge.config import EdgeConfigError, EdgeSettings
from edge.device_auth import EdgeDeviceAuth
from edge.device_manager import DeviceState, EdgeDeviceManager
from edge.main import EdgeService, build_edge_service, main
from edge.nav_backend import NullNavBackend
from edge.navigation import NavigationInferenceService
from edge.pipeline import EdgePipeline
from edge.protocols import Frame, NavLabel
from edge.websocket_server import EdgeWebSocketClient

pytestmark = pytest.mark.track_edg

API_KEY = "edge-api-key-0123456789abcdef"


# ---------------- 测试替身 ----------------
class _FakeTransport:
    def __init__(self, fail=False):
        self.fail = fail
        self.calls = []

    async def post(self, path, payload):
        self.calls.append((path, payload))
        if self.fail:
            raise ConnectionError("backend unreachable")
        return {"status": "ok"}

    async def close(self):
        self.closed = True


class _FakeConnection:
    def __init__(self):
        self.sent = []
        self.closed = False

    async def send(self, message):
        self.sent.append(message)

    async def close(self):
        self.closed = True


class _FakeBackend:
    name = "fake-trt"

    def load(self):
        pass

    def infer(self, frame):
        return [
            NavLabel(
                label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9
            )
        ]

    def close(self):
        pass


class _FakeDicomSCP:
    """假 SCP（真机需要 pynetdicom）。"""

    def __init__(self, fail=False):
        self.fail = fail
        self.ae_title = "ULTRASOUND_EDGE"
        self.port = 11112
        self.started = False
        self.stopped = False
        self.handler = None

    def set_frame_handler(self, handler):
        self.handler = handler

    async def start(self):
        if self.fail:
            raise RuntimeError("pynetdicom 未安装")
        self.started = True

    async def stop(self):
        self.stopped = True

    def latency_stats(self):
        return {"p50_ms": None, "p99_ms": None, "samples": 0}


def _frame(index=0):
    return Frame(
        frame_id=f"frame_{index}",
        session_id="sess_001",
        index=index,
        pixels=np.zeros((8, 8), dtype=np.uint8),
        received_at_ms=0.0,
    )


def _settings(**overrides):
    base = dict(
        device_id="dev_edge_001",
        api_key=API_KEY,
        enable_dicom_scp=False,
        enable_ota=False,
        heartbeat_interval_seconds=0.01,
        health_check_interval_seconds=0.01,
        reconnect_interval_seconds=0.01,
    )
    base.update(overrides)
    return EdgeSettings(**base)


def _service(settings=None, transport=None, dicom=None):
    settings = settings or _settings()
    transport = transport or _FakeTransport()
    auth = EdgeDeviceAuth(device_id=settings.device_id, api_key=settings.api_key)
    device = EdgeDeviceManager(
        auth,
        transport,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        health_check_interval_seconds=settings.health_check_interval_seconds,
        failure_threshold=settings.failure_threshold,
    )
    uplink = EdgeWebSocketClient(
        settings.ws_url, auth, connect_factory=AsyncMock(return_value=_FakeConnection())
    )
    nav = NavigationInferenceService(_FakeBackend())
    pipeline = EdgePipeline(nav, uplink=uplink, device_manager=device)
    service = EdgeService(
        settings,
        pipeline=pipeline,
        device_manager=device,
        uplink=uplink,
        dicom_scp=dicom,
        ota_client=None,
        transport=transport,
    )
    return service, transport


# ---------------- 配置 ----------------
class TestEdgeSettings:
    def test_defaults_from_empty_env(self):
        settings = EdgeSettings.from_env({})

        assert settings.main_service_url == "http://localhost:8000"
        assert settings.ws_url == "ws://localhost:8000"
        assert settings.dicom_port == 11112
        assert settings.nav_target_fps == 15.0
        assert settings.heartbeat_interval_seconds == 15.0
        assert settings.health_check_interval_seconds == 1.0
        assert settings.enable_dicom_scp is True

    def test_env_overrides(self):
        settings = EdgeSettings.from_env(
            {
                "EDGE_MAIN_SERVICE_URL": "http://backend:9000/",
                "EDGE_DEVICE_ID": "dev_42",
                "EDGE_API_KEY": "secret",
                "EDGE_DICOM_PORT": "11113",
                "EDGE_ENABLE_DICOM_SCP": "false",
                "EDGE_NAV_TARGET_FPS": "10",
                "EDGE_ENGINE_PATH": "/opt/nav.plan",
            }
        )

        assert settings.main_service_url == "http://backend:9000/"
        assert settings.device_id == "dev_42"
        assert settings.dicom_port == 11113
        assert settings.enable_dicom_scp is False
        assert settings.nav_target_fps == 10.0
        assert settings.engine_path == "/opt/nav.plan"

    def test_csv_labels_parsed(self):
        settings = EdgeSettings.from_env({"EDGE_MODEL_LABELS": "a, b ,c"})

        assert settings.model_labels == ("a", "b", "c")

    def test_invalid_numeric_falls_back_to_default(self):
        settings = EdgeSettings.from_env({"EDGE_DICOM_PORT": "not-a-number"})

        assert settings.dicom_port == 11112

    @pytest.mark.parametrize(
        "overrides,fragment",
        [
            ({"device_id": ""}, "EDGE_DEVICE_ID"),
            ({"api_key": ""}, "EDGE_API_KEY"),
            ({"dicom_port": 0, "enable_dicom_scp": True}, "EDGE_DICOM_PORT"),
            ({"nav_target_fps": 0}, "EDGE_NAV_TARGET_FPS"),
            ({"ota_rate_limit_bytes_per_second": -1}, "EDGE_OTA_RATE_LIMIT_BPS"),
            ({"health_check_interval_seconds": 0}, "EDGE_HEALTH_CHECK_INTERVAL_SECONDS"),
        ],
    )
    def test_validate_rejects_bad_config(self, overrides, fragment):
        settings = _settings(**overrides)

        with pytest.raises(EdgeConfigError) as excinfo:
            settings.validate()

        assert fragment in str(excinfo.value)

    def test_as_dict_masks_api_key(self):
        payload = _settings().as_dict()

        assert payload["api_key_configured"] is True
        assert "api_key" not in payload

    def test_main_returns_2_on_invalid_config(self, monkeypatch):
        monkeypatch.setenv("EDGE_DEVICE_ID", "")
        monkeypatch.setenv("EDGE_API_KEY", "")

        assert main() == 2


# ---------------- 生命周期 ----------------
class TestServiceLifecycle:
    @pytest.mark.asyncio
    async def test_start_registers_device_and_starts_loops(self):
        service, transport = _service()

        await service.start()
        try:
            assert service.status()["started"] is True
            assert any(path == "/api/v1/admin/device/register" for path, _ in transport.calls)
            assert service.status()["tasks_alive"] == 3
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        service, transport = _service()

        await service.start()
        await service.start()
        try:
            registers = [c for c in transport.calls if c[0].endswith("/register")]
            assert len(registers) == 1
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_loops_and_closes_uplink(self):
        service, transport = _service()
        await service.start()

        await service.stop()

        assert service.status()["started"] is False
        assert service.status()["tasks_alive"] == 0
        assert getattr(transport, "closed", False) is True

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        service, _ = _service()

        await service.stop()

        assert service.status()["started"] is False

    @pytest.mark.asyncio
    async def test_run_forever_exits_on_stop_request(self):
        service, _ = _service()

        task = asyncio.create_task(service.run_forever())
        for _ in range(50):
            if service.status()["started"]:
                break
            await asyncio.sleep(0.01)

        service.request_stop()
        await asyncio.wait_for(task, timeout=5.0)

        assert service.status()["started"] is False

    @pytest.mark.asyncio
    async def test_heartbeat_and_health_loops_run(self):
        transport = _FakeTransport()
        service, _ = _service(transport=transport)
        await service.start()
        try:
            await asyncio.sleep(0.1)
        finally:
            await service.stop()

        paths = [path for path, _ in transport.calls]
        assert any(path.endswith("/heartbeat") for path in paths)
        assert any(path.endswith("/health") for path in paths)


# ---------------- 降级 ----------------
class TestDegradation:
    @pytest.mark.asyncio
    async def test_dicom_scp_failure_does_not_block_start(self):
        dicom = _FakeDicomSCP(fail=True)
        service, _ = _service(dicom=dicom)

        await service.start()
        try:
            status = service.status()
            assert status["started"] is True
            assert status["dicom_scp"]["started"] is False
            assert "pynetdicom" in status["dicom_scp"]["error"]
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_dicom_scp_started_when_available(self):
        dicom = _FakeDicomSCP()
        service, _ = _service(dicom=dicom)

        await service.start()
        try:
            assert service.status()["dicom_scp"]["started"] is True
            assert dicom.handler is not None
        finally:
            await service.stop()
        assert dicom.stopped is True

    @pytest.mark.asyncio
    async def test_backend_unreachable_keeps_service_running(self):
        service, _ = _service(transport=_FakeTransport(fail=True))

        await service.start()
        try:
            status = service.status()
            assert status["started"] is True
            assert status["device"]["state"] == DeviceState.DEGRADED.value
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_offline_frames_are_processed_locally(self):
        service, _ = _service(transport=_FakeTransport(fail=True))
        await service.start()
        try:
            result = await service.handle_frame(_frame())
            assert result is not None  # 后端不可达不影响本地推理
        finally:
            await service.stop()


# ---------------- 帧处理 ----------------
class TestFrameHandling:
    @pytest.mark.asyncio
    async def test_handle_frame_delegates_to_pipeline(self):
        service, _ = _service()
        await service.start()
        try:
            result = await service.handle_frame(_frame(), now=0.0)
            assert result is not None
            assert service.status()["pipeline"]["results_produced"] == 1
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_handle_frame_swallows_exceptions(self):
        class _RaisingBackend(_FakeBackend):
            def infer(self, frame):
                raise RuntimeError("inference exploded")

        transport = _FakeTransport()
        auth = EdgeDeviceAuth(device_id="dev_edge_001", api_key=API_KEY)
        device = EdgeDeviceManager(auth, transport)
        nav = NavigationInferenceService(_RaisingBackend())
        service = EdgeService(
            _settings(enable_dicom_scp=False, enable_ota=False),
            pipeline=EdgePipeline(nav),
            device_manager=device,
            transport=transport,
        )

        await service.start()
        try:
            # 单帧推理抛错不能拖垮常驻服务
            assert await service.handle_frame(_frame(), now=0.0) is None
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_dicom_callback_schedules_onto_running_loop(self):
        dicom = _FakeDicomSCP()
        service, _ = _service(dicom=dicom)
        await service.start()
        try:
            service.on_dicom_frame(_frame())  # 模拟 SCP 线程回调
            for _ in range(50):
                if service.status()["pipeline"]["frames_seen"] >= 1:
                    break
                await asyncio.sleep(0.01)
            assert service.status()["pipeline"]["frames_seen"] == 1
        finally:
            await service.stop()

    @pytest.mark.asyncio
    async def test_download_package_without_ota_raises(self):
        service, _ = _service()

        with pytest.raises(RuntimeError):
            await service.download_package("nav-model", "v1")


# ---------------- 组装 ----------------
class TestBuildEdgeService:
    def test_build_without_engine_uses_null_backend(self, tmp_path):
        service = build_edge_service(_settings(download_dir=str(tmp_path)))

        assert service.status()["pipeline"]["navigation"]["backend"] == "null"

    def test_build_wires_dicom_scp_and_ota(self, tmp_path):
        settings = _settings(
            enable_dicom_scp=True, enable_ota=True, download_dir=str(tmp_path)
        )

        service = build_edge_service(settings)
        status = service.status()

        assert status["dicom_scp"]["configured"] is True
        assert status["ota"]["configured"] is True
        assert status["uplink"]["configured"] is True

    def test_build_disables_optional_components(self):
        service = build_edge_service(_settings(enable_dicom_scp=False, enable_ota=False))
        status = service.status()

        assert status["dicom_scp"]["configured"] is False
        assert status["ota"]["configured"] is False

    def test_build_validates_settings(self):
        with pytest.raises(EdgeConfigError):
            build_edge_service(_settings(device_id=""))

    def test_build_uses_null_backend_when_engine_missing(self, tmp_path):
        service = build_edge_service(_settings(engine_path=str(tmp_path / "nope.plan")))

        assert service.status()["pipeline"]["navigation"]["backend"] == "null"
