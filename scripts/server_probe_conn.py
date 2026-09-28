"""连通性探针：Redis / PG 从「应用同一 venv + 同一 settings」出发是否可达。

刻意**不打印任何含口令的 URL**（只打印 host:port 部分）。
"""
import asyncio
import os
import pathlib
import sys
import time

ENV_PATH = pathlib.Path(os.path.expanduser('~/uai/.env'))


def load_env() -> dict:
    env = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    os.environ.setdefault('USE_SOFT_HSM', 'true')
    return env


def hide(url: str) -> str:
    """只保留 scheme://host:port/db，抹掉 user:password@。"""
    if '@' in url:
        head, tail = url.split('@', 1)
        scheme = head.split('://', 1)[0]
        return f'{scheme}://<redacted>@{tail}'
    return url


async def main() -> None:
    env = load_env()
    sys.path.insert(0, os.path.expanduser('~/uai/src'))

    from app.config.settings import settings

    print('[settings] redis =', hide(settings.redis_url))
    print('[settings] db    =', hide(settings.database_url))
    print(
        '[settings] redis_pool_timeout =',
        settings.redis_pool_timeout,
        'max_connections =',
        settings.redis_pool_max_connections,
    )

    import redis.asyncio as aioredis

    t0 = time.monotonic()
    try:
        c = aioredis.Redis.from_url(
            settings.redis_url, socket_timeout=5, socket_connect_timeout=5
        )
        pong = await c.ping()
        print(f'[raw redis] ping={pong} in {time.monotonic() - t0:.3f}s')
        clients = await c.info('clients')
        print(
            '[raw redis] connected_clients =',
            clients.get('connected_clients'),
            'blocked =',
            clients.get('blocked_clients'),
        )
        print('[raw redis] maxclients =', (await c.config_get('maxclients')).get('maxclients'))
        print('[raw redis] timeout =', (await c.config_get('timeout')).get('timeout'))
        await c.aclose()
    except Exception as exc:
        print(f'[raw redis] FAILED {type(exc).__name__}: {exc} in {time.monotonic() - t0:.3f}s')

    from app.config.redis import RedisPool

    t0 = time.monotonic()
    try:
        ok = await RedisPool.health_check()
        print(f'[RedisPool.health_check] = {ok} in {time.monotonic() - t0:.3f}s')
    except Exception as exc:
        print(f'[RedisPool.health_check] RAISED {type(exc).__name__}: {exc}')

    import asyncpg

    dsn = env.get('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')
    t0 = time.monotonic()
    try:
        conn = await asyncpg.connect(dsn, timeout=5)
        print(f'[asyncpg] select 1 = {await conn.fetchval("select 1")} in {time.monotonic() - t0:.3f}s')
        await conn.close()
    except Exception as exc:
        print(f'[asyncpg] FAILED {type(exc).__name__}: {exc}')

    from sqlalchemy import text

    from app.config.database import async_engine

    t0 = time.monotonic()
    try:
        async with async_engine.connect() as conn:
            val = (await conn.execute(text('SELECT 1'))).scalar()
        print(f'[sqlalchemy engine] select 1 = {val} in {time.monotonic() - t0:.3f}s')
    except Exception as exc:
        print(f'[sqlalchemy engine] FAILED {type(exc).__name__}: {exc}')
    await async_engine.dispose()

    # 应用实际连的 asyncpg pool（rag 向量库用的那条）
    print('[dsn-ish] DATABASE_URL scheme =', env.get('DATABASE_URL', '?').split('://', 1)[0])
    print('[dsn-ish] rag 相关键 =', sorted(k for k in env if 'PG' in k.upper() or 'VECTOR' in k.upper()))


asyncio.run(main())
