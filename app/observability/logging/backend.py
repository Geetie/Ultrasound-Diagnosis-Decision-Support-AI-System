import asyncio
import logging
from abc import ABC, abstractmethod

from .record import LogRecord

_logger = logging.getLogger(__name__)

# 并发审查 P0-3：fire-and-forget 任务追踪。
# asyncio 只持 task 的弱引用——不持强引用时 task 可能在完成前被 GC，任务体
# （日志落库）静默丢失且无任何报错。同时异常无人消费会刷
# "Task exception was never retrieved"。这里统一：持强引用集合 + 消费异常 +
# 完成即摘除 + 数量上界（避免错误风暴时 task 数与日志速率线性增长）。
_MAX_TRACKED_TASKS = 1000
_tracked_tasks: set[asyncio.Task] = set()
_tracked_dropped_count = 0


def tracked_task_count() -> int:
    """当前在飞的后台任务数（供测试与观测使用）。"""
    return len(_tracked_tasks)


def _on_tracked_done(task: asyncio.Task) -> None:
    _tracked_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.warning(
            "log_background_task_failed",
            extra={"error": str(exc), "error_type": type(exc).__name__},
        )


def spawn_tracked(coro) -> asyncio.Task | None:
    """创建后台 task 并纳入追踪集合；超过上界时丢弃（返回 ``None``）。"""
    global _tracked_dropped_count
    if len(_tracked_tasks) >= _MAX_TRACKED_TASKS:
        _tracked_dropped_count += 1
        if _tracked_dropped_count % 1000 == 1:
            _logger.warning(
                "log_background_task_saturated",
                extra={
                    "in_flight": len(_tracked_tasks),
                    "dropped_total": _tracked_dropped_count,
                },
            )
        coro.close()
        return None
    try:
        task = asyncio.ensure_future(coro)
    except RuntimeError:
        coro.close()
        return None
    _tracked_tasks.add(task)
    task.add_done_callback(_on_tracked_done)
    return task


# 兼容别名（backend.py 内部沿用旧名）
_spawn_tracked = spawn_tracked


class LogStorageBackend(ABC):
    _async_lock: asyncio.Lock | None = None

    @abstractmethod
    async def awrite(self, record: LogRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    async def awrite_batch(self, records: list[LogRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    def write(self, record: LogRecord) -> None:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                # 并发审查 P0-3：asyncio 只持 task 的弱引用，无引用的 task 可能在
                # 完成前被 GC → 日志静默丢失；且异常无人消费。高错误率时 task 数
                # 与日志速率线性增长。改为持强引用 + done_callback 摘除 + 上界。
                _spawn_tracked(self.awrite(record))
            else:
                loop.run_until_complete(self.awrite(record))
        except RuntimeError:
            asyncio.run(self.awrite(record))

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock
