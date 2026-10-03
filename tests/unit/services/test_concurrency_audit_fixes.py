"""并发与性能审查回归（2026-10-03，app/services 侧）。

钉住三条资源上限修复的行为契约：

- ``diagnosis_worker._get_fallback_queue``：降级队列原先**无 maxsize** 且入队
  分支不做长度检查——Redis 抖动期间降级进来的任务无限堆积直到进程 OOM。
- ``diagnosis_worker._handle_failure``：诊断 DLQ 是无 TTL 的 list，下游长时间
  故障时单调增长（同意撤回 DLQ 早已用 maxlen，此处补齐同口径）。
- ``diagnosis_worker._worker_loop``：``run_diagnosis_pipeline`` 原先**无端到端
  超时**（dispatch/fusion 是分钟级 LLM 调用），最坏 ~240s 会长时间占死 worker
  槽位 → 队列积压雪崩。现由 ``pipeline_total_budget_s`` 兜底。
- ``report_service.load_template``：原先每份报告都同步 ``read_text`` 一次。
"""
import asyncio
import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.redis_keys import RedisKey
from app.services import diagnosis_worker as worker_module
from app.services import report_service as report_module


def app_main_path() -> str:
    import app.main as main_module

    return main_module.__file__



@pytest.fixture(autouse=True)
def _reset_worker_state(monkeypatch):
    monkeypatch.setattr(worker_module, "_fallback_queue", None)
    monkeypatch.setattr(worker_module, "_workers", [])
    monkeypatch.setattr(worker_module, "_worker_pool_started", False)


def _redis_client(**overrides) -> MagicMock:
    client = MagicMock()
    client.llen = AsyncMock(return_value=0)
    client.rpush = AsyncMock(return_value=1)
    client.blpop = AsyncMock(return_value=None)
    client.incr = AsyncMock(return_value=1)
    client.expire = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


class TestFallbackQueueIsBounded:
    pytestmark = pytest.mark.asyncio
    async def test_queue_has_maxsize(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "int_pending_queue_max_length", 2)
        queue = worker_module._get_fallback_queue()
        assert queue.maxsize == 2

    async def test_rejects_when_fallback_queue_full(self, monkeypatch):
        """Redis 挂掉时降级入队也必须有上界（第 3 条必须被拒）。"""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "int_pending_queue_max_length", 2)
        client = _redis_client(llen=AsyncMock(side_effect=ConnectionError("down")))
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(return_value=client)
        ):
            assert await worker_module.enqueue_diagnosis("r1") is True
            assert await worker_module.enqueue_diagnosis("r2") is True
            assert await worker_module.enqueue_diagnosis("r3") is False

    async def test_same_queue_instance_is_reused(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "int_pending_queue_max_length", 4)
        assert worker_module._get_fallback_queue() is worker_module._get_fallback_queue()


class TestDlqIsCapped:
    pytestmark = pytest.mark.asyncio
    async def test_rpush_uses_maxlen(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "int_dlq_max_retries", 1)
        monkeypatch.setattr(settings, "int_dlq_max_length", 123)
        client = _redis_client(incr=AsyncMock(return_value=1))
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(return_value=client)
        ):
            await worker_module._handle_failure("r1", "boom")
        kwargs = client.rpush.await_args.kwargs
        assert client.rpush.await_args.args[0] == RedisKey.DIAGNOSIS_DLQ
        assert kwargs["maxlen"] == 123
        assert kwargs["approximate"] is True

    async def test_below_threshold_does_not_touch_dlq(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "int_dlq_max_retries", 3)
        client = _redis_client(incr=AsyncMock(return_value=1))
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(return_value=client)
        ):
            await worker_module._handle_failure("r1", "boom")
        client.rpush.assert_not_awaited()


class _StubPipeline:
    async def run_diagnosis_pipeline(self, request_id: str) -> bool:
        await asyncio.sleep(5)
        return True


class TestPipelineTotalBudget:
    pytestmark = pytest.mark.asyncio

    @staticmethod
    async def _drain(task, predicate, *, attempts=300):
        """等 worker 循环出现预期效果，然后主动停掉这个"永不退出"的循环。"""
        for _ in range(attempts):
            if predicate():
                break
            await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def test_slow_pipeline_is_cut_off_and_counted_as_failure(self, monkeypatch):
        """超过总预算必须被取消，并走既有失败处理（计数 → DLQ），不得静默挂死。"""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "pipeline_total_budget_s", 0.05)
        monkeypatch.setattr(settings, "int_consumer_recovery_delay_seconds", 0)

        dequeued = ["r1"]

        async def _fake_dequeue(timeout):
            # 必须真的让出控制权：否则 ``while True`` 变成不让出的紧循环，
            # 定时器回调永远等不到（测试会挂死）。
            await asyncio.sleep(0.01)
            return dequeued.pop(0) if dequeued else None

        handled = []

        async def _fake_handle(request_id, error):
            handled.append((request_id, error))

        async def _fake_clear(request_id):
            return None

        monkeypatch.setattr(worker_module, "_dequeue_with_timeout", _fake_dequeue)
        monkeypatch.setattr(worker_module, "_handle_failure", _fake_handle)
        monkeypatch.setattr(worker_module, "_clear_retry_count", _fake_clear)
        monkeypatch.setitem(
            __import__("sys").modules,
            "app.services.diagnosis_pipeline",
            _StubPipeline(),
        )

        task = asyncio.create_task(worker_module._worker_loop(worker_id=0))
        await self._drain(task, lambda: bool(handled))
        assert [rid for rid, _ in handled] == ["r1"]

    async def test_fast_pipeline_succeeds(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "pipeline_total_budget_s", 5.0)

        dequeued = ["r1"]

        async def _fake_dequeue(timeout):
            await asyncio.sleep(0.01)
            return dequeued.pop(0) if dequeued else None

        cleared = []
        failed = []

        async def _fake_clear(request_id):
            cleared.append(request_id)

        async def _fake_handle(request_id, error):
            failed.append((request_id, error))

        class _OkPipeline:
            async def run_diagnosis_pipeline(self, request_id):
                return True

        monkeypatch.setattr(worker_module, "_dequeue_with_timeout", _fake_dequeue)
        monkeypatch.setattr(worker_module, "_clear_retry_count", _fake_clear)
        monkeypatch.setattr(worker_module, "_handle_failure", _fake_handle)
        monkeypatch.setitem(
            __import__("sys").modules, "app.services.diagnosis_pipeline", _OkPipeline()
        )

        task = asyncio.create_task(worker_module._worker_loop(worker_id=0))
        await self._drain(task, lambda: bool(cleared))
        assert cleared == ["r1"]
        assert failed == []


class TestShutdownChainReleasesResources:
    """停机链必须释放 PG 连接池，且单个引擎 close 抛错不得中断整条链。

    现状（修复前）：``inference_engine.close()`` 一旦抛错 → 后面的
    fusion/rag/RedisPool.close 永不执行；且从不 dispose SQLAlchemy 引擎，
    滚动发布/多副本下残留连接会打满 PG ``max_connections``。
    """

    def test_lifespan_disposes_sqlalchemy_engine(self):
        source = Path(app_main_path()).read_text(encoding="utf-8")
        assert "async_engine.dispose()" in source, "停机链缺少 PG 连接池释放"

    def test_each_engine_close_is_individually_guarded(self):
        source = Path(app_main_path()).read_text(encoding="utf-8")
        # inference / speech 两个 close 各自独立 try（而不是裸 await）
        assert 'for attr in ("inference_engine", "speech_engine"):' in source
        assert '{attr}_close_failed' in source
        assert "db_engine_dispose_failed" in source

    def test_pool_uses_lifo_reclaim(self):
        """连接池按 LIFO 回收空闲连接：池打满时优先复用热的空闲连接。"""
        from app.config.database import async_engine

        queue = async_engine.pool._pool
        assert getattr(queue, "use_lifo", False) is True


class TestReportTemplateCache:
    def test_second_load_does_not_hit_disk(self, monkeypatch, tmp_path):
        template = tmp_path / "tpl.md"
        template.write_text("hello {{x}}", encoding="utf-8")
        monkeypatch.setattr(report_module, "TEMPLATE_DIR", tmp_path)
        report_module.reload_templates()

        reads = []
        original = type(template).read_text

        def _counting_read_text(self, *args, **kwargs):
            reads.append(self)
            return original(self, *args, **kwargs)

        monkeypatch.setattr(type(template), "read_text", _counting_read_text)
        first = report_module.load_template(name="tpl.md")
        second = report_module.load_template(name="tpl.md")
        assert first == second == "hello {{x}}"
        assert len(reads) == 1

    def test_reload_clears_cache(self, monkeypatch, tmp_path):
        template = tmp_path / "tpl.md"
        template.write_text("v1", encoding="utf-8")
        monkeypatch.setattr(report_module, "TEMPLATE_DIR", tmp_path)
        report_module.reload_templates()
        assert report_module.load_template(name="tpl.md") == "v1"
        template.write_text("v2", encoding="utf-8")
        assert report_module.load_template(name="tpl.md") == "v1"
        report_module.reload_templates()
        assert report_module.load_template(name="tpl.md") == "v2"

    def test_missing_template_falls_back_and_is_not_cached(self, monkeypatch, tmp_path):
        monkeypatch.setattr(report_module, "TEMPLATE_DIR", tmp_path / "nope")
        report_module.reload_templates()
        assert report_module.load_template(name="missing.md") == report_module._FALLBACK_TEMPLATE
        assert "missing.md" not in report_module._TEMPLATE_CACHE

    def test_cache_is_size_bounded(self, monkeypatch, tmp_path):
        monkeypatch.setattr(report_module, "TEMPLATE_DIR", tmp_path)
        monkeypatch.setattr(report_module, "_TEMPLATE_CACHE_MAX", 2)
        report_module.reload_templates()
        for i in range(5):
            path = tmp_path / f"tpl{i}.md"
            path.write_text(f"body-{i}", encoding="utf-8")
            report_module.load_template(name=f"tpl{i}.md")
            assert len(report_module._TEMPLATE_CACHE) <= 3
