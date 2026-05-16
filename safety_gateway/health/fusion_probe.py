import asyncio
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import structlog

from safety_gateway.sg_config import sg_settings as settings

logger = structlog.get_logger()


class FusionProbe:
    """融合层可用性探测+TTL缓存组件

    独立于HealthChecker，封装融合层健康检查和缓存逻辑。
    httpx.AsyncClient在首次check()时懒初始化，整个生命周期复用同一实例，
    仅在aclose()时关闭。check()中不再每次判断is_closed，避免高频调用时
    反复创建/销毁client实例导致的连接泄漏和延迟抖动。
    """

    def __init__(self) -> None:
        self._available: bool | None = None
        self._last_check_time: float = 0.0
        self._check_lock = asyncio.Lock()
        self._client: httpx.AsyncClient | None = None
        self._closed: bool = False

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=settings.fusion_layer_health_check_timeout)
        return self._client

    async def check(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "unhealthy", "error": "probe_closed"}
        try:
            fusion_url = f"http://{settings.fusion_layer_host}:{settings.fusion_layer_port}/health"
            client = self._ensure_client()
            start = datetime.now(timezone.utc)
            response = await client.get(fusion_url)
            elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000

            if response.status_code == 200:
                self._available = True
                self._last_check_time = time.monotonic()
                return {
                    "status": "healthy",
                    "latency_ms": round(elapsed, 2),
                }
            logger.warning("sg_fusion_health_check_non_200", status_code=response.status_code)
            self._available = False
            self._last_check_time = time.monotonic()
            return {"status": "unhealthy", "status_code": response.status_code}
        except httpx.TimeoutException:
            logger.warning("sg_fusion_health_check_timeout")
            self._available = False
            self._last_check_time = time.monotonic()
            return {"status": "unhealthy", "error": "timeout"}
        except httpx.ConnectError:
            logger.warning("sg_fusion_health_check_connection_refused")
            self._available = False
            self._last_check_time = time.monotonic()
            return {"status": "unhealthy", "error": "connection_refused"}
        except Exception as e:
            logger.warning("sg_fusion_health_check_failed", error=str(e))
            self._available = False
            self._last_check_time = time.monotonic()
            return {"status": "unhealthy", "error": str(e)}

    async def get_availability(self) -> bool | None:
        async with self._check_lock:
            now = time.monotonic()
            cache_ttl = settings.fusion_layer_health_check_cache_ttl
            if (
                self._available is not None
                and (now - self._last_check_time) < cache_ttl
            ):
                return self._available
            fusion_status = await self.check()
            status = fusion_status.get("status")
            if status == "healthy":
                return True
            elif status == "unhealthy":
                return False
            return None

    @property
    def available(self) -> bool | None:
        return self._available

    async def aclose(self) -> None:
        self._closed = True
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None
