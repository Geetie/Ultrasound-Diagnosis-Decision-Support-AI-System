"""Safety Gateway Redis连接池模块

提供SafetyGateway专用的Redis异步连接池管理，使用独立数据库（db=1）
与主服务Redis隔离。

核心功能：
- 懒初始化Redis连接（首次请求时创建）
- 连接池配置（max_connections、socket_timeout）
- 健康检查（ping命令）
- 优雅关闭
- asyncio.Lock保护客户端重建，防止并发请求同时触发重建
"""

import asyncio
import redis.asyncio as aioredis
from redis.retry import Retry
from redis.backoff import ExponentialBackoff
from typing import Any, ClassVar

import structlog

from safety_gateway.sg_config import sg_settings as settings

logger = structlog.get_logger()


class SafetyGatewayRedisPool:
    """SafetyGateway独立Redis连接池管理器

    使用独立数据库db=1与主服务(db=0)隔离。
    类变量_client/_init_lock/_lock_loop_id在多进程部署场景下，
    每个进程有独立的Python解释器实例，类变量天然进程隔离。
    单进程内的asyncio并发安全由_init_lock（asyncio.Lock）保障。
    """
    _client: ClassVar["aioredis.Redis | None"] = None
    _init_lock: asyncio.Lock | None = None
    _lock_loop_id: int | None = None
    _consecutive_failures: ClassVar[int] = 0  # asyncio单线程安全：仅在async方法中读写，无需额外同步

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        try:
            current_loop = asyncio.get_running_loop()
            current_loop_id = id(current_loop)
        except RuntimeError:
            current_loop_id = None

        if cls._init_lock is None or cls._lock_loop_id != current_loop_id:
            cls._init_lock = asyncio.Lock()
            cls._lock_loop_id = current_loop_id
        return cls._init_lock

    @classmethod
    async def _is_connected(cls, client: aioredis.Redis) -> bool:
        try:
            await client.ping()
            return True
        except Exception:
            return False

    @classmethod
    async def detailed_health_check(cls) -> dict:
        try:
            client = await cls.get_client()
            pong = await client.ping()
            if not pong:
                return {"status": "unhealthy", "error": "ping failed"}

            info = await client.info()
            return {
                "status": "healthy",
                "used_memory_human": info.get("used_memory_human"),
                "used_memory_peak_human": info.get("used_memory_peak_human"),
                "maxmemory": info.get("maxmemory"),
                "maxmemory_policy": info.get("maxmemory_policy"),
                "connected_clients": info.get("connected_clients"),
                "keyspace_hits": info.get("keyspace_hits"),
                "keyspace_misses": info.get("keyspace_misses"),
                "hit_rate": round(
                    info.get("keyspace_hits", 0) / max(info.get("keyspace_hits", 0) + info.get("keyspace_misses", 0), 1) * 100, 2
                ),
                "uptime_in_seconds": info.get("uptime_in_seconds"),
            }
        except Exception as e:
            return {"status": "unhealthy", "error": str(e)}

    @classmethod
    async def _ensure_connected(cls) -> None:
        if cls._client is None:
            return
        if not await cls._is_connected(cls._client):
            async with cls._get_lock():
                if cls._client is not None and not await cls._is_connected(cls._client):
                    try:
                        await cls._client.aclose()
                    except Exception:
                        pass
                    cls._client = None
                    logger.warning("sg_redis_connection_stale_triggering_reconnect")

    @classmethod
    async def get_client(cls) -> aioredis.Redis:
        if cls._client is None:
            async with cls._get_lock():
                if cls._client is None:
                    from urllib.parse import urlparse, urlunparse
                    parsed = urlparse(settings.redis_url)
                    path_parts = parsed.path.rsplit("/", 1)
                    new_path = "/1" if len(path_parts) <= 1 else f"{path_parts[0]}/1"
                    sg_redis_url = urlunparse(parsed._replace(path=new_path))

                    connection_kwargs = dict(
                        max_connections=settings.redis_pool_max_connections,
                        socket_timeout=settings.redis_pool_timeout,
                        socket_connect_timeout=settings.redis_pool_timeout,
                        retry_on_timeout=True,
                        retry=Retry(
                            retries=3,
                            backoff=ExponentialBackoff(base=0.1, cap=2.0),
                        ),
                    )
                    if settings.redis_ssl:
                        connection_kwargs['ssl'] = True

                    cls._client = aioredis.Redis.from_url(sg_redis_url, **connection_kwargs)
                    cls._consecutive_failures = 0
                    logger.info("sg_redis_pool_initialized", url=sg_redis_url)
        else:
            await cls._ensure_connected()
            if cls._client is None:
                return await cls.get_client()
        return cls._client

    @classmethod
    async def close(cls) -> None:
        async with cls._get_lock():
            if cls._client is not None:
                try:
                    loop = asyncio.get_running_loop()
                    if cls._lock_loop_id is not None and loop is not None and id(loop) != cls._lock_loop_id:
                        logger.warning(
                            "sg_redis_pool_close_different_event_loop",
                            current_loop_id=id(loop),
                            client_loop_id=cls._lock_loop_id,
                        )
                    await cls._client.aclose()
                except Exception as e:
                    logger.warning("sg_redis_pool_close_error", error=str(e))
                cls._client = None
                logger.info("sg_redis_pool_closed")

    @classmethod
    async def health_check(cls) -> bool:
        try:
            client = await cls.get_client()
            pong = await client.ping()
            cls._consecutive_failures = 0
            return pong is True
        except Exception as e:
            cls._consecutive_failures += 1
            if cls._consecutive_failures >= 3:
                logger.warning(
                    "sg_redis_health_check_resetting_client",
                    consecutive_failures=cls._consecutive_failures,
                    error=str(e),
                )
                async with cls._get_lock():
                    if cls._client is not None:
                        try:
                            await cls._client.aclose()
                        except Exception:
                            pass
                        cls._client = None
                cls._consecutive_failures = 0
            else:
                logger.info(
                    "sg_redis_health_check_transient_failure",
                    consecutive_failures=cls._consecutive_failures,
                    error=str(e),
                )
            return False

    @classmethod
    async def get(cls, key: str) -> bytes | None:
        try:
            client = await cls.get_client()
            return await client.get(key)
        except aioredis.RedisError as e:
            logger.warning("sg_redis_get_failed", key=key, error=str(e))
            return None
        except Exception as e:
            logger.error("sg_redis_unexpected_error", operation="get", key=key, error=str(e))
            return None

    @classmethod
    async def setex(cls, key: str, ttl: int, value: Any) -> bool:
        try:
            client = await cls.get_client()
            await client.setex(key, ttl, value)
            return True
        except aioredis.RedisError as e:
            logger.warning("sg_redis_setex_failed", key=key, ttl=ttl, error=str(e))
            return False
        except Exception as e:
            logger.error("sg_redis_unexpected_error", operation="setex", key=key, error=str(e))
            return False
