"""边缘端常驻服务入口：把 EDG 各模块组装成一个可运行进程。

    python -m edge

组装关系：

    EdgeService
    ├── EdgeDeviceManager  设备注册/心跳/健康探测/隔离恢复（T-GB-EDG-02）
    ├── EdgeWebSocketClient 上行双通道 + 认证 + 重连（T-GA-EDG-03）
    ├── DicomSCPService    C-STORE 接收 → 解码 → 帧回调（T-GA-EDG-01）
    ├── NavigationInferenceService + nav_backend（T-GA-EDG-02）
    ├── EdgePipeline       帧→推理→本地校验→上行 + 温度/隔离双门控
    └── OTAClient          模型包下载（T-GB-EDG-01，按需调用）

启动策略是 **best-effort + 显式状态**：DICOM SCP 起不来（缺 pynetdicom）、
上行连不上（后端重启中）都不阻断进程启动——边缘端停机意味着医生失去辅助，
比"降级运行"更糟。所有降级原因都进 ``status()``，由运维侧观测。
"""
from __future__ import annotations

import asyncio
import contextlib
import signal
from typing import Any

import httpx
import structlog

from edge.config import EdgeConfigError, EdgeSettings
from edge.device_auth import EdgeDeviceAuth
from edge.device_manager import EdgeDeviceManager
from edge.dicom_scp import DicomSCPService
from edge.image_quality import create_image_quality_adapter
from edge.nav_backend import create_nav_backend
from edge.navigation import NavigationInferenceService
from edge.ota_client import OTAClient
from edge.pipeline import EdgePipeline
from edge.plane_assessment import create_plane_assessment_adapter
from edge.protocols import Frame, NavResult
from edge.safety import LocalSafetyChecker
from edge.websocket_server import NAV_CHANNEL_PATH, EdgeWebSocketClient

logger = structlog.get_logger(__name__)

DEFAULT_HTTP_TIMEOUT_SECONDS = 5.0


class HttpDeviceTransport:
    """设备管理用的 HTTP 传输（带设备认证头）。"""

    def __init__(
        self,
        base_url: str,
        auth: EdgeDeviceAuth,
        timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._client = httpx.AsyncClient(timeout=timeout_seconds)

    async def post(self, path: str, payload: dict[str, Any]) -> Any:
        response = await self._client.post(
            f"{self._base_url}{path}",
            json=payload,
            headers=self._auth.build_headers(),
        )
        response.raise_for_status()
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:  # silent-ok: 非 JSON 响应按原文回传（上层按 raw 字段处理，非丢数据）
            return {"raw": response.text}

    async def close(self) -> None:
        await self._client.aclose()


class EdgeService:
    """边缘端常驻服务（组装 + 生命周期 + 内部巡检循环）。"""

    def __init__(
        self,
        settings: EdgeSettings,
        *,
        pipeline: EdgePipeline,
        device_manager: EdgeDeviceManager,
        uplink: EdgeWebSocketClient | None = None,
        dicom_scp: DicomSCPService | None = None,
        ota_client: OTAClient | None = None,
        transport: HttpDeviceTransport | None = None,
    ) -> None:
        self.settings = settings
        self._pipeline = pipeline
        self._device = device_manager
        self._uplink = uplink
        self._dicom_scp = dicom_scp
        self._ota = ota_client
        self._transport = transport

        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._tasks: list[asyncio.Task] = []
        self._started = False
        self._dicom_error: str | None = None

    # ---- 生命周期 ----
    async def start(self) -> None:
        """启动服务（best-effort：任何子组件失败都只降级，不阻断启动）。"""
        if self._started:
            return

        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        # 1) 设备注册（失败也继续：可能是后端在重启，隔离逻辑会兜住）
        registered = await self._device.register()
        logger.info("edge_service_register_attempt", registered=registered)

        # 2) DICOM SCP（缺 pynetdicom / 端口占用 → 记录原因后继续）
        if self._dicom_scp is not None:
            # 帧回调在启动时挂上：任何构造路径（含直接 new）都能拿到回调
            self._dicom_scp.set_frame_handler(self.on_dicom_frame)
            try:
                await self._dicom_scp.start()
                self._dicom_error = None
            except Exception as e:
                self._dicom_error = str(e)
                logger.error("edge_dicom_scp_start_failed", error=str(e))

        # 3) 内部循环：上行保活 / 心跳 / 健康探测
        self._tasks = [
            asyncio.create_task(self._uplink_loop()),
            asyncio.create_task(self._heartbeat_loop()),
            asyncio.create_task(self._health_loop()),
        ]

        self._started = True
        logger.info("edge_service_started", **self._settings_snapshot())

    async def stop(self) -> None:
        """优雅停止：先停循环，再关外部连接。"""
        if not self._started:
            return

        if self._stop_event is not None:
            self._stop_event.set()

        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []

        if self._dicom_scp is not None:
            # silent-ok: 停机收尾的幂等关闭（边端进程即将退出，失败无补救意义）
            with contextlib.suppress(Exception):  # silent-ok: 停机幂等关闭（进程即将退出）
                await self._dicom_scp.stop()

        if self._uplink is not None:
            with contextlib.suppress(Exception):  # silent-ok: 停机幂等关闭（同上）
                await self._uplink.close()

        if self._transport is not None:
            with contextlib.suppress(Exception):  # silent-ok: 停机幂等关闭（同上）
                await self._transport.close()

        self._started = False
        logger.info("edge_service_stopped", device_id=self.settings.device_id)

    async def run_forever(self) -> None:
        """启动并等待停止信号（SIGINT/SIGTERM）。"""
        await self.start()
        assert self._stop_event is not None

        self._install_signal_handlers()
        try:
            await self._stop_event.wait()
        finally:
            await self.stop()

    def request_stop(self) -> None:
        """请求停止（信号处理与测试用）。"""
        if self._stop_event is not None:
            self._stop_event.set()

    def _install_signal_handlers(self) -> None:
        """注册退出信号。Windows 不支持 loop.add_signal_handler，退回 signal。"""
        assert self._loop is not None

        def _handler(signum, _frame):  # pragma: no cover - 依赖真实信号
            logger.info("edge_service_stop_signal", signal=signum)
            self._loop.call_soon_threadsafe(self.request_stop)

        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:
                continue
            try:
                self._loop.add_signal_handler(sig, self.request_stop)
            except NotImplementedError:  # silent-ok: Windows 不支持 loop.add_signal_handler，走 signal 回退
                with contextlib.suppress(ValueError):  # silent-ok: 非主线程装 signal 会抛（回退不可用）
                    signal.signal(sig, _handler)

    # ---- 帧处理 ----
    async def handle_frame(
        self, frame: Frame, now: float | None = None
    ) -> NavResult | None:
        """处理一帧（业务入口；DICOM 回调也会走到这里）。"""
        try:
            return await self._pipeline.on_frame(frame, now=now)
        except Exception as e:
            # 单帧异常不能拖垮常驻服务
            logger.error(
                "edge_frame_processing_failed", frame_id=frame.frame_id, error=str(e)
            )
            return None

    def on_dicom_frame(self, frame: Frame) -> None:
        """DICOM SCP 回调（运行在 SCP 线程）→ 调度回主事件循环。"""
        loop = self._loop
        if loop is None or loop.is_closed():
            logger.warning("edge_frame_dropped_no_loop", frame_id=frame.frame_id)
            return
        asyncio.run_coroutine_threadsafe(self.handle_frame(frame), loop)

    async def download_package(
        self, package: str, version: str, **kwargs: Any
    ):
        """下载 OTA 包（按需调用，不做后台轮询）。"""
        if self._ota is None:
            raise RuntimeError("OTA 未启用（EDGE_ENABLE_OTA=false 或未配置下载目录）")
        return await self._ota.download(package, version, **kwargs)

    # ---- 内部循环 ----
    async def _uplink_loop(self) -> None:
        """上行保活：断线则按配置间隔重连（connect 内部自带指数退避）。"""
        assert self._stop_event is not None
        if self._uplink is None:
            return

        while not self._stop_event.is_set():
            if not self._uplink.connected:
                connected = await self._uplink.connect()
                if not connected:
                    logger.warning(
                        "edge_uplink_unavailable",
                        url=self._uplink.url,
                        retry_in_seconds=self.settings.reconnect_interval_seconds,
                    )
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.reconnect_interval_seconds,
                )
            except asyncio.TimeoutError:  # silent-ok: 等待间隔到点即进入下一轮（循环节拍，非故障）
                continue

    async def _heartbeat_loop(self) -> None:
        """心跳循环（15s 周期由 DeviceManager.maybe_heartbeat 内部节流）。"""
        assert self._stop_event is not None
        interval = min(5.0, self.settings.heartbeat_interval_seconds) or 5.0

        while not self._stop_event.is_set():
            await self._device.maybe_heartbeat()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:  # silent-ok: 循环节拍（同上）
                continue

    async def _health_loop(self) -> None:
        """健康探测循环（默认 1s；隔离期也照跑，用于判断能否恢复）。"""
        assert self._stop_event is not None
        while not self._stop_event.is_set():
            await self._device.health_probe()
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self.settings.health_check_interval_seconds,
                )
            except asyncio.TimeoutError:  # silent-ok: 循环节拍（同上）
                continue

    # ---- 观测 ----
    def _settings_snapshot(self) -> dict[str, Any]:
        return self.settings.as_dict()

    def status(self) -> dict[str, Any]:
        return {
            "started": self._started,
            "device_id": self.settings.device_id,
            "device": self._device.health.as_dict(),
            "uplink": {
                "configured": self._uplink is not None,
                "connected": bool(self._uplink and self._uplink.connected),
                "url": self._uplink.url if self._uplink else None,
                "stats": self._uplink.stats.__dict__ if self._uplink else None,
            },
            "dicom_scp": {
                "configured": self._dicom_scp is not None,
                "started": bool(self._dicom_scp and self._dicom_scp.started),
                "ae_title": self._dicom_scp.ae_title if self._dicom_scp else None,
                "port": self._dicom_scp.port if self._dicom_scp else None,
                "error": self._dicom_error,
                "latency": self._dicom_scp.latency_stats() if self._dicom_scp else None,
            },
            "ota": {"configured": self._ota is not None},
            "pipeline": self._pipeline.status(),
            "tasks_alive": sum(1 for task in self._tasks if not task.done()),
        }


def build_edge_service(
    settings: EdgeSettings,
    *,
    uplink: EdgeWebSocketClient | None = None,
    dicom_scp: DicomSCPService | None = None,
    transport: HttpDeviceTransport | None = None,
    nav_service: NavigationInferenceService | None = None,
) -> EdgeService:
    """按配置组装边缘端服务（可注入替换以实现离线测试/离线评估）。"""
    settings.validate()

    auth = EdgeDeviceAuth(device_id=settings.device_id, api_key=settings.api_key)
    transport = transport or HttpDeviceTransport(settings.main_service_url, auth)

    device_manager = EdgeDeviceManager(
        auth,
        transport,
        heartbeat_interval_seconds=settings.heartbeat_interval_seconds,
        health_check_interval_seconds=settings.health_check_interval_seconds,
        failure_threshold=settings.failure_threshold,
    )

    uplink = uplink or EdgeWebSocketClient(
        settings.ws_url, auth, channel_path=NAV_CHANNEL_PATH
    )

    if nav_service is None:
        backend = create_nav_backend(
            settings.engine_path, labels=settings.model_labels
        )
        nav_service = NavigationInferenceService(
            backend,
            safety_checker=LocalSafetyChecker(),
            target_fps=settings.nav_target_fps,
            # M1/M6：未部署评估头时为 null 适配器（不产出字段）
            plane_assessment_adapter=create_plane_assessment_adapter(
                settings.plane_assessment_adapter
            ),
            image_quality_adapter=create_image_quality_adapter(
                settings.image_quality_adapter
            ),
            organ_code=settings.organ_code,
        )

    pipeline = EdgePipeline(nav_service, uplink=uplink, device_manager=device_manager)

    if dicom_scp is None and settings.enable_dicom_scp:
        dicom_scp = DicomSCPService(
            ae_title=settings.ae_title,
            port=settings.dicom_port,
        )

    ota_client = None
    if settings.enable_ota:
        ota_client = OTAClient(
            settings.main_service_url,
            auth,
            download_dir=settings.download_dir,
            rate_limit_bytes_per_second=settings.ota_rate_limit_bytes_per_second,
        )

    service = EdgeService(
        settings,
        pipeline=pipeline,
        device_manager=device_manager,
        uplink=uplink,
        dicom_scp=dicom_scp,
        ota_client=ota_client,
        transport=transport,
    )
    return service


def main() -> int:
    """命令行入口（``python -m edge``）。"""
    settings = EdgeSettings.from_env()
    try:
        settings.validate()
    except EdgeConfigError as e:
        logger.error("edge_config_invalid", error=str(e))
        return 2

    logger.info("edge_service_booting", **settings.as_dict())
    service = build_edge_service(settings)

    try:
        asyncio.run(service.run_forever())
    except KeyboardInterrupt:  # pragma: no cover - 人工中断
        logger.info("edge_service_interrupted")
    return 0


__all__ = ["DEFAULT_HTTP_TIMEOUT_SECONDS", "EdgeService", "HttpDeviceTransport", "build_edge_service", "main"]
