"""后台诊断 worker 池补测（quality-debt，核心模块 app/services；64.6% -> 目标高位）。

这一层的价值在**任务不丢**：Redis list 做队列 → 掉进程也能被别的 worker 消费；
Redis 不可用 → 降级到进程内队列（只保证单实例）；失败 ≥ N 次 → 写 DLQ 供人工排查。
每条降级路径都必须真的走到，否则"以为有兜底、实际没有"。

覆盖：
- `enqueue_diagnosis`：正常入队 / 队列满拒绝 / Redis 失败降级 / 两条路都失败；
- `_dequeue_with_timeout`：None、bytes 解码、str 原样、Redis 失败后取降级队列、
  降级队列超时、降级队列异常；
- `_handle_failure`：未超阈值只计数、超阈值写 DLQ、Redis 失败不抛；
- `_clear_retry_count`：best-effort（失败必须吞掉）；
- `_worker_loop`：成功（清计数）/ 管线失败（走失败处理）/ 空闲（含节流日志）
  / worker 自身异常（不终止 pool）/ 取消（向上传播）；
- 池启停：幂等、`app.state` 挂载、未启动时 stop 直接返回、活跃计数。

**注意**：本模块用**模块级全局**保存池状态（`_workers` / `_worker_pool_started`
/ `_fallback_queue`），跨用例会互相污染，所以下面用 autouse fixture 逐条复位。
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.redis_keys import RedisKey
from app.services import diagnosis_worker as worker_module
from app.services.diagnosis_worker import (
    _clear_retry_count,
    _dequeue_with_timeout,
    _get_fallback_queue,
    _handle_failure,
    _worker_loop,
    enqueue_diagnosis,
    get_active_worker_count,
    is_worker_pool_started,
    start_worker_pool,
    stop_worker_pool,
)


@pytest.fixture(autouse=True)
def _reset_worker_module_state(monkeypatch):
    """池状态是模块级全局：不复位会让"已启动"从上一用例漏到下一用例。"""
    monkeypatch.setattr(worker_module, "_workers", [])
    monkeypatch.setattr(worker_module, "_worker_pool_started", False)
    monkeypatch.setattr(worker_module, "_fallback_queue", None)


@pytest.fixture
def settings_patch(monkeypatch):
    """把与本模块相关的 settings 都钉成确定值。"""
    from app.config.settings import settings

    def _apply(**overrides):
        defaults = {
            "int_pending_queue_max_length": 10,
            "int_worker_blpop_timeout_seconds": 1,
            "int_worker_idle_log_interval_seconds": 0,
            "int_consumer_recovery_delay_seconds": 0,
            "int_dlq_max_retries": 3,
            "int_worker_pool_size": 2,
        }
        defaults.update(overrides)
        for key, value in defaults.items():
            monkeypatch.setattr(settings, key, value, raising=False)
        return settings

    return _apply


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


class TestEnqueueDiagnosis:
    async def test_pushes_to_redis_queue(self, settings_patch):
        settings_patch()
        client = _redis_client(llen=AsyncMock(return_value=3))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            assert await enqueue_diagnosis("req-1") is True

        client.rpush.assert_awaited_once_with(RedisKey.DIAGNOSIS_PENDING_QUEUE, "req-1")

    async def test_rejects_when_queue_full(self, settings_patch):
        """队列超长要拒收：否则积压会把内存和 Redis 一起拖垮。"""
        settings_patch(int_pending_queue_max_length=10)
        client = _redis_client(llen=AsyncMock(return_value=10))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            assert await enqueue_diagnosis("req-1") is False

        client.rpush.assert_not_awaited()

    async def test_falls_back_to_in_process_queue(self, settings_patch):
        """Redis 不可用时必须降级入队（返回 True），而不是把任务丢掉。"""
        settings_patch()
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(side_effect=ConnectionError("down"))
        ):
            assert await enqueue_diagnosis("req-1") is True

        assert _get_fallback_queue().qsize() == 1

    async def test_returns_false_when_both_paths_fail(self, settings_patch, monkeypatch):
        """两条路都失败才返回 False，且不抛异常（调用方无需重试）。"""
        settings_patch()
        monkeypatch.setattr(
            worker_module, "_get_fallback_queue", MagicMock(side_effect=RuntimeError("no queue"))
        )
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(side_effect=ConnectionError("down"))
        ):
            assert await enqueue_diagnosis("req-1") is False


class TestFallbackQueue:
    def test_is_a_lazy_singleton(self):
        first = _get_fallback_queue()
        second = _get_fallback_queue()

        assert first is second


class TestDequeueWithTimeout:
    async def test_returns_none_on_blpop_timeout(self):
        client = _redis_client(blpop=AsyncMock(return_value=None))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            assert await _dequeue_with_timeout(timeout=1) is None

    async def test_decodes_bytes_payload(self):
        """Redis 客户端返回 bytes 时要解码：否则下游会把 b"req" 当请求 id 用。"""
        client = _redis_client(blpop=AsyncMock(return_value=(b"q", b"req-9")))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            assert await _dequeue_with_timeout(timeout=1) == "req-9"

    async def test_str_payload_passed_through(self):
        client = _redis_client(blpop=AsyncMock(return_value=("q", "req-9")))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            assert await _dequeue_with_timeout(timeout=1) == "req-9"

    async def test_fallback_queue_item_returned(self):
        queue = MagicMock()
        queue.get = AsyncMock(return_value="req-fallback")
        with (
            patch.object(
                worker_module.RedisPool,
                "get_client",
                AsyncMock(side_effect=ConnectionError("down")),
            ),
            patch.object(worker_module, "_get_fallback_queue", MagicMock(return_value=queue)),
        ):
            assert await _dequeue_with_timeout(timeout=1) == "req-fallback"

    async def test_fallback_timeout_returns_none(self):
        """降级队列空等 1s 超时后返回 None（让 worker 回到空闲循环，而不是卡死）。"""
        queue = MagicMock()
        queue.get = AsyncMock(side_effect=asyncio.TimeoutError)
        with (
            patch.object(
                worker_module.RedisPool,
                "get_client",
                AsyncMock(side_effect=ConnectionError("down")),
            ),
            patch.object(worker_module, "_get_fallback_queue", MagicMock(return_value=queue)),
        ):
            assert await _dequeue_with_timeout(timeout=1) is None

    async def test_fallback_error_returns_none(self):
        queue = MagicMock()
        queue.get = AsyncMock(side_effect=RuntimeError("queue broken"))
        with (
            patch.object(
                worker_module.RedisPool,
                "get_client",
                AsyncMock(side_effect=ConnectionError("down")),
            ),
            patch.object(worker_module, "_get_fallback_queue", MagicMock(return_value=queue)),
        ):
            assert await _dequeue_with_timeout(timeout=1) is None


class TestHandleFailure:
    async def test_below_threshold_only_counts(self, settings_patch):
        settings_patch(int_dlq_max_retries=3)
        client = _redis_client(incr=AsyncMock(return_value=1))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            await _handle_failure("req-1", "boom")

        client.expire.assert_awaited_once()
        client.rpush.assert_not_awaited()

    async def test_writes_dlq_at_threshold(self, settings_patch):
        """达到阈值必须写 DLQ，且载荷要能定位问题（request_id + 时间 + 错误摘要）。"""
        settings_patch(int_dlq_max_retries=3)
        client = _redis_client(incr=AsyncMock(return_value=3))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            await _handle_failure("req-1", "boom")

        client.rpush.assert_awaited_once()
        dlq_key, payload = client.rpush.await_args.args
        assert dlq_key == RedisKey.DIAGNOSIS_DLQ
        assert payload.startswith("req-1|")
        assert payload.endswith("|boom")

    async def test_long_error_is_truncated(self, settings_patch):
        """错误摘要截断到 200 字符：否则单条 DLQ 记录可能塞进整个栈回溯。"""
        settings_patch(int_dlq_max_retries=3)
        client = _redis_client(incr=AsyncMock(return_value=5))
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            await _handle_failure("req-1", "x" * 500)

        _, payload = client.rpush.await_args.args
        assert len(payload) < 260

    async def test_redis_failure_is_swallowed(self, settings_patch):
        """失败处理本身失败不能抛：它是在 worker 的异常路径里被调用的。"""
        settings_patch()
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(side_effect=ConnectionError("down"))
        ):
            await _handle_failure("req-1", "boom")


class TestClearRetryCount:
    async def test_deletes_retry_key(self):
        client = _redis_client()
        with patch.object(worker_module.RedisPool, "get_client", AsyncMock(return_value=client)):
            await _clear_retry_count("req-1")

        client.delete.assert_awaited_once_with(RedisKey.diagnosis_retry_count("req-1"))

    async def test_failure_is_swallowed(self):
        with patch.object(
            worker_module.RedisPool, "get_client", AsyncMock(side_effect=ConnectionError("down"))
        ):
            await _clear_retry_count("req-1")


class TestWorkerLoop:
    """驱动无限循环的方式：让出队函数按序给出「一次任务 + 一次取消」。

    `_worker_loop` 显式把 CancelledError 向上抛（见模块 217-219 行），
    所以用它作为"跑完一轮就退出"的出口，比给循环加超时更贴近真实关闭路径。
    """

    def _dequeue_sequence(self, monkeypatch, *values):
        calls = {"n": 0}

        async def _dequeue(timeout):
            index = calls["n"]
            calls["n"] += 1
            if index >= len(values):
                raise asyncio.CancelledError
            value = values[index]
            if isinstance(value, Exception):
                raise value
            return value

        monkeypatch.setattr(worker_module, "_dequeue_with_timeout", _dequeue)

    async def test_success_path_clears_retry_count(self, settings_patch, monkeypatch):
        settings_patch()
        pipeline = AsyncMock(return_value=True)
        cleared = AsyncMock()
        monkeypatch.setattr(worker_module, "_clear_retry_count", cleared)
        self._dequeue_sequence(monkeypatch, "req-1")

        with (
            patch("app.services.diagnosis_pipeline.run_diagnosis_pipeline", pipeline),
            pytest.raises(asyncio.CancelledError),
        ):
            await _worker_loop(0)

        pipeline.assert_awaited_once_with("req-1")
        cleared.assert_awaited_once_with("req-1")

    async def test_pipeline_failure_goes_to_failure_handler(self, settings_patch, monkeypatch):
        """管线抛异常必须走失败处理（计数 → DLQ），而不是静默丢掉任务。"""
        settings_patch()
        handled = AsyncMock()
        monkeypatch.setattr(worker_module, "_handle_failure", handled)
        self._dequeue_sequence(monkeypatch, "req-1")

        with patch(
            "app.services.diagnosis_pipeline.run_diagnosis_pipeline",
            AsyncMock(side_effect=RuntimeError("pipeline exploded")),
        ), pytest.raises(asyncio.CancelledError):
            await _worker_loop(0)

        handled.assert_awaited_once_with("req-1", "pipeline exploded")

    async def test_idle_iteration_keeps_loop_alive(self, settings_patch, monkeypatch):
        """空闲（None）时只打节流日志并继续，不退出、不报错。"""
        settings_patch(int_worker_idle_log_interval_seconds=0)
        self._dequeue_sequence(monkeypatch, None)

        with pytest.raises(asyncio.CancelledError):
            await _worker_loop(0)

    async def test_unexpected_loop_error_is_contained(self, settings_patch, monkeypatch):
        """worker 自身异常（不是管线异常）不得终止 pool：打日志 + 退避后继续。"""
        settings_patch(int_consumer_recovery_delay_seconds=0)
        self._dequeue_sequence(monkeypatch, RuntimeError("dequeue exploded"))

        with pytest.raises(asyncio.CancelledError):
            await _worker_loop(0)


class TestWorkerPoolLifecycle:
    @pytest.fixture
    def fake_loop(self, monkeypatch):
        """顶替真实 worker 循环：真实循环会空转抢 CPU 且依赖 Redis 状态。"""
        started = []

        async def _loop(worker_id):
            started.append(worker_id)
            await asyncio.Event().wait()

        monkeypatch.setattr(worker_module, "_worker_loop", _loop)
        return started

    async def test_start_then_stop(self, settings_patch, fake_loop):
        settings_patch(int_worker_pool_size=2)
        app = MagicMock()
        app.state = MagicMock()

        await start_worker_pool(app)
        assert is_worker_pool_started() is True
        assert get_active_worker_count() == 2
        assert len(started_workers := app.state.diagnosis_workers) == 2
        assert started_workers is not None

        await stop_worker_pool()

        assert is_worker_pool_started() is False
        assert get_active_worker_count() == 0

    async def test_start_is_idempotent(self, settings_patch, fake_loop):
        """重复启动不该起第二套 worker（否则同一任务被消费两次）。

        断言"创建了几个 task"而不是"循环跑了几次"：task 要等事件循环调度才会
        执行到第一行，`start_worker_pool` 返回时它可能还没被调度（第一版就栽在这，
        拿到 0 == 1）。
        """
        settings_patch(int_worker_pool_size=1)
        app = MagicMock()
        app.state = MagicMock()

        await start_worker_pool(app)
        await start_worker_pool(app)

        assert len(app.state.diagnosis_workers) == 1
        await stop_worker_pool()

    async def test_start_without_app_skips_state_attach(self, settings_patch, fake_loop):
        """app 为空时跳过 `app.state` 挂载（纯队列模式，供脚本与测试使用）。"""
        settings_patch(int_worker_pool_size=1)

        await start_worker_pool(None)

        assert is_worker_pool_started() is True
        await stop_worker_pool()

    async def test_stop_without_start_is_noop(self, settings_patch):
        await stop_worker_pool()

        assert is_worker_pool_started() is False

    async def test_shutdown_timeout_is_logged_not_raised(self, settings_patch, monkeypatch):
        """worker 不响应取消时只告警（不能卡住 lifespan 关闭）。

        `_workers` 里必须放**真的 awaitable**：第一版我放了 MagicMock，
        `asyncio.gather` 直接抛 "a coroutine or an awaitable is required"，
        根本没走到超时分支。
        """
        settings_patch(int_worker_pool_size=1, int_consumer_recovery_delay_seconds=0)
        pending = asyncio.get_running_loop().create_future()
        monkeypatch.setattr(worker_module, "_workers", [pending])
        monkeypatch.setattr(worker_module, "_worker_pool_started", True)
        monkeypatch.setattr(
            worker_module.asyncio, "wait_for", AsyncMock(side_effect=asyncio.TimeoutError)
        )

        await stop_worker_pool(timeout=0.01)

        assert is_worker_pool_started() is False
        assert worker_module._workers == []
