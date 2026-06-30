"""T-GA-INT-01: Worker 池启停 + Redis 队列行为测试。

测试场景：
- 启动 / 停止 worker 池
- 入队 / 出队行为
- BLPop 超时返回 None
- DLQ 写入行为
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.redis_keys import RedisKey
from app.services import diagnosis_worker
from app.services.diagnosis_worker import (
    enqueue_diagnosis,
    start_worker_pool,
    stop_worker_pool,
    is_worker_pool_started,
    get_active_worker_count,
)


@pytest.fixture(autouse=True)
async def reset_worker_pool():
    """每个测试前重置 worker 池状态。"""
    await stop_worker_pool(timeout=1.0)
    diagnosis_worker._fallback_queue = None
    yield
    await stop_worker_pool(timeout=1.0)
    diagnosis_worker._fallback_queue = None


class TestWorkerPoolLifecycle:
    """测试 worker 池的启停。"""

    @pytest.mark.asyncio
    async def test_start_and_stop_worker_pool(self):
        """启动后 is_worker_pool_started() 为 True，停止后为 False。"""
        assert is_worker_pool_started() is False

        mock_app = MagicMock()
        await start_worker_pool(mock_app, n=2)

        assert is_worker_pool_started() is True
        assert get_active_worker_count() == 2

        await stop_worker_pool(timeout=1.0)
        assert is_worker_pool_started() is False

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self):
        """重复启动不会启动更多 worker。"""
        mock_app = MagicMock()
        await start_worker_pool(mock_app, n=3)
        first_count = get_active_worker_count()

        await start_worker_pool(mock_app, n=3)  # 第二次启动
        second_count = get_active_worker_count()

        assert first_count == second_count == 3
        await stop_worker_pool(timeout=1.0)

    @pytest.mark.asyncio
    async def test_start_app_state_has_workers(self):
        """启动后 app.state.diagnosis_workers 包含 worker 列表。"""
        mock_app = MagicMock()
        await start_worker_pool(mock_app, n=4)
        assert hasattr(mock_app.state, "diagnosis_workers")
        assert len(mock_app.state.diagnosis_workers) == 4
        await stop_worker_pool(timeout=1.0)


class TestEnqueueDequeue:
    """测试入队/出队行为。"""

    @pytest.mark.asyncio
    async def test_enqueue_success_with_redis(self, int_queue_key):
        """Redis 可用时入队成功。"""
        mock_redis = MagicMock()
        mock_redis.llen = AsyncMock(return_value=0)
        mock_redis.rpush = AsyncMock(return_value=1)

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            result = await enqueue_diagnosis("req_test_001")
            assert result is True
            mock_redis.rpush.assert_called_once_with(int_queue_key, "req_test_001")

    @pytest.mark.asyncio
    async def test_enqueue_queue_full_returns_false(self, int_queue_key):
        """队列满时入队返回 False。"""
        mock_redis = MagicMock()
        mock_redis.llen = AsyncMock(return_value=1000)  # 假设达到 max

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            with patch.object(diagnosis_worker.settings, "int_pending_queue_max_length", 1000):
                result = await enqueue_diagnosis("req_test_002")
                assert result is False

    @pytest.mark.asyncio
    async def test_enqueue_falls_back_to_in_process_queue(self):
        """Redis 不可用时降级到 in-process queue。"""
        with patch.object(
            diagnosis_worker.RedisPool, "get_client",
            AsyncMock(side_effect=Exception("redis unavailable")),
        ):
            result = await enqueue_diagnosis("req_test_003")
            assert result is True
            # 验证 fallback queue 有该 request
            q = diagnosis_worker._get_fallback_queue()
            assert q.qsize() == 1

    @pytest.mark.asyncio
    async def test_dequeue_returns_request_id(self, int_queue_key):
        """BLPop 返回 (key, request_id)。"""
        mock_redis = MagicMock()
        mock_redis.blpop = AsyncMock(return_value=(int_queue_key.encode(), b"req_test_004"))

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            result = await diagnosis_worker._dequeue_with_timeout(timeout=1)
            assert result == "req_test_004"

    @pytest.mark.asyncio
    async def test_dequeue_timeout_returns_none(self, int_queue_key):
        """BLPop 超时返回 None。"""
        mock_redis = MagicMock()
        mock_redis.blpop = AsyncMock(return_value=None)

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            result = await diagnosis_worker._dequeue_with_timeout(timeout=0)
            assert result is None


class TestDLQBehavior:
    """测试 DLQ 写入行为。"""

    @pytest.mark.asyncio
    async def test_retry_count_increments_on_failure(self):
        """失败时 retry 计数 +1。"""
        mock_redis = MagicMock()
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.expire = AsyncMock()

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            await diagnosis_worker._handle_failure("req_test_005", "test error")
            mock_redis.incr.assert_called_once()

    @pytest.mark.asyncio
    async def test_dlq_write_when_retry_exceeds_threshold(self, int_dlq_key):
        """retry >= max_retries 时写 DLQ。"""
        mock_redis = MagicMock()
        mock_redis.incr = AsyncMock(return_value=3)  # 已达阈值
        mock_redis.expire = AsyncMock()
        mock_redis.rpush = AsyncMock()

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            with patch.object(diagnosis_worker.settings, "int_dlq_max_retries", 3):
                await diagnosis_worker._handle_failure("req_test_006", "max retries hit")
                mock_redis.rpush.assert_called_once()
                call_args = mock_redis.rpush.call_args
                assert call_args[0][0] == int_dlq_key
                assert "req_test_006" in call_args[0][1]

    @pytest.mark.asyncio
    async def test_clear_retry_count_on_success(self):
        """成功后清零 retry 计数。"""
        mock_redis = MagicMock()
        mock_redis.delete = AsyncMock()

        with patch.object(diagnosis_worker.RedisPool, "get_client", AsyncMock(return_value=mock_redis)):
            await diagnosis_worker._clear_retry_count("req_test_007")
            mock_redis.delete.assert_called_once()


__all__ = [
    "TestWorkerPoolLifecycle",
    "TestEnqueueDequeue",
    "TestDLQBehavior",
]
