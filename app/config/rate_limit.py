import asyncio
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
    "/api/v1/imaging/dicom/upload": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/imaging/dicom/stream": {"capacity": 30, "refill_rate": 30 / 60},
    "/api/v1/feedback": {"capacity": 20, "refill_rate": 20 / 60},
    "/api/v1/auth/token": {"capacity": 5, "refill_rate": 5 / 60},
    "/api/v1/auth/login": {"capacity": 5, "refill_rate": 5 / 60},
    "/api/v1/consent": {"capacity": 10, "refill_rate": 10 / 60},
    "/fhir": {"capacity": 60, "refill_rate": 60 / 60},
}

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
) -> RateLimitExceededException | None:
    if skip:
        return None

    if settings.rate_limit_redis_enabled:
        return await _check_rate_limit_redis(path, client_ip, user_id, is_emergency, dedup_key)

    return await _check_rate_limit_local(path, client_ip, user_id, is_emergency, dedup_key)


async def _check_rate_limit_local(
    path: str,
    client_ip: str,
    user_id: str,
    is_emergency: bool,
    dedup_key: str | None,
) -> RateLimitExceededException | None:
    await _cleanup_expired_buckets()

    bucket_key = f"{client_ip}:{user_id}:{path}"

    capacity_override = None
    if is_emergency:
        multiplier = _get_emergency_multiplier()
        base_config = RATE_LIMITS[path]
        capacity_override = base_config["capacity"] * multiplier

    bucket = await _get_bucket(bucket_key, path, capacity_override)

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
) -> RateLimitExceededException | None:
    from app.config.redis import RedisPool

    config = RATE_LIMITS.get(path, {"capacity": 60, "refill_rate": 1.0})
    capacity = config["capacity"]
    refill_rate = config["refill_rate"]

    if is_emergency:
        multiplier = _get_emergency_multiplier()
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
        return await _check_rate_limit_local(path, client_ip, user_id, is_emergency, dedup_key)


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, **kwargs):
        super().__init__(app, **kwargs)

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path not in RATE_LIMITS:
            return await call_next(request)

        if RATE_LIMITS[path].get("endpoint_managed"):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        user_id = _get_user_id_from_request(request)

        x_priority = request.headers.get("X-Priority", "1")
        is_emergency = x_priority == "0"

        exc = await check_rate_limit(path, client_ip, user_id, is_emergency)
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
