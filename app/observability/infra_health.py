import asyncio
import shutil
import time
from collections.abc import Sequence
from datetime import timedelta

import structlog

from app.config.dsn import redact_url
from app.observability.constants import DiskAlertThresholds, EdgeGPUConstants
from app.observability.metrics_collector import get_metrics_collector

_logger = structlog.get_logger("app.observability.infra_health")

# 巡检循环的停止信号（在运行中的事件循环内创建，避免导入期绑定循环）
_infra_shutdown_event: asyncio.Event | None = None

# 边缘 GPU 连续过热起始时间戳（None 表示当前未过热）
_edge_gpu_overheat_since: float | None = None


async def check_redis_health(redis_url: str | None = None) -> bool:
    # 默认值必须取自 settings：原实现写死 "redis://localhost:6379"，真机上探测的是
    # 错误地址（我们的 Redis 在 127.0.0.1:16379 + requirepass），健康检查因此
    # 永远 unhealthy 并刷错误日志（2026-09-28 现场部署暴露）。
    if redis_url is None:
        from app.config.settings import settings as _settings

        redis_url = _settings.redis_url
    try:
        import redis.asyncio as aioredis

        client = aioredis.from_url(redis_url, socket_timeout=5, socket_connect_timeout=5)
        alive = await client.ping()
        await client.aclose()
        collector = get_metrics_collector()
        collector.set_redis_process_alive(alive)
        if not alive:
            # 打 URL 前必须脱敏：redis_url 里带 requirepass 口令，
            # 原实现会把明文口令写进共享机上的日志文件。
            _logger.warning("redis_health_check_failed", redis_url=redact_url(redis_url))
        return alive
    except Exception as e:
        _logger.error(
            "redis_health_check_error", error=str(e), redis_url=redact_url(redis_url)
        )
        try:
            collector = get_metrics_collector()
            collector.set_redis_process_alive(False)
        except Exception:
            pass
        return False


async def check_pg_health(
    dsn: str | None = None,
    slow_query_threshold_seconds: float = 300.0,
) -> dict:
    # 默认值必须取自 settings 且**归一为 asyncpg 形式**：原实现写死
    # `postgresql://localhost:5432/ultrasound`，真机上巡检的是不存在的库；
    # 而 settings.database_url 带 `+asyncpg` 后缀，裸驱动又不认（见 app/config/dsn.py）。
    if dsn is None:
        from app.config.dsn import to_asyncpg_dsn
        from app.config.settings import settings as _settings

        dsn = to_asyncpg_dsn(_settings.database_url)
    else:
        from app.config.dsn import to_asyncpg_dsn

        dsn = to_asyncpg_dsn(dsn)

    result = {"pool_utilization": 0.0, "slow_queries_detected": False}
    pool = None
    try:
        import asyncpg

        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10, timeout=5)
        async with pool.acquire() as conn:
            idle_size = pool.get_idle_size() if hasattr(pool, "get_idle_size") else 0
            max_size = pool.get_max_size() if hasattr(pool, "get_max_size") else 10
            utilization = 1.0 - (idle_size / max(max_size, 1))
            result["pool_utilization"] = utilization

            slow_count = await conn.fetchval(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE state = 'active' AND now() - query_start > $1::interval",
                # asyncpg 的 `interval` 参数要的是 `datetime.timedelta`，**不是字符串**：
                # 原实现传 f"{n} seconds" 会抛
                #   TypeError: invalid input for query argument $1:
                #              '300.0 seconds' ('str' object has no attribute 'days')
                # 真机上这条报错一直被"巡检连不上库"的噪声掩盖着，直到库连通才显形。
                timedelta(seconds=slow_query_threshold_seconds),
            )
            if slow_count and slow_count >= 3:
                result["slow_queries_detected"] = True

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
    finally:
        # 连接池由本函数创建，必须由本函数释放（含 acquire 失败路径），
        # 否则每次探测异常都会泄漏一个连接池。
        if pool is not None:
            try:
                await pool.close()
            except Exception as e:
                _logger.warning("pg_pool_close_error", error=str(e))
    return result


def check_disk_usage(path: str) -> dict:
    """检查磁盘使用率并上报指标（规格：05-可观测性与运维.md 磁盘资源）。

    告警级别：>=95% critical、>=85% warning、其余 normal。
    """
    usage = shutil.disk_usage(path)
    ratio = (usage.used / usage.total) if usage.total else 0.0

    collector = get_metrics_collector()
    collector.set_disk_usage_ratio(path, ratio)

    if ratio >= DiskAlertThresholds.CRITICAL_RATIO:
        alert_level = "critical"
        _logger.error("disk_usage_critical", path=path, usage_ratio=round(ratio, 4))
    elif ratio >= DiskAlertThresholds.WARNING_RATIO:
        alert_level = "warning"
        _logger.warning("disk_usage_warning", path=path, usage_ratio=round(ratio, 4))
    else:
        alert_level = "normal"

    return {"path": path, "usage_ratio": ratio, "alert_level": alert_level}


def check_edge_gpu_temperature(celsius: float) -> dict:
    """检查边缘端 Jetson GPU 温度（规格：05-可观测性与运维.md GPU 温度）。

    分级：>=90°C 且持续 >=60s → critical（紧急降级，暂停所有 GPU 推理）；
    >=90°C 但未达持续时长 → warning；>=80°C → warning；其余 normal。
    温度回落到 critical 阈值以下即复位过热计时。
    """
    global _edge_gpu_overheat_since

    collector = get_metrics_collector()
    collector.set_edge_gpu_temperature(celsius)

    if celsius >= EdgeGPUConstants.CRITICAL_TEMP_CELSIUS:
        now = time.time()
        if _edge_gpu_overheat_since is None:
            _edge_gpu_overheat_since = now
        overheat_seconds = now - _edge_gpu_overheat_since

        if overheat_seconds >= EdgeGPUConstants.OVERHEAT_DURATION_SECONDS:
            collector.set_edge_gpu_overheat_duration(overheat_seconds)
            _logger.critical(
                "edge_gpu_overheat_critical",
                celsius=celsius,
                overheat_seconds=round(overheat_seconds, 1),
            )
            return {
                "celsius": celsius,
                "overheat_seconds": overheat_seconds,
                "alert_level": "critical",
            }

        collector.set_edge_gpu_overheat_duration(0.0)
        _logger.warning(
            "edge_gpu_overheat_warning",
            celsius=celsius,
            overheat_seconds=round(overheat_seconds, 1),
        )
        return {
            "celsius": celsius,
            "overheat_seconds": overheat_seconds,
            "alert_level": "warning",
        }

    _edge_gpu_overheat_since = None
    collector.set_edge_gpu_overheat_duration(0.0)

    if celsius >= EdgeGPUConstants.WARNING_TEMP_CELSIUS:
        _logger.warning("edge_gpu_temperature_warning", celsius=celsius)
        return {"celsius": celsius, "overheat_seconds": 0.0, "alert_level": "warning"}

    return {"celsius": celsius, "overheat_seconds": 0.0, "alert_level": "normal"}


async def stop_infra_health_checks() -> None:
    """通知巡检循环退出（lifespan 关闭阶段调用）。"""
    if _infra_shutdown_event is not None:
        _infra_shutdown_event.set()


async def check_dlq_depth(threshold: int = 1) -> int:
    """读诊断死信队列深度（并发审查 P2-8：DLQ 此前**只写不读**，没有出口）。

    DLQ 的语义是"失败 >= int_dlq_max_retries 次、待人工排查"，没有出口就等于这些
    任务被静默丢弃——诊断请求永久停在 processing，医生看不到任何提示。

    最小可用出口：随基础设施巡检读一次深度，非零即 error（只报条数，不读 payload
    ——payload 含错误摘要，避免把临床内容写进日志）。返回深度；Redis 不可用返回 -1
    （不抛，避免打断巡检循环）。

    "谁看、多久看"仍需产品决策（reports/concurrency_perf_audit.md 第 4 节）；本函数
    只负责让 DLQ 不再是哑巴。
    """
    try:
        from app.config.redis import RedisPool
        from app.config.redis_keys import RedisKey

        client = await RedisPool.get_client()
        depth = int(await client.llen(RedisKey.DIAGNOSIS_DLQ))
    except Exception as e:
        _logger.warning("dlq_depth_check_failed", error=str(e))
        return -1

    if depth >= threshold:
        _logger.error(
            "diagnosis_dlq_not_empty",
            extra={
                "depth": depth,
                "threshold": threshold,
                "action": "人工排查 Redis 列表 ultrasound:diagnosis:dlq（P2-8 尚无重放工具）",
            },
        )
    return depth


async def run_infra_health_checks(
    redis_url: str | None = None,
    pg_dsn: str | None = None,
    interval_seconds: float = 30.0,
    disk_paths: Sequence[str] = ("/",),
) -> None:
    """基础设施巡检循环（Redis / PG / 磁盘）。

    可通过 :func:`stop_infra_health_checks` 优雅停止；等待间隔用
    ``asyncio.wait_for`` 包住事件等待，保证停止信号能被及时响应
    （原先的 ``asyncio.sleep`` 只能等满整个间隔）。

    两个连接串默认取自 settings：原实现把 ``redis://localhost:6379`` /
    ``postgresql://localhost:5432/ultrasound`` 写死在签名里，而 lifespan 是无参调用
    —— 真机上巡检**一直在探测两个不存在的服务**，日志被健康检查错误刷满
    （2026-09-28 现场部署暴露）。

    边缘端 GPU 温度由边缘设备上报后调用
    :func:`check_edge_gpu_temperature`，不在服务端巡检循环内采集。
    """
    if redis_url is None or pg_dsn is None:
        from app.config.dsn import to_asyncpg_dsn
        from app.config.settings import settings as _settings

        if redis_url is None:
            redis_url = _settings.redis_url
        if pg_dsn is None:
            pg_dsn = to_asyncpg_dsn(_settings.database_url)

    global _infra_shutdown_event
    if _infra_shutdown_event is None:
        _infra_shutdown_event = asyncio.Event()
    shutdown_event = _infra_shutdown_event

    while not shutdown_event.is_set():
        await check_redis_health(redis_url)
        await check_pg_health(pg_dsn)
        await check_dlq_depth()
        for path in disk_paths:
            try:
                check_disk_usage(path)
            except Exception as e:
                _logger.error("disk_health_check_error", error=str(e), path=path)

        try:
            await asyncio.wait_for(shutdown_event.wait(), timeout=interval_seconds)
        except asyncio.TimeoutError:
            continue
