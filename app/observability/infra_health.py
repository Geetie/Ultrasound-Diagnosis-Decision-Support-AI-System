import asyncio
import structlog
from typing import Optional

from app.observability.metrics_collector import get_metrics_collector

_logger = structlog.get_logger("app.observability.infra_health")


async def check_redis_health(redis_url: str = "redis://localhost:6379") -> bool:
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, socket_timeout=5, socket_connect_timeout=5)
        alive = await client.ping()
        await client.aclose()
        collector = get_metrics_collector()
        collector.set_redis_process_alive(alive)
        if not alive:
            _logger.warning("redis_health_check_failed", redis_url=redis_url)
        return alive
    except Exception as e:
        _logger.error("redis_health_check_error", error=str(e), redis_url=redis_url)
        try:
            collector = get_metrics_collector()
            collector.set_redis_process_alive(False)
        except Exception:
            pass
        return False


async def check_pg_health(
    dsn: str = "postgresql://localhost:5432/ultrasound",
    slow_query_threshold_seconds: float = 300.0,
) -> dict:
    result = {"pool_utilization": 0.0, "slow_queries_detected": False}
    try:
        import asyncpg

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, timeout=5)
        async with pool.acquire() as conn:
            pool_stats = pool.get_stats() if hasattr(pool, "get_stats") else {}
            idle_size = pool.get_idle_size() if hasattr(pool, "get_idle_size") else 0
            max_size = pool.get_max_size() if hasattr(pool, "get_max_size") else 10
            utilization = 1.0 - (idle_size / max(max_size, 1))
            result["pool_utilization"] = utilization

            slow_count = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state = 'active' AND now() - query_start > $1::interval",
                f"{slow_query_threshold_seconds} seconds",
            )
            if slow_count and slow_count >= 3:
                result["slow_queries_detected"] = True

        await pool.close()

        collector = get_metrics_collector()
        collector.set_pg_connection_pool_utilization(utilization)
        if result["slow_queries_detected"]:
            collector.inc_pg_slow_queries()
            _logger.warning(
                "pg_slow_queries_detected",
                slow_count=slow_count,
                threshold=3,
            )
    except Exception as e:
        _logger.error("pg_health_check_error", error=str(e))
        try:
            collector = get_metrics_collector()
            collector.set_pg_connection_pool_utilization(0.0)
        except Exception:
            pass
    return result


async def run_infra_health_checks(
    redis_url: str = "redis://localhost:6379",
    pg_dsn: str = "postgresql://localhost:5432/ultrasound",
    interval_seconds: float = 30.0,
) -> None:
    while True:
        await check_redis_health(redis_url)
        await check_pg_health(pg_dsn)
        await asyncio.sleep(interval_seconds)
