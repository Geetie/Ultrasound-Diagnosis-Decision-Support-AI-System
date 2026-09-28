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
        self._loop: asyncio.AbstractEventLoop | None = None
        self._check_lock: asyncio.Lock | None = None
        self._client: httpx.AsyncClient | None = None
        self._closed: bool = False

    def _ensure_loop_bound(self) -> None:
        """让 asyncio 原语与 HTTP 连接池跟随当前事件循环。

        FusionProbe 由进程级单例 HealthChecker 持有，而 asyncio.Lock 与
        httpx.AsyncClient 的连接池都绑定到"创建它的那个事件循环"。一旦跨
        事件循环复用（测试每用例一个新循环、uvicorn reload 等），等待旧循环
        的 socket 永远不会有结果，事件循环也不注册定时器 —— 表现为
        perform_full_check 永久挂死（曾导致单测整轮卡住）。
        检测到循环变化即重建原语；旧 client 已不可用，直接丢弃。
        """
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._check_lock is not None:
            return
        if self._loop is not None:
            logger.debug("sg_fusion_probe_rebound_to_new_loop")
        self._loop = loop
        self._check_lock = asyncio.Lock()
        self._client = None

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=settings.fusion_layer_health_check_timeout)
        return self._client

    async def check(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "unhealthy", "error": "probe_closed"}
        self._ensure_loop_bound()
        try:
            # 路径必须是主服务**真实**的健康路由。
            # 2026-09-28 真机部署暴露：这里原写 `/health`，而主服务的健康接口挂在
            # `/api/v1/health`（见 app/main.py 的 `health_router` include 前缀），
            # 于是探测恒 404 → SG 总判自己 `degraded` → 主服务又把 SG 的 `degraded`
            # 视为 unhealthy（`safety_gateway_client.health_check` 要求 `healthy`）
            # → 双方各报各的：SG 每 4s 刷 fusion 404，主服务每 4s 刷
            # `safety_gateway_consecutive_unhealthy_critical`，形成永久告警环。
            fusion_url = (
                f"http://{settings.fusion_layer_host}:{settings.fusion_layer_port}"
                "/api/v1/health"
            )
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
        self._ensure_loop_bound()
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
