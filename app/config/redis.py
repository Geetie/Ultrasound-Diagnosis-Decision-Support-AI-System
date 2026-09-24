import asyncio
from typing import Any, NamedTuple

import redis.asyncio as redis
import redis.backoff
import redis.retry
import structlog

from app.config.settings import settings

logger = structlog.get_logger()


class RedisGetResult(NamedTuple):
    value: bytes | None
    error: bool = False

    def decode_value(self) -> str | None:
        if self.value is None:
            return None
        return self.value.decode('utf-8') if isinstance(self.value, bytes) else self.value


class RedisPool:
    _client: redis.Redis | None = None
    _init_lock: asyncio.Lock | None = None
    _lock_loop_id: int | None = None
    _connection_check_failures: int = 0

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
    async def _is_connected(cls, client: redis.Redis) -> bool:
        try:
            await client.ping()
            return True
        except Exception:
            return False

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
                    cls._connection_check_failures += 1
                    logger.warning("redis_connection_stale_triggering_reconnect", failures=cls._connection_check_failures)

    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            async with cls._get_lock():
                if cls._client is None:
                    connection_kwargs = dict(
                        max_connections=settings.redis_pool_max_connections,
                        socket_timeout=settings.redis_pool_timeout,
                        socket_connect_timeout=settings.redis_pool_timeout,
                        retry_on_timeout=True,
                        retry=redis.retry.Retry(
                            retries=3,
                            backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
                        ),
                    )
                    if settings.redis_ssl:
                        connection_kwargs['ssl'] = True
                        connection_kwargs['ssl_cert_reqs'] = settings.redis_ssl_cert_reqs

                    cls._client = redis.Redis.from_url(settings.redis_url, **connection_kwargs)
                    cls._connection_check_failures = 0
                    logger.info("redis_client_created")
        else:
            await cls._ensure_connected()
        return cls._client

    @classmethod
    async def close(cls) -> None:
        async with cls._get_lock():
            if cls._client is not None:
                try:
                    await cls._client.aclose()
                except Exception as e:
                    logger.warning("redis_pool_close_error", error=str(e))
                cls._client = None

    @classmethod
    async def health_check(cls) -> bool:
        try:
            client = await cls.get_client()
            pong = await client.ping()
            return pong is True
        except Exception as e:
            logger.warning("redis_health_check_failed", error=str(e))
            async with cls._get_lock():
                if cls._client is not None:
                    try:
                        await cls._client.aclose()
                    except Exception:
                        pass
                    cls._client = None
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
                "total_connections_received": info.get("total_connections_received"),
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
    async def get(cls, key: str) -> bytes | None:
        try:
            client = await cls.get_client()
            return await client.get(key)
        except redis.RedisError as e:
            logger.warning("redis_get_failed", key=key, error=str(e))
            return None
        except Exception as e:
            logger.error("redis_unexpected_error", operation="get", key=key, error=str(e))
            return None

    @classmethod
    async def get_with_error(cls, key: str) -> RedisGetResult:
        try:
            client = await cls.get_client()
            value = await client.get(key)
            return RedisGetResult(value=value, error=False)
        except redis.RedisError as e:
            logger.warning("redis_get_failed", key=key, error=str(e))
            return RedisGetResult(value=None, error=True)
        except Exception as e:
            logger.error("redis_unexpected_error", operation="get", key=key, error=str(e))
            return RedisGetResult(value=None, error=True)

    @classmethod
    async def setex(cls, key: str, ttl: int, value: Any) -> bool:
        try:
            client = await cls.get_client()
            await client.setex(key, ttl, value)
            return True
        except redis.RedisError as e:
            logger.warning("redis_setex_failed", key=key, ttl=ttl, error=str(e))
            return False
        except Exception as e:
            logger.error("redis_unexpected_error", operation="setex", key=key, error=str(e))
            return False
