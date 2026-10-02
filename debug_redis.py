import redis.asyncio as redis
import redis.backoff
import redis.retry

sg_redis_url = "redis://:sg_redis_dev_2024@redis:6379/1"
connection_kwargs = dict(
    max_connections=15,
    socket_timeout=5,
    socket_connect_timeout=5,
    retry_on_timeout=True,
    retry=redis.retry.Retry(
        retries=3,
        backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
    ),
)
client = redis.Redis.from_url(sg_redis_url, **connection_kwargs)
print(f"Client class: {client.__class__.__module__}.{client.__class__.__name__}")
print(f"Has aclose: {hasattr(client, 'aclose')}")
