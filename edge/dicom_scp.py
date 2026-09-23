"""T-GA-EDG-01 边缘端 DICOM SCP 服务。

方案（`开发计划.md` T-GA-EDG-01）：pynetdicom 负责 DICOM 网络通信
（C-ECHO/C-STORE），python-gdcm 负责像素解码；两者都只在边缘设备上安装，
因此这里采用懒加载 + 注入式解码器：

    - `GdcmPixelDecoder`：python-gdcm（边缘设备上首选，支持压缩传输语法）
    - `PydicomPixelDecoder`：pydicom 兜底（开发机可用，便于单测）

规格：帧接收 P99 < 50ms（从数据集到达回调到帧交付）。
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, Optional, Protocol, Tuple

import numpy as np
import structlog

from edge.protocols import Frame

logger = structlog.get_logger(__name__)

DEFAULT_AE_TITLE = "ULTRASOUND_EDGE"
DEFAULT_PORT = 11112
DEFAULT_LATENCY_WINDOW = 512

# pynetdicom 状态码
STATUS_SUCCESS = 0x0000
STATUS_PROCESSING_FAILURE = 0xC000


class PixelDecodeError(Exception):
    """像素解码失败。"""


class PixelDecoder(Protocol):
    """DICOM 像素解码器。"""

    name: str

    def decode(self, dataset: Any) -> np.ndarray:
        """把 pydicom Dataset 解成二维灰度数组（uint8 或原始位深）。"""


class PydicomPixelDecoder:
    """pydicom 兜底解码器（未压缩/常见传输语法可直接用）。"""

    name = "pydicom"

    def decode(self, dataset: Any) -> np.ndarray:
        try:
            pixels = dataset.pixel_array
        except Exception as e:  # pydicom 对缺失/压缩像素会抛多种异常
            raise PixelDecodeError(f"pydicom 解码失败: {e}") from e
        return np.asarray(pixels)


class GdcmPixelDecoder:
    """python-gdcm 解码器（边缘设备首选，支持压缩传输语法）。"""

    name = "gdcm"

    def __init__(self) -> None:
        try:
            import gdcm  # noqa: F401
        except ImportError as e:
            raise PixelDecodeError(
                "python-gdcm 未安装（边缘设备请安装 requirements-edge.txt）"
            ) from e

    def decode(self, dataset: Any) -> np.ndarray:
        try:
            import gdcm
        except ImportError as e:
            raise PixelDecodeError("python-gdcm 未安装") from e

        try:
            gdcm_image = gdcm.ImageReader()
            source = dataset.filename if getattr(dataset, "filename", None) else None
            if source is None:
                raise PixelDecodeError("gdcm 解码需要文件路径，当前数据集无 filename")
            gdcm_image.SetFileName(source)
            if not gdcm_image.Read():
                raise PixelDecodeError("gdcm 读取失败")
            pixel_data = gdcm_image.GetImage()
            size = pixel_data.GetBufferLength()
            buffer = pixel_data.GetBuffer()
            rows = int(dataset.Rows)
            columns = int(dataset.Columns)
            dtype = np.uint16 if buffer and size > rows * columns else np.uint8
            return np.frombuffer(buffer, dtype=dtype).reshape(rows, columns)
        except PixelDecodeError:
            raise
        except Exception as e:
            raise PixelDecodeError(f"gdcm 解码失败: {e}") from e


def default_pixel_decoder() -> PixelDecoder:
    """优先 gdcm，不可用则回退 pydicom。"""
    try:
        return GdcmPixelDecoder()
    except PixelDecodeError:
        logger.info("edge_dicom_decoder_fallback", decoder="pydicom")
        return PydicomPixelDecoder()


@dataclass
class DicomSCPStats:
    """SCP 运行统计。"""

    stores_received: int = 0
    stores_failed: int = 0
    frames_delivered: int = 0
    association_errors: int = 0


class DicomSCPService:
    """边缘端 DICOM SCP（C-STORE 接收 → 解码 → 帧交付）。"""

    def __init__(
        self,
        *,
        ae_title: str = DEFAULT_AE_TITLE,
        port: int = DEFAULT_PORT,
        frame_handler: Optional[Callable[[Frame], None]] = None,
        decoder: Optional[PixelDecoder] = None,
        latency_window: int = DEFAULT_LATENCY_WINDOW,
    ) -> None:
        self._ae_title = ae_title
        self._port = port
        self._frame_handler = frame_handler
        self._decoder = decoder or default_pixel_decoder()
        self._latencies_ms: Deque[float] = deque(maxlen=latency_window)
        self._frame_index = 0
        self._stats = DicomSCPStats()
        self._server = None
        self._started = False

    @property
    def ae_title(self) -> str:
        return self._ae_title

    @property
    def port(self) -> int:
        return self._port

    @property
    def stats(self) -> DicomSCPStats:
        return self._stats

    @property
    def started(self) -> bool:
        return self._started

    def set_frame_handler(self, handler: Optional[Callable[[Frame], None]]) -> None:
        """设置/替换帧回调。

        组装期存在循环依赖（回调需要服务实例，服务需要 SCP），因此提供
        构造后注入的入口，避免调用方直接改私有字段。
        """
        self._frame_handler = handler

    # ---- 生命周期 ----
    async def start(self) -> None:
        """启动 SCP（pynetdicom 懒加载；阻塞式启动放到线程中执行）。"""
        if self._started:
            return
        try:
            from pynetdicom import AE
        except ImportError as e:
            raise RuntimeError(
                "pynetdicom 未安装（边缘设备请安装 requirements-edge.txt）"
            ) from e

        ae = AE(ae_title=self._ae_title)
        ae.add_supported_context("UltrasoundMultiFrameStorage")
        ae.add_supported_context("SecondaryCaptureImageStorage")

        loop = asyncio.get_running_loop()

        def _run_server() -> None:
            self._server = ae.start_server(
                ("0.0.0.0", self._port),
                block=True,
                evt_handlers=[
                    ("evt.EVT_C_STORE", self._on_c_store_event),
                    ("evt.EVT_C_ECHO", self._on_c_echo_event),
                ],
            )

        # 起在独立线程里，避免阻塞事件循环
        await loop.run_in_executor(None, _run_server)
        self._started = True
        logger.info("edge_dicom_scp_started", ae_title=self._ae_title, port=self._port)

    async def stop(self) -> None:
        if not self._started or self._server is None:
            return
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._server.shutdown)
        self._server = None
        self._started = False
        logger.info("edge_dicom_scp_stopped", port=self._port)

    # ---- 事件处理 ----
    def _on_c_echo_event(self, event: Any) -> int:
        return STATUS_SUCCESS

    def _on_c_store_event(self, event: Any) -> int:
        return self.handle_store(event.dataset)

    def handle_store(self, dataset: Any) -> int:
        """处理一个 C-STORE 数据集并交付帧（同步，便于直接单测）。

        Returns:
            DICOM 状态码（0x0000 成功）。
        """
        started = time.monotonic()
        self._stats.stores_received += 1

        try:
            pixels = self._decoder.decode(dataset)
        except Exception as e:
            self._stats.stores_failed += 1
            logger.error("edge_dicom_decode_failed", error=str(e))
            return STATUS_PROCESSING_FAILURE

        session_id = self._extract_session_id(dataset)
        self._frame_index += 1

        frame = Frame(
            frame_id=self._extract_frame_id(dataset, self._frame_index),
            session_id=session_id,
            index=self._frame_index,
            pixels=pixels,
            received_at_ms=time.monotonic() * 1000.0,
            acquisition_datetime=self._extract_acquisition_datetime(dataset),
        )

        if self._frame_handler is not None:
            try:
                self._frame_handler(frame)
            except Exception as e:
                self._stats.stores_failed += 1
                logger.error("edge_dicom_frame_handler_failed", error=str(e))
                return STATUS_PROCESSING_FAILURE

        self._stats.frames_delivered += 1
        self._latencies_ms.append((time.monotonic() - started) * 1000.0)
        return STATUS_SUCCESS

    # ---- 字段抽取 ----
    @staticmethod
    def _extract_session_id(dataset: Any) -> str:
        for attr in ("AccessionNumber", "StudyID"):
            value = getattr(dataset, attr, None)
            if value:
                return str(value)
        return str(getattr(dataset, "StudyInstanceUID", "unknown"))

    @staticmethod
    def _extract_frame_id(dataset: Any, fallback_index: int) -> str:
        for attr in ("SOPInstanceUID", "InstanceNumber"):
            value = getattr(dataset, attr, None)
            if value:
                return str(value)
        return f"frame_{fallback_index}"

    @staticmethod
    def _extract_acquisition_datetime(dataset: Any) -> Optional[str]:
        date = getattr(dataset, "AcquisitionDate", None) or getattr(dataset, "StudyDate", None)
        tm = getattr(dataset, "AcquisitionTime", None) or getattr(dataset, "StudyTime", None)
        if date and tm:
            return f"{date}{str(tm).split('.')[0]}"
        return None

    # ---- 观测 ----
    def latency_stats(self) -> Dict[str, Optional[float]]:
        if not self._latencies_ms:
            return {"p50_ms": None, "p99_ms": None, "samples": 0}
        ordered = sorted(self._latencies_ms)
        return {
            "p50_ms": round(self._percentile(ordered, 50.0), 3),
            "p99_ms": round(self._percentile(ordered, 99.0), 3),
            "samples": len(ordered),
        }

    @staticmethod
    def _percentile(ordered: list, pct: float) -> float:
        n = len(ordered)
        index = (pct / 100.0) * (n - 1)
        lower = int(index)
        upper = min(lower + 1, n - 1)
        if lower == upper:
            return ordered[lower]
        return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


__all__ = [
    "DEFAULT_AE_TITLE",
    "DEFAULT_PORT",
    "DicomSCPService",
    "DicomSCPStats",
    "GdcmPixelDecoder",
    "PixelDecodeError",
    "PixelDecoder",
    "PydicomPixelDecoder",
    "STATUS_PROCESSING_FAILURE",
    "STATUS_SUCCESS",
    "default_pixel_decoder",
]
