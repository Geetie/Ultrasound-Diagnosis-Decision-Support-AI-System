"""T-GA-INT-01 — 后台诊断 worker 池。

提供基于 Redis list 的「生产者-消费者」架构：

- :func:`enqueue_diagnosis` ：``submit_diagnosis_request`` 端点调用，
  把 ``request_id`` RPUSH 到 :data:`RedisKey.DIAGNOSIS_PENDING_QUEUE`。
- :func:`start_worker_pool` ：lifespan 启动期调用，启动 N 个 worker 协程，
  每个 worker BLPOP 队列并通过 :func:`app.services.diagnosis_pipeline.run_diagnosis_pipeline`
  执行完整诊断管线。
- :func:`stop_worker_pool` ：lifespan shutdown 调用，取消所有 worker 并
  等待最多 5s 完成清理。

设计要点（来自 :file:`.trae/documents/plan-T-GA-INT-01-e2e-integration.md`）：

1. **Redis list 做队列** — 进程崩溃后任务不丢（持久化到 Redis），
   可被多 worker 实例消费。
2. **降级到 in-process ``asyncio.Queue``** — Redis 不可用时降级，
   仅单实例生效。
3. **DLQ（dead-letter queue）** — 失败 ≥ ``int_dlq_max_retries`` 次后
   写入 :data:`RedisKey.DIAGNOSIS_DLQ`，供运维人工排查。
4. **延迟埋点** — 每个 worker 用 ``time.monotonic()`` 记录 BLPop
   等待时间、消费时间；管线内部 6 阶段 latency 由 pipeline 模块输出。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from app.config.redis import RedisPool
from app.config.redis_keys import RedisKey
from app.config.settings import settings

logger = structlog.get_logger("app.services.diagnosis_worker")


# ----------------------------------------------------------------------
# 全局 worker 池状态
# ----------------------------------------------------------------------

_workers: list[asyncio.Task] = []
_worker_pool_started: bool = False
_worker_pool_lock = asyncio.Lock()
_fallback_queue: asyncio.Queue | None = None  # Redis 不可用时降级


def _get_fallback_queue() -> asyncio.Queue:
    """获取或创建 in-process fallback 队列（仅 Redis 不可用时使用）。"""
    global _fallback_queue
    if _fallback_queue is None:
        _fallback_queue = asyncio.Queue()
    return _fallback_queue


# ----------------------------------------------------------------------
# 入队 / 出队
# ----------------------------------------------------------------------


async def enqueue_diagnosis(request_id: str) -> bool:
    """把 ``request_id`` 入队到 Redis pending queue（或 in-process fallback）。

    Returns:
        True 表示入队成功；False 表示入队失败（已记 warning，调用方无需重试）。
    """
    # 1) 优先尝试 Redis
    try:
        client = await RedisPool.get_client()
        # 检查队列长度，超过阈值则拒绝入队（防积压）
        queue_len = await client.llen(RedisKey.DIAGNOSIS_PENDING_QUEUE)
        if queue_len >= settings.int_pending_queue_max_length:
            logger.error(
                "diagnosis_queue_full",
                request_id=request_id,
                queue_len=queue_len,
                max_length=settings.int_pending_queue_max_length,
            )
            return False
        await client.rpush(RedisKey.DIAGNOSIS_PENDING_QUEUE, request_id)
        logger.debug(
            "diagnosis_enqueued",
            request_id=request_id,
            queue_len=queue_len + 1,
        )
        return True
    except Exception as exc:
        logger.warning(
            "diagnosis_enqueue_redis_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )

    # 2) 降级到 in-process queue
    try:
        q = _get_fallback_queue()
        await q.put(request_id)
        logger.warning(
            "diagnosis_enqueue_fallback",
            request_id=request_id,
            queue_size=q.qsize(),
        )
        return True
    except Exception as exc:
        logger.error(
            "diagnosis_enqueue_failed",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return False


async def _dequeue_with_timeout(timeout: int) -> str | None:
    """从 Redis BLPOP（带超时），失败时回退到 in-process queue。

    Args:
        timeout: BLPOP 超时（秒）。

    Returns:
        request_id 或 None（超时/无任务）。
    """
    try:
        client = await RedisPool.get_client()
        # BLPOP 返回 (key, value) 或 None
        result = await client.blpop(
            RedisKey.DIAGNOSIS_PENDING_QUEUE,
            timeout=timeout,
        )
        if result is None:
            return None
        _, request_id = result
        if isinstance(request_id, bytes):
            request_id = request_id.decode("utf-8")
        return request_id
    except Exception as exc:
        logger.warning(
            "diagnosis_dequeue_redis_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        # 降级到 in-process queue（不阻塞，等待 fallback 中的项）
        try:
            q = _get_fallback_queue()
            return await asyncio.wait_for(q.get(), timeout=1.0)
        except asyncio.TimeoutError:
            return None
        except Exception as exc:
            logger.warning(
            "worker_blpop_fallback_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
            return None


# ----------------------------------------------------------------------
# Worker 协程
# ----------------------------------------------------------------------


async def _worker_loop(worker_id: int) -> None:
    """单个 worker 的主循环。

    行为：
    1. BLPOP 队列（超时 = ``int_worker_blpop_timeout_seconds``）；
    2. 取出 ``request_id`` 后调用 :func:`run_diagnosis_pipeline`；
    3. 失败时检查重试次数 → 超过阈值写 DLQ；
    4. 任何异常都不终止 worker（仅 sleep 后继续下一轮）。
    """
    logger.info("worker_started", worker_id=worker_id)
    last_idle_log = time.monotonic()

    while True:
        try:
            # 1) 出队
            request_id = await _dequeue_with_timeout(
                timeout=settings.int_worker_blpop_timeout_seconds
            )

            # 空闲日志（节流）
            if request_id is None:
                now = time.monotonic()
                if now - last_idle_log >= settings.int_worker_idle_log_interval_seconds:
                    logger.debug("worker_idle", worker_id=worker_id)
                    last_idle_log = now
                continue

            last_idle_log = time.monotonic()  # 收到任务时刷新空闲节流

            # 2) 执行管线
            t_start = time.monotonic()
            try:
                # 延迟导入避免循环依赖（pipeline 依赖 main lifespan 中实例化的组件）
                from app.services.diagnosis_pipeline import run_diagnosis_pipeline

                success = await run_diagnosis_pipeline(request_id)
                elapsed_ms = (time.monotonic() - t_start) * 1000.0
                logger.info(
                    "worker_task_completed",
                    worker_id=worker_id,
                    request_id=request_id,
                    success=success,
                    elapsed_ms=round(elapsed_ms, 2),
                )
                # 成功后清零重试计数
                await _clear_retry_count(request_id)
            except Exception as exc:
                elapsed_ms = (time.monotonic() - t_start) * 1000.0
                logger.error(
                    "worker_task_failed",
                    worker_id=worker_id,
                    request_id=request_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    elapsed_ms=round(elapsed_ms, 2),
                )
                # 失败：递增重试计数 → 超阈值写 DLQ
                await _handle_failure(request_id, str(exc))

        except asyncio.CancelledError:
            logger.info("worker_cancelled", worker_id=worker_id)
            raise
        except Exception as exc:
            # worker 自身异常（不应终止整个 pool）
            logger.error(
                "worker_loop_error",
                worker_id=worker_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await asyncio.sleep(settings.int_consumer_recovery_delay_seconds)


async def _handle_failure(request_id: str, error_message: str) -> None:
    """处理单次诊断失败：递增重试计数 → 超阈值写 DLQ。"""
    try:
        client = await RedisPool.get_client()
        retry_key = RedisKey.diagnosis_retry_count(request_id)
        new_count = await client.incr(retry_key)
        # 设置 TTL，避免计数 key 永久残留（24h）
        await client.expire(retry_key, 86400)

        if new_count >= settings.int_dlq_max_retries:
            # 写 DLQ
            await client.rpush(
                RedisKey.DIAGNOSIS_DLQ,
                f"{request_id}|{int(time.time())}|{error_message[:200]}",
            )
            logger.error(
                "diagnosis_written_to_dlq",
                request_id=request_id,
                retry_count=new_count,
                error=error_message[:200],
            )
    except Exception as exc:
        logger.warning(
            "diagnosis_failure_handling_error",
            request_id=request_id,
            error=str(exc),
            error_type=type(exc).__name__,
        )


async def _clear_retry_count(request_id: str) -> None:
    """成功后清零重试计数。"""
    try:
        client = await RedisPool.get_client()
        await client.delete(RedisKey.diagnosis_retry_count(request_id))
    except Exception as exc:
        logger.warning(
            "diagnosis_worker_shutdown_task_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )  # best-effort


# ----------------------------------------------------------------------
# Worker 池管理
# ----------------------------------------------------------------------


async def start_worker_pool(app, n: int | None = None) -> None:
    """启动 N 个 worker 协程（默认从 settings 读取）。

    Args:
        app: FastAPI app 实例（用于将 pool 句柄挂到 ``app.state`` 便于测试）。
        n: worker 数量（默认 ``settings.int_worker_pool_size``）。
    """
    global _workers, _worker_pool_started
    if _worker_pool_started:
        logger.warning("worker_pool_already_started")
        return

    n = n or settings.int_worker_pool_size
    async with _worker_pool_lock:
        if _worker_pool_started:
            return
        _workers = [
            asyncio.create_task(_worker_loop(worker_id=i), name=f"diagnosis-worker-{i}")
            for i in range(n)
        ]
        _worker_pool_started = True
        # 把 pool 句柄挂到 app.state 供测试和外部观察
        if app is not None:
            app.state.diagnosis_workers = _workers
        logger.info(
            "worker_pool_started",
            n_workers=n,
            queue=RedisKey.DIAGNOSIS_PENDING_QUEUE,
        )


async def stop_worker_pool(timeout: float = 5.0) -> None:
    """停止所有 worker。

    Args:
        timeout: 等待 worker 自行结束的最大秒数。
    """
    global _workers, _worker_pool_started
    if not _worker_pool_started:
        return
    async with _worker_pool_lock:
        if not _worker_pool_started:
            return
        # 1) 取消所有 worker task
        for w in _workers:
            w.cancel()
        # 2) 等待完成（带超时）
        try:
            await asyncio.wait_for(
                asyncio.gather(*_workers, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("worker_pool_shutdown_timeout", timeout=timeout)
        _workers = []
        _worker_pool_started = False
        logger.info("worker_pool_stopped")


def is_worker_pool_started() -> bool:
    """检查 worker pool 是否已启动（供 health check 使用）。"""
    return _worker_pool_started


def get_active_worker_count() -> int:
    """返回当前活跃的 worker 数量（已启动且未完成）。"""
    return sum(1 for w in _workers if not w.done())


__all__ = [
    "enqueue_diagnosis",
    "get_active_worker_count",
    "is_worker_pool_started",
    "start_worker_pool",
    "stop_worker_pool",
]
