"""02-5 链B：熔断状态 + CUSUM 累计量的 Redis 持久化（BR4 缺口闭合）。

判据：
1. 快照含 breaker 三态与 CUSUM 累计量；
2. persist 写 SETEX 到 RedisKey.AI_SAFETY_BREAKER_STATE；
3. restore 回灌 pending/suspended 状态与 s_plus 等（重启不再失忆）；
4. restore 失败/无快照 → 保持 ACTIVE（不比现状差）；
5. 同步上下文（无事件循环）schedule 为 no-op（不炸既有单测）。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.redis_keys import RedisKey
from app.observability.cusum import AISafetyCircuitBreaker
from app.observability.cusum import persistence
from app.observability.cusum.monitor import CUSUMConfig, CUSUMMonitor


@pytest.fixture()
def breaker_pair(monkeypatch):
    # threshold=3.0：2 次 severe（s_plus≈1.99）不触发报警 —— 报警会触发
    # monitor.py:82 的累加器自复位（s_plus 归零、计数保留），那是状态机
    # 自身语义，不属于持久化测试范围。
    config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
    monitor = CUSUMMonitor(config)
    breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)
    # 关键：monitor.update() 内部的落盘钩子会取**单例** breaker——若不 patch，
    # fire-and-forget 任务会用单例（全新状态）覆盖本测试写的快照（实测踩过：
    # s_plus 被覆盖成 0）。patch 后钩子与显式 persist 写同一份状态，竞态无害。
    monkeypatch.setattr(
        AISafetyCircuitBreaker, "get_instance",
        classmethod(lambda cls: breaker),
    )
    return breaker, monitor


@pytest.fixture()
def fake_redis(monkeypatch):
    store: dict = {}
    client = MagicMock()
    client.setex = AsyncMock(side_effect=lambda k, ttl, v: store.__setitem__(k, (ttl, v)))
    client.get = AsyncMock(side_effect=lambda k: store.get(k, (None, None))[1])
    # persist/restore 内部是 lazy import：patch 真实来源
    from app.config import redis as redis_module

    monkeypatch.setattr(redis_module.RedisPool, "get_client", AsyncMock(return_value=client))
    return store


class TestSnapshotAndPersist:
    @pytest.mark.asyncio
    async def test_persist_writes_snapshot(self, breaker_pair, fake_redis):
        breaker, monitor = breaker_pair
        for _ in range(2):
            monitor.update(is_severe_hallucination=True)
        assert await persistence.persist_breaker_state(breaker) is True
        ttl, raw = fake_redis[RedisKey.AI_SAFETY_BREAKER_STATE]
        payload = json.loads(raw)
        assert ttl == persistence._STATE_TTL_SECONDS
        assert payload["monitor"]["total_observations"] == 2
        assert payload["monitor"]["s_plus"] > 0
        assert payload["state"] == "active"

    @pytest.mark.asyncio
    async def test_persist_redis_failure_is_soft(self, breaker_pair, monkeypatch):
        breaker, _ = breaker_pair
        from app.config import redis as redis_module

        monkeypatch.setattr(
            redis_module.RedisPool, "get_client",
            AsyncMock(side_effect=ConnectionError("down")),
        )
        assert await persistence.persist_breaker_state(breaker) is False
        breaker.resume()  # 内存状态机不受影响


class TestRestore:
    @pytest.mark.asyncio
    async def test_restore_pending_state_and_monitor(self, breaker_pair, fake_redis):
        breaker, monitor = breaker_pair
        for _ in range(2):
            monitor.update(is_severe_hallucination=True)
        # _request_suspension → PENDING_CONFIRMATION（SUSPENDED 要等确认超时）
        breaker._request_suspension("cusum_s_plus_exceeded:1.99")
        assert breaker.is_pending_confirmation is True
        assert await persistence.persist_breaker_state(breaker) is True

        # 模拟重启：全新实例
        new_monitor = CUSUMMonitor(
            CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        )
        new_breaker = AISafetyCircuitBreaker(new_monitor)
        assert await persistence.restore_breaker_state(new_breaker) is True
        assert new_breaker.is_pending_confirmation is True
        assert new_monitor.get_state()["total_observations"] == 2

    @pytest.mark.asyncio
    async def test_restore_suspended_state(self, breaker_pair, fake_redis):
        breaker, _ = breaker_pair
        # 直接置 SUSPENDED（persistence 只测快照/回灌，不测状态机本身）
        breaker._state = breaker.SUSPENDED
        breaker._suspension_reason = "cusum_s_plus_exceeded:9.99"
        breaker._suspended_at = __import__("time").time()
        assert await persistence.persist_breaker_state(breaker) is True

        new_breaker = AISafetyCircuitBreaker(
            CUSUMMonitor(CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005))
        )
        assert await persistence.restore_breaker_state(new_breaker) is True
        assert new_breaker.is_suspended is True

    @pytest.mark.asyncio
    async def test_restore_keeps_active_when_no_snapshot(self, breaker_pair, fake_redis):
        breaker, _ = breaker_pair
        assert await persistence.restore_breaker_state(breaker) is False
        assert breaker.is_suspended is False

    @pytest.mark.asyncio
    async def test_restore_read_failure_stays_active(self, breaker_pair, monkeypatch):
        breaker, _ = breaker_pair
        from app.config import redis as redis_module

        monkeypatch.setattr(
            redis_module.RedisPool, "get_client",
            AsyncMock(side_effect=ConnectionError("down")),
        )
        assert await persistence.restore_breaker_state(breaker) is False
        assert breaker.is_suspended is False


class TestScheduleNoopWithoutLoop:
    def test_schedule_is_noop_in_sync_context(self, breaker_pair):
        breaker, _ = breaker_pair
        # 同步测试无 running loop：必须静默跳过，不得抛 RuntimeError
        persistence.schedule_breaker_state_persist(breaker)
