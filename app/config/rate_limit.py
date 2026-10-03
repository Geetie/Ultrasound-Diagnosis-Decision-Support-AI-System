import asyncio
import fnmatch
import hashlib
import time
from collections import OrderedDict

import structlog
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from app.api.v1.errors import RateLimitExceededException
from app.config.settings import settings

logger = structlog.get_logger()

BUCKET_MAX_AGE_SECONDS = 3600
BUCKET_CLEANUP_INTERVAL_SECONDS = 300
MAX_BUCKETS = 100000


class TokenBucket:
    def __init__(self, capacity: int, refill_rate: float):
        self.capacity = capacity
        self.refill_rate = refill_rate
        self.tokens: float = capacity
        self.last_refill: float = time.monotonic()
        self.last_access: float = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now
        self.last_access = now

    def consume(self, tokens: int = 1) -> bool:
        self._refill()
        if self.tokens >= tokens:
            self.tokens -= tokens
            return True
        return False


RATE_LIMITS = {
    "/api/v1/diagnosis/request": {"capacity": 10, "refill_rate": 10 / 60, "endpoint_managed": True},
    # 并发审查 P0-2：多器官联合诊断原先**完全无限流**。它比单器官重得多——
    # 请求协程内串行跑满 3 个器官（每器官完整管线，最长 90s），且全程持有一个
    # DB session；30 个并发即可耗尽连接池（pool_size 20 + max_overflow 10），
    # 随后 pool_timeout=30s 让全部请求一起卡死——级联雪崩最短路径。
    # 容量对齐单器官（10/60），留出一次多器官会话的额度。
    "/api/v1/diagnosis/multi-organ": {"capacity": 10, "refill_rate": 10 / 60},
    "/api/v1/imaging/dicom/upload": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/imaging/dicom/stream": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/feedback": {"capacity": 20, "refill_rate": 20 / 60},
    # 并发审查 P1-12：登录端点的 IP 桶由 5/min 提到 30/min，改由"用户名桶"承担
    # 撞库防护（见 enforce_login_rate_limit）。原因：桶键是
    # f"{client_ip}:{user_id}:{path}"，而登录请求没有 token → user_id 落回
    # client_ip，于是**同一医院出口 NAT 下的所有医生共享一个 5/min 桶**（实测：
    # 压测准备 10 个账号时被逼到退避重试，第 6 个账号起 429）。IP 桶只保留
    # "单主机洪水"这一层粗粒度保护。
    "/api/v1/auth/token": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/auth/login": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/consent": {"capacity": 10, "refill_rate": 10 / 60},
    # 并发审查 P2-1：报告生成原先不在限流表里。它走 to_thread 不阻塞事件循环，
    # 但每次生成都要读模板 + 渲染 + 落库，是典型的"可被单客户端刷"的资源入口。
    # 并发审查 P2-1：报告链路原先完全不在限流表里。三个入口都要登记：草稿生成
    # （渲染 + 落库）、HSM 签名（CPU 签名 + 审计）、PACS/HIS 推送（外部 IO）。
    # 带路径参数的用 `/*` 前缀匹配（见 resolve_rate_limit_config）。
    "/api/v1/report": {"capacity": 20, "refill_rate": 20 / 60},
    "/api/v1/report/*/sign": {"capacity": 10, "refill_rate": 10 / 60},
    "/api/v1/report/*/push": {"capacity": 10, "refill_rate": 10 / 60},
    "/fhir": {"capacity": 60, "refill_rate": 60 / 60},
}



def resolve_rate_limit_config(path: str) -> tuple[str, dict] | None:
    """把真实请求路径映射到 ``(桶路径, 配置)``；未登记则返回 ``None``。

    先精确匹配，再做**通配匹配**（配置键里含 ``*``，按 fnmatch 语义）——中间件拿到
    的是已展开的真实路径（``/api/v1/report/rpt_abc/sign``），带路径参数的端点无法用
    字面量登记。没有这一步，``RATE_LIMITS`` 里带 ``*`` 的键永远是死条目。

    多个通配同时命中时取**最具体**的（``/`` 段数多、模式串长的优先），
    避免粗粒度前缀把窄路径的额度吃掉。
    """
    config = RATE_LIMITS.get(path)
    if config is not None:
        return path, config
    matches = [
        (pattern, cfg)
        for pattern, cfg in RATE_LIMITS.items()
        if "*" in pattern and fnmatch.fnmatchcase(path, pattern)
    ]
    if not matches:
        return None
    matches.sort(key=lambda item: (item[0].count("/"), len(item[0])), reverse=True)
    return matches[0]

# 登录端点的用户名桶（与上面的 IP 桶正交；同一路径，靠 user_id 维度区分桶）
LOGIN_RATE_LIMIT_PATH = "/api/v1/auth/login"
LOGIN_USER_BUCKET_CAPACITY = 5


async def enforce_login_rate_limit(username: str) -> None:
    """按**用户名**维度限流（登录专用第二维），超限抛 ``RateLimitExceededException``。

    为什么必须有这一维：中间件的桶键是 ``f"{client_ip}:{user_id}:{path}"``，登录
    请求没有 token → ``_get_user_id_from_request`` 落回 client_ip，于是同一医院
    出口 IP（NAT/同一 WiFi）下**所有医生共享一个桶**，交接班或查房后集中登录时
    第 6 人起就吃 429（P1-12 的真机实测）。

    两维正交，缺一不可：
    - **IP 桶**（中间件，30/min）：挡"单主机洪水"；
    - **用户名桶**（本函数，5/min）：挡"针对单个账号的撞库/暴破"——即使攻击者
      换 IP 也一样被挡。

    隐私：用户名只以 sha256 前 16 位参与桶键，不落库、不进日志（限流日志里只会
    出现哈希），也不引入任何新的信任边界（不读 X-Forwarded-For）。
    """
    digest = hashlib.sha256(username.encode("utf-8")).hexdigest()[:16]
    exc = await check_rate_limit(
        LOGIN_RATE_LIMIT_PATH,
        "*",
        f"login:{digest}",
        is_emergency=False,
        capacity_override=LOGIN_USER_BUCKET_CAPACITY,
    )
    if exc is not None:
        logger.warning(
            "rate_limit_exceeded_login_user_bucket",
            username_hash=digest[:8],
            path=LOGIN_RATE_LIMIT_PATH,
        )
        raise exc

EMERGENCY_MULTIPLIER_COMPACT = 2
EMERGENCY_MULTIPLIER_FULL = 5

_buckets: OrderedDict[str, TokenBucket] = OrderedDict()
_STRIPE_COUNT = 16
_bucket_stripes: list[asyncio.Lock] = [asyncio.Lock() for _ in range(_STRIPE_COUNT)]
_last_cleanup: float = time.monotonic()


def _get_stripe_index(key: str) -> int:
    return hash(key) % _STRIPE_COUNT


async def _cleanup_expired_buckets() -> None:
    global _last_cleanup
    now = time.monotonic()
    if now - _last_cleanup < BUCKET_CLEANUP_INTERVAL_SECONDS:
        return
    expired_by_stripe: dict[int, list[str]] = {}
    for k, v in _buckets.items():
        if now - v.last_access > BUCKET_MAX_AGE_SECONDS:
            stripe_idx = _get_stripe_index(k)
            expired_by_stripe.setdefault(stripe_idx, []).append(k)
    total_removed = 0
    for stripe_idx, keys in expired_by_stripe.items():
        async with _bucket_stripes[stripe_idx]:
            for k in keys:
                if k in _buckets and now - _buckets[k].last_access > BUCKET_MAX_AGE_SECONDS:
                    _buckets.pop(k, None)
                    total_removed += 1
    if total_removed:
        logger.info("rate_limit_buckets_cleaned", removed=total_removed, remaining=len(_buckets))
    _last_cleanup = now


async def _get_bucket(key: str, path: str, capacity_override: int | None = None) -> TokenBucket | None:
    stripe_idx = _get_stripe_index(key)
    async with _bucket_stripes[stripe_idx]:
        if key in _buckets:
            _buckets.move_to_end(key)
            return _buckets[key]
        if len(_buckets) >= MAX_BUCKETS:
            _evict_oldest_bucket()
            if len(_buckets) >= MAX_BUCKETS:
                logger.warning("rate_limit_buckets_full", total=len(_buckets), max=MAX_BUCKETS)
                return None
        config = RATE_LIMITS.get(path)
        if not config:
            config = {"capacity": 60, "refill_rate": 1.0}
        capacity = capacity_override if capacity_override is not None else config["capacity"]
        refill_rate = config["refill_rate"] * (capacity / config["capacity"]) if capacity_override else config["refill_rate"]
        _buckets[key] = TokenBucket(
            capacity=capacity,
            refill_rate=refill_rate,
        )
        return _buckets[key]


def _evict_oldest_bucket() -> None:
    if not _buckets:
        return
    _buckets.popitem(last=False)


def _get_user_id_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
        return f"user:{hashlib.sha256(token.encode()).hexdigest()[:16]}"
    return "anonymous"


def _get_emergency_multiplier() -> int:
    if settings.deployment_profile == "compact":
        return EMERGENCY_MULTIPLIER_COMPACT
    return EMERGENCY_MULTIPLIER_FULL


async def check_rate_limit(
    path: str,
    client_ip: str,
    user_id: str,
    is_emergency: bool = False,
    skip: bool = False,
    dedup_key: str | None = None,
    capacity_override: int | None = None,
) -> RateLimitExceededException | None:
    if skip:
        return None

    if settings.rate_limit_redis_enabled:
        return await _check_rate_limit_redis(
            path, client_ip, user_id, is_emergency, dedup_key, capacity_override
        )

    return await _check_rate_limit_local(
        path, client_ip, user_id, is_emergency, dedup_key, capacity_override
    )


async def _check_rate_limit_local(
    path: str,
    client_ip: str,
    user_id: str,
    is_emergency: bool,
    dedup_key: str | None,
    capacity_override: int | None = None,
) -> RateLimitExceededException | None:
    await _cleanup_expired_buckets()

    bucket_key = f"{client_ip}:{user_id}:{path}"

    # emergency 乘数与显式覆盖同时存在时，取"更宽松的那个"：显式覆盖是调用方
    # 刻意指定的独立额度（如登录用户名桶 5/min），不应被 emergency 抬高成 25/min。
    effective_override = capacity_override
    if is_emergency:
        base_config = RATE_LIMITS[path]
        emergency_capacity = base_config["capacity"] * _get_emergency_multiplier()
        if effective_override is None or emergency_capacity > effective_override:
            effective_override = emergency_capacity

    bucket = await _get_bucket(bucket_key, path, effective_override)

    if bucket is None:
        logger.warning("rate_limit_bucket_unavailable", client=client_ip, user_id=user_id, path=path)
        return None

    if dedup_key:
        dedup_bucket_key = f"{bucket_key}:dedup:{dedup_key}"
        main_idx = _get_stripe_index(bucket_key)
        dedup_idx = _get_stripe_index(dedup_bucket_key)
        indices = sorted(set([main_idx, dedup_idx]))

        for idx in indices:
            await _bucket_stripes[idx].acquire()
        try:
            if dedup_bucket_key in _buckets:
                return None
            if not bucket.consume():
                logger.warning("rate_limit_exceeded", client=client_ip, user_id=user_id, path=path)
                return RateLimitExceededException()
            _buckets[dedup_bucket_key] = TokenBucket(capacity=1, refill_rate=1.0)
            _buckets[dedup_bucket_key].tokens = 0
        finally:
            for idx in reversed(indices):
                _bucket_stripes[idx].release()
        return None

    stripe_idx = _get_stripe_index(bucket_key)
    async with _bucket_stripes[stripe_idx]:
        consumed = bucket.consume()
    if not consumed:
        logger.warning("rate_limit_exceeded", client=client_ip, user_id=user_id, path=path)
        return RateLimitExceededException()

    return None


_REDIS_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])
local consume_tokens = tonumber(ARGV[4])
local ttl = tonumber(ARGV[5])

local data = redis.call('HMGET', key, 'tokens', 'last_refill')
local tokens = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil then
    tokens = capacity
    last_refill = now
end

local elapsed = now - last_refill
if elapsed > 0 then
    tokens = math.min(capacity, tokens + elapsed * refill_rate)
    last_refill = now
end

if tokens >= consume_tokens then
    tokens = tokens - consume_tokens
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, ttl)
    return 1
else
    redis.call('HMSET', key, 'tokens', tokens, 'last_refill', last_refill)
    redis.call('EXPIRE', key, ttl)
    return 0
end
"""


async def _check_rate_limit_redis(
    path: str,
    client_ip: str,
    user_id: str,
    is_emergency: bool,
    dedup_key: str | None,
    capacity_override: int | None = None,
) -> RateLimitExceededException | None:
    from app.config.redis import RedisPool

    config = RATE_LIMITS.get(path, {"capacity": 60, "refill_rate": 1.0})
    capacity = config["capacity"]
    refill_rate = config["refill_rate"]

    # 显式覆盖：容量按比例缩放速率（与 _get_bucket 的本地口径一致），
    # 保证 Redis/本地两条实现对同一 (path, override) 给出同样的桶。
    if capacity_override is not None:
        refill_rate = refill_rate * (capacity_override / capacity)
        capacity = capacity_override

    if is_emergency:
        multiplier = _get_emergency_multiplier()
        if capacity * multiplier > capacity:
            capacity = capacity * multiplier
            refill_rate = refill_rate * multiplier

    bucket_key = f"{settings.redis_key_prefix}:ratelimit:{client_ip}:{user_id}:{path}"

    if dedup_key:
        dedup_key_redis = f"{settings.redis_key_prefix}:ratelimit:dedup:{dedup_key}"
        try:
            client = await RedisPool.get_client()
            exists = await client.exists(dedup_key_redis)
            if exists:
                return None
        except Exception as e:
            logger.warning("rate_limit_redis_dedup_check_failed", error=str(e))
            return None

    try:
        client = await RedisPool.get_client()
        now = time.time()
        allowed = await client.eval(
            _REDIS_TOKEN_BUCKET_LUA,
            1,
            bucket_key,
            capacity,
            refill_rate,
            now,
            1,
            BUCKET_MAX_AGE_SECONDS,
        )

        if dedup_key and allowed:
            try:
                await client.setex(dedup_key_redis, BUCKET_MAX_AGE_SECONDS, "1")
            except Exception as e:
                logger.warning("rate_limit_redis_dedup_set_failed", error=str(e))

        if not allowed:
            logger.warning("rate_limit_exceeded_redis", client=client_ip, user_id=user_id, path=path)
            return RateLimitExceededException()

        return None
    except Exception as e:
        logger.warning("rate_limit_redis_fallback_to_local", error=str(e))
        return await _check_rate_limit_local(
            path, client_ip, user_id, is_emergency, dedup_key, capacity_override
        )


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 并发审查 P2-1：带路径参数的端点（/report/{id}/sign）用 `/*` 前缀登记，
        # 因此这里不能直接 `path not in RATE_LIMITS`（那是字面量判断）。
        resolved = resolve_rate_limit_config(path)
        if resolved is None:
            return await call_next(request)

        limit_path, config = resolved
        if config.get("endpoint_managed"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        user_id = _get_user_id_from_request(request)

        x_priority = request.headers.get("X-Priority", "1")
        is_emergency = x_priority == "0"

        exc = await check_rate_limit(limit_path, client_ip, user_id, is_emergency)
        if exc:
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "error_code": exc.error_code,
                    "error_message": exc.error_message,
                    "suggestion": exc.suggestion,
                    "retryable": exc.retryable,
                    "retry_strategy": exc.retry_strategy,
                },
                headers={"Retry-After": "5"},
            )

        return await call_next(request)
