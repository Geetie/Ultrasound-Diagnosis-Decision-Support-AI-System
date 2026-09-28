#!/usr/bin/env bash
# 对照实验：为什么"裸连接成功"而 RedisPool 连不上（Timeout connecting）。
# 变量只有一个：RedisPool 额外传的 connection kwargs（retry / retry_on_timeout / 超时）。
set -u
ROOT=/mnt/ee16/home/student03/uai
SRC=$ROOT/src
PY=$ROOT/venv/bin/python
ENVF=$ROOT/.env
cd "$SRC" || exit 1
set -a; . "$ENVF"; set +a

echo "=== A. 容器与宿主监听 ==="
docker ps --format '{{.Names}} | {{.Status}} | {{.Ports}}' | grep -i redis || echo "(redis 容器不在运行!)"
ss -ltn 2>/dev/null | grep 16379 || echo "(宿主未监听 16379!)"

echo
echo "=== B. 容器内自检（确认 Redis 自身健康）==="
docker exec uai-redis sh -lc 'redis-cli -a "$REDIS_PASSWORD" ping' 2>&1 | tail -1

echo
echo "=== C. 对照实验 ==="
"$PY" - <<'PY'
import asyncio
import os
import time

import redis.asyncio as redis
import redis.backoff
import redis.retry
from app.config.settings import settings


async def try_connect(label: str, **kwargs) -> None:
    url = settings.redis_url
    client = redis.Redis.from_url(url, **kwargs)
    start = time.monotonic()
    try:
        raw = client.ping()
        result = await raw if hasattr(raw, "__await__") else raw
        print(f"[{label}] OK ping={result}  ({time.monotonic() - start:.2f}s)")
    except Exception as exc:
        print(f"[{label}] FAIL {type(exc).__name__}: {str(exc)[:90]}  ({time.monotonic() - start:.2f}s)")
    finally:
        try:
            await client.aclose()
        except Exception:
            pass


async def main() -> None:
    print("redis 包版本:", __import__("redis").__version__)
    await try_connect("裸连接")
    await try_connect(
        "带超时",
        socket_timeout=settings.redis_pool_timeout,
        socket_connect_timeout=settings.redis_pool_timeout,
    )
    await try_connect(
        "带retry",
        socket_timeout=settings.redis_pool_timeout,
        socket_connect_timeout=settings.redis_pool_timeout,
        retry_on_timeout=True,
        retry=redis.retry.Retry(
            retries=3,
            backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
        ),
    )
    await try_connect(
        "与RedisPool完全一致",
        max_connections=settings.redis_pool_max_connections,
        socket_timeout=settings.redis_pool_timeout,
        socket_connect_timeout=settings.redis_pool_timeout,
        retry_on_timeout=True,
        retry=redis.retry.Retry(
            retries=3,
            backoff=redis.backoff.ExponentialBackoff(base=0.1, cap=2.0),
        ),
    )


asyncio.run(main())
PY
echo
echo "=== D. 直接调用 RedisPool（应用真实路径）==="
"$PY" - <<'PY'
import asyncio

from app.config.redis import RedisPool


async def main() -> None:
    client = await RedisPool.get_client()
    print("[RedisPool] client =", type(client).__name__ if client is not None else "None")


asyncio.run(main())
PY
echo "AB_DONE"
