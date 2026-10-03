"""并发与性能审查回归（2026-10-03，app/observability 侧）。

``app/observability/logging`` 原先有两处 fire-and-forget：
``LogStorageBackend.write`` 每条日志一个 ``asyncio.create_task``、``BatchLogStorage``
满批时一个 ``asyncio.create_task(self._flush_batch())``。asyncio **只持 task 的弱
引用**——无引用的 task 可能在完成前被 GC，日志静默丢失且无任何报错；异常也无人
消费；突发写入时 flush 任务数无上限。现统一走 ``spawn_tracked``（持引用 + 消费
异常 + 全局上界 + in-flight 去重）。
"""
import asyncio

import pytest

from app.observability.enums import LogCategory
from app.observability.logging import backend as backend_module
from app.observability.logging.backend import LogStorageBackend
from app.observability.logging.record import LogRecord

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_tracked():
    backend_module._tracked_tasks.clear()
    yield
    for task in list(backend_module._tracked_tasks):
        task.cancel()
    backend_module._tracked_tasks.clear()


class _Echo(LogStorageBackend):
    def __init__(self) -> None:
        self.written = []
        self.gate = asyncio.Event()

    async def awrite(self, record: LogRecord) -> None:
        await self.gate.wait()
        self.written.append(record)

    async def awrite_batch(self, records) -> None:  # pragma: no cover
        self.written.extend(records)

    async def close(self) -> None:
        return None


def _record(msg: str = "hello") -> LogRecord:
    return LogRecord(category=LogCategory.OPERATIONAL, message=msg)


class TestSpawnTracked:
    async def test_holds_strong_reference_until_done(self):
        """在飞任务必须出现在追踪集合里（否则可能被 GC 静默丢弃）。"""
        backend = _Echo()
        task = backend_module.spawn_tracked(backend.awrite(_record()))
        assert backend_module.tracked_task_count() == 1
        assert task in backend_module._tracked_tasks
        backend.gate.set()
        await task
        await asyncio.sleep(0)
        assert backend_module.tracked_task_count() == 0

    async def test_exception_is_consumed_not_leaked(self):
        async def _boom():
            raise RuntimeError("write failed")

        task = backend_module.spawn_tracked(_boom())
        assert task is not None
        await asyncio.gather(task, return_exceptions=True)
        await asyncio.sleep(0)
        assert backend_module.tracked_task_count() == 0
        # 异常已被 done_callback 消费：不再抛 "exception was never retrieved"
        assert task.exception().__class__ is RuntimeError

    async def test_saturation_drops_instead_of_growing(self, monkeypatch):
        monkeypatch.setattr(backend_module, "_MAX_TRACKED_TASKS", 0)
        backend = _Echo()
        assert backend_module.spawn_tracked(backend.awrite(_record())) is None
        assert backend_module.tracked_task_count() == 0

    async def test_tracked_task_count_helper(self):
        backend = _Echo()
        task = backend_module.spawn_tracked(backend.awrite(_record()))
        assert backend_module.tracked_task_count() == 1
        backend.gate.set()
        await task


class TestBackendWriteUsesTrackedTask:
    async def test_write_schedules_tracked_task(self):
        backend = _Echo()
        backend.write(_record("sync-call"))
        await asyncio.sleep(0)
        assert backend_module.tracked_task_count() == 1
        backend.gate.set()
        for _ in range(3):
            await asyncio.sleep(0)
        assert backend_module.tracked_task_count() == 0
        assert [r.message for r in backend.written] == ["sync-call"]


class TestBatchFlushInFlightDedup:
    async def test_only_one_flush_task_in_flight(self):
        from app.observability.logging.batch import BatchLogStorageWrapper

        flushed = []

        class _FakeBatch(BatchLogStorageWrapper):
            async def _flush_batch(self):
                flushed.append(1)
                await asyncio.sleep(0.05)

        storage = _FakeBatch(_Echo(), batch_size=2, flush_interval=100)
        for i in range(10):
            storage._enqueue(_record(f"m{i}"))
        await asyncio.sleep(0)  # 让 spawn_tracked 的 task 真正开始执行
        assert len(flushed) == 1
        assert storage._flush_batch_inflight is not None
        await asyncio.sleep(0.1)
        assert len(flushed) == 1
        # 句柄保留但已结束——下一次入队会重新调度（这正是去重的判据）
        assert storage._flush_batch_inflight.done()

    async def test_flush_task_starts_after_inflight_completes(self):
        from app.observability.logging.batch import BatchLogStorageWrapper

        flushed = []

        class _FakeBatch(BatchLogStorageWrapper):
            async def _flush_batch(self):
                flushed.append(1)
                await asyncio.sleep(0.01)

        storage = _FakeBatch(_Echo(), batch_size=2, flush_interval=100)
        storage._enqueue(_record("a"))
        storage._enqueue(_record("b"))
        await asyncio.sleep(0.05)
        storage._enqueue(_record("c"))
        storage._enqueue(_record("d"))
        await asyncio.sleep(0.05)
        assert len(flushed) == 2
