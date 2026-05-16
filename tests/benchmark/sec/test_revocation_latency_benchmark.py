import time
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

import pytest
pytestmark = [pytest.mark.track_sec, pytest.mark.track_inf, pytest.mark.benchmark]

from app.security.consent_withdrawal_consumer import (
    ConsentWithdrawalConsumer,
    SafetyActionHandler,
    ConsentWithdrawalEvent,
)


class FakeRedisStream:
    def __init__(self):
        self._streams = {}
        self._consumer_groups = {}
        self._pending = {}

    async def xadd(self, stream, fields, maxlen=None, id="*"):
        if stream not in self._streams:
            self._streams[stream] = []
        entry_id = f"{int(time.time() * 1000)}-0"
        self._streams[stream].append((entry_id, fields))
        return entry_id

    async def xgroup_create(self, stream, group, id="$", mkstream=True):
        key = (stream, group)
        self._consumer_groups[key] = id

    async def xreadgroup(
        self, group, consumer, streams, count=None, block=None, noack=False
    ):
        results = []
        for stream, last_id in streams:
            if stream in self._streams:
                entries = self._streams[stream][-count:] if count else self._streams[stream]
                if entries:
                    results.append((stream, entries))
        return results if results else []

    async def xack(self, stream, group, *ids):
        return len(ids)


class InMemoryRedisClient:
    def __init__(self):
        self._data = {}
        self._channels = {}

    async def set(self, key, value, ex=None):
        self._data[key] = value

    async def get(self, key):
        return self._data.get(key)

    async def publish(self, channel, message):
        if channel not in self._channels:
            self._channels[channel] = []
        self._channels[channel].append(message)

    async def scan_iter(self, pattern):
        import fnmatch
        for key in list(self._data.keys()):
            if fnmatch.fnmatch(key, pattern):
                yield key

    async def delete(self, key):
        self._data.pop(key, None)


class TestRevocationLatencyBenchmark:
    """同意撤销延迟基准测试。

    注意：本测试在模拟I/O环境下运行（Redis/DB均为内存模拟），
    测量结果仅反映逻辑正确性和相对性能，不代表真实网络/数据库的绝对延迟。
    不应将此处的基准数值直接用于生产环境SLA评估。
    """
    @pytest.mark.asyncio
    async def test_single_withdrawal_event_processing_latency(self):
        handler = SafetyActionHandler()
        event = ConsentWithdrawalEvent(
            patient_id="P001",
            operator_id="OP_001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1",
            event_id="evt_001",
        )

        fake_redis = InMemoryRedisClient()
        mock_db_result = MagicMock()
        mock_db_result.scalars().all.return_value = []
        mock_db_session = AsyncMock()
        mock_db_session.execute.return_value = mock_db_result
        mock_db_session.commit = AsyncMock()

        async def mock_get_session():
            yield mock_db_session

        mock_audit = AsyncMock()

        with patch("app.config.redis.RedisPool.get_client", return_value=fake_redis), \
             patch("app.security.consent_withdrawal_consumer.get_session", side_effect=mock_get_session, create=True), \
             patch("app.security.consent_withdrawal_consumer.AuditService", return_value=mock_audit, create=True):
            for _ in range(10):
                await handler.handle_withdrawal(event)

            latencies = []
            for i in range(100):
                ev = ConsentWithdrawalEvent(
                    patient_id=f"P{i:03d}",
                    operator_id="OP_001",
                    withdrawn_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    event_id=f"evt_{i:03d}",
                )
                start = time.monotonic()
                await handler.handle_withdrawal(ev)
                elapsed_ms = (time.monotonic() - start) * 1000
                latencies.append(elapsed_ms)

        latencies.sort()
        p50 = latencies[50]
        p95 = latencies[95]
        p99 = latencies[99]

        assert p99 < 500, f"P99 latency {p99:.2f}ms exceeds 500ms SLA"
        assert p95 < 100, f"P95 latency {p95:.2f}ms unexpectedly high"

    @pytest.mark.asyncio
    async def test_batch_withdrawal_events_latency(self):
        handler = SafetyActionHandler()
        fake_redis = InMemoryRedisClient()
        mock_db_result = MagicMock()
        mock_db_result.scalars().all.return_value = []
        mock_db_session = AsyncMock()
        mock_db_session.execute.return_value = mock_db_result
        mock_db_session.commit = AsyncMock()

        async def mock_get_session():
            yield mock_db_session

        mock_audit = AsyncMock()

        with patch("app.config.redis.RedisPool.get_client", return_value=fake_redis), \
             patch("app.security.consent_withdrawal_consumer.get_session", side_effect=mock_get_session, create=True), \
             patch("app.security.consent_withdrawal_consumer.AuditService", return_value=mock_audit, create=True):
            events = [
                ConsentWithdrawalEvent(
                    patient_id=f"P{i:03d}",
                    operator_id="OP_001",
                    withdrawn_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    event_id=f"evt_{i:03d}",
                )
                for i in range(50)
            ]

            for _ in range(10):
                await handler.handle_withdrawal(events[0])

            start = time.monotonic()
            for event in events:
                await handler.handle_withdrawal(event)
            total_ms = (time.monotonic() - start) * 1000

        avg_ms = total_ms / len(events)
        assert avg_ms < 10, f"Average latency {avg_ms:.2f}ms per event too high"

    @pytest.mark.asyncio
    async def test_redis_stream_write_latency(self):
        fake_stream = FakeRedisStream()
        event_data = {
            "patient_id": "P001",
            "action": "withdrawn",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for _ in range(10):
            await fake_stream.xadd("consent:withdrawal", event_data)

        latencies = []
        for _ in range(100):
            start = time.monotonic()
            await fake_stream.xadd("consent:withdrawal", event_data)
            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p99 = latencies[99]
        assert p99 < 50, f"Redis stream write P99 {p99:.2f}ms too high"

    @pytest.mark.asyncio
    async def test_concurrent_withdrawal_events(self):
        import asyncio

        handler = SafetyActionHandler()
        fake_redis = InMemoryRedisClient()
        mock_db_result = MagicMock()
        mock_db_result.scalars().all.return_value = []
        mock_db_session = AsyncMock()
        mock_db_session.execute.return_value = mock_db_result
        mock_db_session.commit = AsyncMock()

        async def mock_get_session():
            yield mock_db_session

        mock_audit = AsyncMock()

        with patch("app.config.redis.RedisPool.get_client", return_value=fake_redis), \
             patch("app.security.consent_withdrawal_consumer.get_session", side_effect=mock_get_session, create=True), \
             patch("app.security.consent_withdrawal_consumer.AuditService", return_value=mock_audit, create=True):

            async def process_event(idx):
                event = ConsentWithdrawalEvent(
                    patient_id=f"P{idx:03d}",
                    operator_id="OP_001",
                    withdrawn_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    event_id=f"evt_{idx:03d}",
                )
                start = time.monotonic()
                await handler.handle_withdrawal(event)
                return (time.monotonic() - start) * 1000

            for _ in range(10):
                await handler.handle_withdrawal(ConsentWithdrawalEvent(
                    patient_id="P_WARM", operator_id="OP_001",
                    withdrawn_at=datetime.now(timezone.utc), consent_version="v1",
                    event_id="evt_warm",
                ))

            latencies = await asyncio.gather(*[process_event(i) for i in range(20)])
            max_latency = max(latencies)
            assert max_latency < 500, f"Max concurrent latency {max_latency:.2f}ms exceeds 500ms"

    @pytest.mark.asyncio
    async def test_end_to_end_withdrawal_propagation_latency(self):
        fake_stream = FakeRedisStream()

        event_data = {
            "patient_id": "P001",
            "consent_type": "diagnostic",
            "action": "withdrawn",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_id": "evt_e2e_001",
        }

        for _ in range(10):
            await fake_stream.xadd("consent:withdrawal", event_data)

        latencies = []
        for _ in range(50):
            start = time.monotonic()

            await fake_stream.xadd("consent:withdrawal", event_data)

            messages = await fake_stream.xreadgroup(
                group="consent_withdrawal_consumers",
                consumer="sec_consumer_01",
                streams=[("consent:withdrawal", ">")],
                count=1,
            )

            elapsed_ms = (time.monotonic() - start) * 1000
            latencies.append(elapsed_ms)

        latencies.sort()
        p99 = latencies[49]
        assert p99 < 100, f"E2E P99 latency {p99:.2f}ms unexpectedly high for mock stream"


class TestRealHandlerRevocationBenchmark:
    """真实Handler撤销性能基准测试。

    注意：本测试在模拟I/O环境下运行（Redis/DB均为内存模拟），
    测量结果仅反映逻辑正确性和相对性能，不代表真实网络/数据库的绝对延迟。
    不应将此处的基准数值直接用于生产环境SLA评估。
    """
    @pytest.mark.asyncio
    async def test_real_handler_p99_under_500ms(self):
        handler = SafetyActionHandler()
        event = ConsentWithdrawalEvent(
            patient_id="P_BENCH_001",
            operator_id="OP_001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1",
            event_id="evt_bench_001",
        )

        fake_redis = InMemoryRedisClient()
        mock_db_result = MagicMock()
        mock_db_result.scalars().all.return_value = []
        mock_db_session = AsyncMock()
        mock_db_session.execute.return_value = mock_db_result
        mock_db_session.commit = AsyncMock()

        async def mock_get_session():
            yield mock_db_session

        mock_audit = AsyncMock()

        with patch("app.config.redis.RedisPool.get_client", return_value=fake_redis), \
             patch("app.security.consent_withdrawal_consumer.get_session", side_effect=mock_get_session, create=True), \
             patch("app.security.consent_withdrawal_consumer.AuditService", return_value=mock_audit, create=True):
            for _ in range(10):
                await handler.handle_withdrawal(event)

            latencies = []
            for i in range(100):
                ev = ConsentWithdrawalEvent(
                    patient_id=f"P_BENCH_{i:03d}",
                    operator_id="OP_001",
                    withdrawn_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    event_id=f"evt_bench_{i:03d}",
                )
                start = time.monotonic()
                await handler.handle_withdrawal(ev)
                latencies.append((time.monotonic() - start) * 1000)

        latencies.sort()
        p99 = latencies[99]
        p95 = latencies[95]
        assert p99 < 500, f"Real handler P99 {p99:.2f}ms exceeds 500ms SLA"
        assert p95 < 200, f"Real handler P95 {p95:.2f}ms unexpectedly high"

    @pytest.mark.asyncio
    async def test_real_handler_with_sessions_p99_under_500ms(self):
        handler = SafetyActionHandler()
        event = ConsentWithdrawalEvent(
            patient_id="P_BENCH_SESSIONS",
            operator_id="OP_001",
            withdrawn_at=datetime.now(timezone.utc),
            consent_version="v1",
            event_id="evt_bench_sessions",
        )

        fake_redis = InMemoryRedisClient()
        mock_session_obj = MagicMock()
        mock_session_obj.session_id = "sess_bench_001"
        mock_session_obj.patient_id = "P_BENCH_SESSIONS"
        mock_session_obj.consent_status_at_creation = "granted"
        mock_db_result = MagicMock()
        mock_db_result.scalars().all.return_value = [mock_session_obj]
        mock_db_session = AsyncMock()
        mock_db_session.execute.return_value = mock_db_result
        mock_db_session.commit = AsyncMock()

        async def mock_get_session():
            yield mock_db_session

        mock_audit = AsyncMock()

        with patch("app.config.redis.RedisPool.get_client", return_value=fake_redis), \
             patch("app.security.consent_withdrawal_consumer.get_session", side_effect=mock_get_session, create=True), \
             patch("app.security.consent_withdrawal_consumer.AuditService", return_value=mock_audit, create=True):
            for _ in range(10):
                await handler.handle_withdrawal(event)

            latencies = []
            for i in range(100):
                ev = ConsentWithdrawalEvent(
                    patient_id=f"P_BENCH_S_{i:03d}",
                    operator_id="OP_001",
                    withdrawn_at=datetime.now(timezone.utc),
                    consent_version="v1",
                    event_id=f"evt_bench_s_{i:03d}",
                )
                start = time.monotonic()
                await handler.handle_withdrawal(ev)
                latencies.append((time.monotonic() - start) * 1000)

        latencies.sort()
        p99 = latencies[99]
        assert p99 < 500, f"Real handler (with sessions) P99 {p99:.2f}ms exceeds 500ms SLA"
