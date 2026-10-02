"""ConsentWithdrawalConsumer 异常分支与工厂函数覆盖（wiring-audit 后补测）。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_sec]

import contextlib

from app.security.consent_withdrawal_consumer import (
    ConsentWithdrawalConsumer,
    ConsentWithdrawalEvent,
    DataCleanupHandler,
    SafetyActionHandler,
    create_consent_withdrawal_consumer,
)


def _event(naive=False):
    ts = datetime.now(timezone.utc if not naive else None)
    return ConsentWithdrawalEvent(
        patient_id="P1",
        operator_id="admin",
        withdrawn_at=ts,
        consent_version="1.0",
        event_id="evt-1",
    )


def _redis(**overrides):
    r = AsyncMock()
    r.scan = AsyncMock(return_value=(0, []))
    r.publish = AsyncMock()
    r.set = AsyncMock()
    r.delete = AsyncMock()
    r.xadd = AsyncMock()
    r.xgroup_create = AsyncMock()
    r.xreadgroup = AsyncMock(return_value=[])
    r.xack = AsyncMock()
    for k, v in overrides.items():
        setattr(r, k, v)
    return r


def _db_ctx(db):
    async def _gen():
        yield db

    return _gen


@pytest.fixture
def redis_patched(monkeypatch):
    r = _redis()
    monkeypatch.setattr(
        "app.config.redis.RedisPool.get_client", AsyncMock(return_value=r)
    )
    return r


@pytest.mark.anyio
class TestSafetyActionHandler:
    async def test_db_error_swallowed(self, redis_patched):
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        with patch("app.config.database.get_session", _db_ctx(db)):
            await SafetyActionHandler().handle_withdrawal(_event())  # 不应抛

    async def test_publish_error_swallowed(self, redis_patched):
        redis_patched.publish = AsyncMock(side_effect=ConnectionError("down"))
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        )
        db.commit = AsyncMock()
        with patch("app.config.database.get_session", _db_ctx(db)):
            await SafetyActionHandler().handle_withdrawal(_event())

    async def test_edge_scan_multi_page(self, redis_patched):
        key = b"ultrasound:edge:session:S1:P1"
        redis_patched.scan = AsyncMock(side_effect=[(1, [key]), (0, [])])
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        )
        db.commit = AsyncMock()
        with patch("app.config.database.get_session", _db_ctx(db)):
            await SafetyActionHandler().handle_withdrawal(_event())
        # 第二页 edge 发布 + 单页即止（cursor=0 不触发 asyncio.sleep 分支）

    async def test_inflight_cancelled_logged(self, redis_patched, monkeypatch):
        registry = MagicMock()
        registry.cancel_for_patient = MagicMock(return_value=["req-1"])
        monkeypatch.setattr(
            "app.services.inflight_diagnosis.get_inflight_registry",
            lambda: registry,
        )
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        )
        db.commit = AsyncMock()
        with patch("app.config.database.get_session", _db_ctx(db)):
            await SafetyActionHandler().handle_withdrawal(_event())
        registry.cancel_for_patient.assert_called_once()

    async def test_inflight_error_swallowed(self, redis_patched, monkeypatch):
        monkeypatch.setattr(
            "app.services.inflight_diagnosis.get_inflight_registry",
            MagicMock(side_effect=RuntimeError("boom")),
        )
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        )
        db.commit = AsyncMock()
        with patch("app.config.database.get_session", _db_ctx(db)):
            await SafetyActionHandler().handle_withdrawal(_event())

    async def test_audit_failure_swallowed(self, redis_patched):
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("audit db down"))
        with patch("app.config.database.get_session", _db_ctx(db)):
            await SafetyActionHandler().handle_withdrawal(_event())


@pytest.mark.anyio
class TestDataCleanupHandler:
    async def test_multi_page_scan_and_completed_sessions(self, redis_patched):
        keys_page = [b"ultrasound:desens:sess:S1:P1"]
        redis_patched.scan = AsyncMock(side_effect=[(1, keys_page), (0, [])])
        db = MagicMock()
        completed = [SimpleNamespace(session_id="S1")]
        db.execute = AsyncMock(
            return_value=SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: completed)
            )
        )
        db.commit = AsyncMock()
        with patch("app.config.database.get_session", _db_ctx(db)):
            await DataCleanupHandler().handle_withdrawal(_event())
        redis_patched.delete.assert_awaited()
        assert completed[0].consent_status_at_creation == "withdrawn"
        db.commit.assert_awaited_once()

    async def test_get_client_failure_swallowed(self, monkeypatch):
        async def _boom():
            raise ConnectionError("down")

        monkeypatch.setattr("app.config.redis.RedisPool.get_client", _boom)
        await DataCleanupHandler().handle_withdrawal(_event())


@pytest.mark.anyio
class TestConsumerLifecycle:
    async def test_start_busygroup_ok(self, redis_patched, monkeypatch):
        c = ConsentWithdrawalConsumer([SafetyActionHandler()])
        # 消费循环打桩：让 task 立即返回
        async def _noop_loop():
            return None

        monkeypatch.setattr(c, "_consume_loop", _noop_loop)
        await c.start()
        await c.stop()
        assert c._running is False

    async def test_start_non_busygroup_logged(self, redis_patched):
        redis_patched.xgroup_create = AsyncMock(side_effect=Exception("nope"))
        c = ConsentWithdrawalConsumer([])
        # 消费循环打桩
        async def _noop_loop():
            return None

        c._consume_loop = _noop_loop
        await c.start()
        await c.stop()

    async def test_start_get_client_failure_sets_running_false(self, monkeypatch):
        async def _boom():
            raise ConnectionError("down")

        monkeypatch.setattr("app.config.redis.RedisPool.get_client", _boom)
        c = ConsentWithdrawalConsumer([])
        await c.start()
        assert c._running is False

    async def test_consume_loop_cancelled_exits(self, redis_patched):
        c = ConsentWithdrawalConsumer([])
        task = asyncio.create_task(c._consume_loop())
        await asyncio.sleep(0.01)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


@pytest.mark.anyio
class TestProcessEventAndLatency:
    async def test_bad_timestamp_goes_dead_letter(self, redis_patched):
        c = ConsentWithdrawalConsumer([])
        fields = {
            b"patient_id": b"P1",
            b"withdrawn_at": b"not-a-date",
        }
        await c._process_event(b"m1", fields)
        redis_patched.xadd.assert_awaited_once()  # 死信流

    async def test_dead_letter_xadd_failure_critical(self, redis_patched):
        redis_patched.xadd = AsyncMock(side_effect=RuntimeError("stream down"))
        c = ConsentWithdrawalConsumer([])
        await c._handle_processing_error(b"m1", {b"a": b"b"}, RuntimeError("x"))

    async def test_e2e_latency_naive_ts_and_within_target(self, monkeypatch):
        c = ConsentWithdrawalConsumer([])
        monkeypatch.setattr(
            "app.observability.metrics_collector.get_metrics_collector",
            MagicMock(
                return_value=MagicMock(
                    record_consent_withdrawal_latency=MagicMock()
                )
            ),
            raising=False,
        )
        c._record_e2e_latency(_event(naive=True))  # naive 时间戳补时区 + 未超阈值 info

    async def test_e2e_latency_metrics_error_swallowed(self, monkeypatch):
        c = ConsentWithdrawalConsumer([])
        monkeypatch.setattr(
            "app.observability.metrics_collector.get_metrics_collector",
            MagicMock(side_effect=RuntimeError("no metrics")),
            raising=False,
        )
        c._record_e2e_latency(_event(naive=True))  # 不应抛


class TestFactory:
    async def test_default_handlers_when_setting_missing(self, monkeypatch):
        from app.config.settings import settings as app_settings

        monkeypatch.setattr(
            app_settings, "consent_withdrawal_handlers", None, raising=False
        )
        c = await create_consent_withdrawal_consumer()
        names = [h.get_handler_name() for h in c._handlers]
        assert "safety_action" in names and "data_cleanup" in names

    async def test_explicit_handler_names(self, monkeypatch):
        from app.config.settings import settings as app_settings

        monkeypatch.setattr(
            app_settings,
            "consent_withdrawal_handlers",
            ["safety_action", "data_cleanup", "scheduling_agent"],
            raising=False,
        )
        c = await create_consent_withdrawal_consumer()
        names = [h.get_handler_name() for h in c._handlers]
        assert "safety_action" in names and "data_cleanup" in names

    async def test_unknown_names_fall_back_to_safety(self, monkeypatch):
        from app.config.settings import settings as app_settings

        monkeypatch.setattr(
            app_settings, "consent_withdrawal_handlers", ["zzz"], raising=False
        )
        c = await create_consent_withdrawal_consumer()
        assert [h.get_handler_name() for h in c._handlers] == ["safety_action"]
