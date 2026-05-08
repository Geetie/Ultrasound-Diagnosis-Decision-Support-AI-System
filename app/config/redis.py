import redis.asyncio as redis
from typing import Optional
from app.config.settings import settings


class RedisPool:
    _client: Optional[redis.Redis] = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            cls._client = redis.Redis.from_url(settings.redis_url)
        return cls._client

    @classmethod
    async def close(cls) -> None:
        if cls._client is not None:
            await cls._client.aclose()
            cls._client = None

    @classmethod
    async def health_check(cls) -> bool:
        try:
            client = await cls.get_client()
            pong = await client.ping()
            return pong is True
        except Exception as e:
            cls._client = None
            return False