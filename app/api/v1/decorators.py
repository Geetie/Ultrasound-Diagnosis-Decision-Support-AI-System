import json
from functools import wraps

import structlog

from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey

logger = structlog.get_logger()


def idempotency_check(ttl_seconds: int, key_prefix: str = "diagnosis"):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, idempotency_key: str | None = None, **kwargs):
            if not idempotency_key:
                return await func(*args, idempotency_key=idempotency_key, **kwargs)

            cache_key = RedisKey.custom_idempotency(key_prefix, idempotency_key)
            result = await RedisPool.get_with_error(cache_key)
            if result.error:
                logger.warning("idempotency_check_redis_error", key=cache_key)
            elif result.value:
                raw = result.value.decode('utf-8') if isinstance(result.value, bytes) else result.value
                return json.loads(raw)

            response = await func(*args, idempotency_key=idempotency_key, **kwargs)

            try:
                response_data = response.model_dump() if hasattr(response, 'model_dump') else response
                await RedisPool.setex(cache_key, ttl_seconds, json.dumps(response_data))
            except Exception as e:
                logger.warning("idempotency_cache_set_failed", key=cache_key, error=str(e))

            return response
        return wrapper
    return decorator
