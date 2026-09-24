"""T-GA-EDG-03 边缘端 WebSocket 客户端（双通道 + 设备认证 + 重连）。

与后端 `/api/v1/realtime/{nav,dicom}` 对齐：边缘端作为客户端上行
导航结果与 DICOM 帧，后端转发给前端（前端已有 DualWebSocketManager）。

要点：
- 握手带上 ``X-Device-Id`` / ``X-Device-Token``（HKDF 派生，见 device_auth）；
- 有界发送队列：后端不可达时**丢弃最旧**消息而不是无限堆积
  （导航是实时流，堆积旧帧只会让画面滞后）；
- 断线指数退避重连，上限封顶。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import structlog

from edge.device_auth import EdgeDeviceAuth
from edge.image_quality import image_quality_to_dict
from edge.plane_assessment import plane_assessment_to_dict
from edge.protocols import Frame, NavResult

logger = structlog.get_logger(__name__)

NAV_CHANNEL_PATH = "/api/v1/realtime/nav"
DICOM_CHANNEL_PATH = "/api/v1/realtime/dicom"

DEFAULT_QUEUE_MAXLEN = 64
DEFAULT_RECONNECT_BASE_SECONDS = 0.5
DEFAULT_RECONNECT_MAX_SECONDS = 30.0
DEFAULT_MAX_RECONNECT_ATTEMPTS = 10


@dataclass
class EdgeWSStats:
    sent_nav: int = 0
    sent_frames: int = 0
    dropped: int = 0
    reconnects: int = 0
    connect_failures: int = 0


class EdgeWebSocketClient:
    """边缘端上行 WebSocket 客户端。"""

    def __init__(
        self,
        base_url: str,
        auth: EdgeDeviceAuth,
        *,
        channel_path: str = NAV_CHANNEL_PATH,
        queue_maxlen: int = DEFAULT_QUEUE_MAXLEN,
        reconnect_base_seconds: float = DEFAULT_RECONNECT_BASE_SECONDS,
        reconnect_max_seconds: float = DEFAULT_RECONNECT_MAX_SECONDS,
        max_reconnect_attempts: int = DEFAULT_MAX_RECONNECT_ATTEMPTS,
        connect_factory: Callable[[str, dict[str, str]], Awaitable[Any]] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._channel_path = channel_path
        self._queue: deque[str] = deque(maxlen=queue_maxlen)
        self._reconnect_base = reconnect_base_seconds
        self._reconnect_max = reconnect_max_seconds
        self._max_reconnect_attempts = max_reconnect_attempts
        self._connect_factory = connect_factory or self._default_connect
        self._connection: Any = None
        self._send_lock = asyncio.Lock()
        self._stats = EdgeWSStats()

    # ---- 连接 ----
    @property
    def url(self) -> str:
        return f"{self._base_url}{self._channel_path}"

    @property
    def stats(self) -> EdgeWSStats:
        return self._stats

    @property
    def connected(self) -> bool:
        if self._connection is None:
            return False
        closed = getattr(self._connection, "closed", False)
        return not closed

    @staticmethod
    async def _default_connect(url: str, headers: dict[str, str]) -> Any:
        import websockets

        return await websockets.connect(url, additional_headers=headers)

    async def connect(self) -> bool:
        """建立连接（失败按指数退避重试，返回是否成功）。"""
        if self.connected:
            return True

        delay = self._reconnect_base
        for attempt in range(1, self._max_reconnect_attempts + 1):
            try:
                self._connection = await self._connect_factory(
                    self.url, self._auth.build_headers()
                )
                logger.info(
                    "edge_ws_connected",
                    url=self.url,
                    device_id=self._auth.device_id,
                    attempt=attempt,
                )
                return True
            except Exception as e:
                self._stats.connect_failures += 1
                logger.warning(
                    "edge_ws_connect_failed",
                    url=self.url,
                    attempt=attempt,
                    error=str(e),
                    retry_in_seconds=round(delay, 2),
                )
                if attempt == self._max_reconnect_attempts:
                    return False
                await asyncio.sleep(delay)
                delay = min(delay * 2, self._reconnect_max)
        return False

    async def close(self) -> None:
        if self._connection is not None:
            with contextlib.suppress(Exception):
                await self._connection.close()
            self._connection = None
            logger.info("edge_ws_closed", url=self.url)

    # ---- 发送 ----
    async def _send(self, payload: dict[str, Any]) -> bool:
        """发送一条消息；未连接或发送失败则入队（队列满丢弃最旧）。"""
        message = json.dumps(payload, ensure_ascii=False, default=str)

        if not self.connected:
            self._enqueue(message)
            return False

        async with self._send_lock:
            try:
                await self._connection.send(message)
                # 队列里还有积压就顺带冲掉（保持时序）
                while self._queue:
                    await self._connection.send(self._queue.popleft())
                return True
            except Exception as e:
                self._stats.reconnects += 1
                logger.warning("edge_ws_send_failed", url=self.url, error=str(e))
                self._connection = None
                self._enqueue(message)
                return False

    def _enqueue(self, message: str) -> None:
        if self._queue.maxlen is not None and len(self._queue) >= self._queue.maxlen:
            self._queue.popleft()
            self._stats.dropped += 1
        self._queue.append(message)

    async def push_nav_result(self, result: NavResult) -> bool:
        """上行导航结果。

        M1/M2/M6 的新增字段只在**确有值**时写入载荷：既减小实时流体积，
        也让未升级的后端可以照旧解析（后端契约里这些字段均为可选）。
        """
        payload: dict[str, Any] = {
            "type": "nav_result",
            "frame_id": result.frame_id,
            "session_id": result.session_id,
            "latency_ms": result.infer_latency_ms,
            "backend": result.backend,
            "labels": [
                {
                    "label": label.label,
                    "x": label.x,
                    "y": label.y,
                    "width": label.width,
                    "height": label.height,
                    "confidence": label.confidence,
                    **(
                        {
                            "risk_flag": label.risk_flag,
                            "suggested_action": label.suggested_action,
                        }
                        if label.risk_flag or label.suggested_action
                        else {}
                    ),
                }
                for label in result.labels
            ],
            "timestamp_ms": int(time.time() * 1000),
        }
        if result.plane_assessment is not None:
            payload["plane_assessment"] = plane_assessment_to_dict(
                result.plane_assessment
            )
        if result.image_quality is not None:
            payload["image_quality"] = image_quality_to_dict(result.image_quality)

        ok = await self._send(payload)
        if ok:
            self._stats.sent_nav += 1
        return ok

    async def push_frame(self, frame: Frame, payload: dict[str, Any]) -> bool:
        ok = await self._send(
            {
                "type": "dicom_frame",
                "frame_id": frame.frame_id,
                "session_id": frame.session_id,
                "index": frame.index,
                "acquisition_datetime": frame.acquisition_datetime,
                "payload": payload,
                "timestamp_ms": int(time.time() * 1000),
            }
        )
        if ok:
            self._stats.sent_frames += 1
        return ok


__all__ = [
    "DEFAULT_QUEUE_MAXLEN",
    "DEFAULT_RECONNECT_BASE_SECONDS",
    "DEFAULT_RECONNECT_MAX_SECONDS",
    "DICOM_CHANNEL_PATH",
    "NAV_CHANNEL_PATH",
    "EdgeWSStats",
    "EdgeWebSocketClient",
]
