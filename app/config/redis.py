import asyncio
import inspect
from typing import Any, NamedTuple

import redis.asyncio as aioredis
import structlog
from redis.asyncio.retry import Retry
from redis.backoff import ExponentialBackoff
from redis.exceptions import RedisError

# ----------------------------------------------------------------------
# ⚠️ 不要把这些导入改写成 `import redis.asyncio as redis` + `import redis.backoff`
# ----------------------------------------------------------------------
# 2026-09-28 真机部署暴露的**单点根因**：原实现是
#
#     import redis.asyncio as redis      # 别名：redis -> redis.asyncio
#     import redis.backoff               # ← 把顶层包 redis 绑定到本模块的 redis
#     import redis.retry                 # ← 同上
#
# Python 的 `import a.b` 会把**顶层包 a** 绑定到当前命名空间，于是 `redis` 这个名字
# 被重新指向**同步**包，`redis.Redis.from_url(...)` 造出的是 `redis.Redis`（同步），
# 而不是 `redis.asyncio.Redis`。后果是**整个 Redis 层在异步代码里全部不可用**，
# 且症状分散在四个子系统上，极难反查：
#
#   * `await client.ping()`      -> ping 同步返回 bool  -> TypeError: object bool ...
#   * `await client.xreadgroup()` -> 返回 list           -> TypeError: object list ...
#   * `async for k in scan_iter()`-> 返回 generator      -> TypeError: got generator
#   * `await client.get(k)`       -> 返回 None           -> TypeError: object NoneType ...
#
# 真机表现：健康检查恒 503、同意撤回消费者刷屏报错、HSM 持久化任务失败、WS 订阅起不来。
# 现在一律使用**显式别名**（`aioredis`）并只导入需要的符号，避免同名遮蔽回潮；
# `_build_client()` 另有类型断言做兜底。
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
    _client: aioredis.Redis | None = None
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
    async def _ping_once(cls, client: aioredis.Redis) -> bool:
        """执行一次 PING 并归一为 bool。

        `redis.asyncio.Redis.ping` 在 redis-py 5.0.1 上解析到的是
        `ManagementCommands.ping`（同步实现）——**异步客户端调用它会返回协程**，
        同步客户端调用它才返回 bool。这里两种都接受：`_build_client` 已确保客户端
        是异步的，容错只为测试替身（`AsyncMock` / `Mock`）不至于把探活打成失败。
        """
        result = client.ping()
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    @classmethod
    async def _is_connected(cls, client: aioredis.Redis) -> bool:
        try:
            return await cls._ping_once(client)
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
    def _build_client(cls) -> aioredis.Redis:
        """构造**异步** Redis 客户端。

        末尾的类型断言是"防回潮"闸门：一旦有人再把 `redis` 这个名字遮蔽成同步包
        （见文件头 ⚠️ 段），这里会在建连那一刻**大声失败**，而不是让缓存/队列/订阅/
        HSM 持久化四个子系统各自静默降级成"Redis 不可用"。
        """
        connection_kwargs: dict[str, Any] = dict(
            max_connections=settings.redis_pool_max_connections,
            # `socket_timeout` 必须**严格大于**任何阻塞读的 block，否则阻塞读会与
            # socket 读超时在同一秒打平 → 时而抛 TimeoutError。本项目最长的两个阻塞读
            # 都是 5s（`BLPOP int_worker_blpop_timeout_seconds=5`、
            # `XREADGROUP consent_withdrawal_poll_timeout_ms=5000`），真机因此出现
            # 间歇性的 `diagnosis_dequeue_redis_failed` / `consent_withdrawal_consume_error
            # error=Timeout reading from socket` 告警与无谓重试。取 +3s 余量。
            socket_timeout=max(settings.redis_pool_timeout, 8),
            socket_connect_timeout=settings.redis_pool_timeout,
            retry_on_timeout=True,
            retry=Retry(
                retries=3,
                backoff=ExponentialBackoff(base=0.1, cap=2.0),
            ),
        )
        if settings.redis_ssl:
            connection_kwargs['ssl'] = True
            connection_kwargs['ssl_cert_reqs'] = settings.redis_ssl_cert_reqs

        client = aioredis.Redis.from_url(settings.redis_url, **connection_kwargs)
        if not isinstance(client, aioredis.Redis):  # pragma: no cover - 防遮蔽回潮
            raise TypeError(
                "RedisPool 必须使用 redis.asyncio.Redis 构造异步客户端，"
                f"实际拿到 {type(client).__module__}.{type(client).__name__}"
                "（多半是 `import redis.backoff` 之类的导入把 `redis` 名字遮蔽成同步包了）"
            )
        cls._connection_check_failures = 0
        logger.info("redis_client_created")
        return client

    @classmethod
    async def get_client(cls) -> aioredis.Redis:
        if cls._client is None:
            async with cls._get_lock():
                if cls._client is None:
                    cls._client = cls._build_client()
            return cls._client

        # 已有客户端：先探活。探活判定失效会**清空** `_client`，因此这里必须
        # 立刻重建再返回 —— 原实现在这种情况下直接 `return cls._client`（None），
        # 调用方随即炸在 `'NoneType' object has no attribute 'xreadgroup'` 上
        # （真机日志里同意撤回消费者就是这么刷屏的）。
        await cls._ensure_connected()
        if cls._client is None:
            async with cls._get_lock():
                if cls._client is None:
                    cls._client = cls._build_client()
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
        except RedisError as e:
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
        except RedisError as e:
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
        except RedisError as e:
            logger.warning("redis_setex_failed", key=key, ttl=ttl, error=str(e))
            return False
        except Exception as e:
            logger.error("redis_unexpected_error", operation="setex", key=key, error=str(e))
            return False

    @classmethod
    async def set_nx(cls, key: str, ttl: int, value: Any) -> bool:
        """原子 SET NX EX：仅当 key 不存在时写入并返回 True。

        幂等键防并发竞态用（A.50.7 E12 探针实测：check-then-set 双请求
        竞态窗口下产生两个 request_id）——占位必须是原子的。
        """
        try:
            client = await cls.get_client()
            result = await client.set(key, value, nx=True, ex=ttl)
            return bool(result)
        except RedisError as e:
            logger.warning("redis_set_nx_failed", key=key, ttl=ttl, error=str(e))
            return False
        except Exception as e:
            logger.error("redis_unexpected_error", operation="set_nx", key=key, error=str(e))
            return False
