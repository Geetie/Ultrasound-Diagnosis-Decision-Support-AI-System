"""T-G1-INT-06：同意撤回端到端验收。

验收三条：
1. 撤回后 <500ms 生效——计时起点是**医生点撤回的时刻**（流消息的 withdrawn_at），
   不是消费者读到消息的时刻，否则队列积压被排除在计时外，指标永远好看；
2. 在途诊断被取消——此前只"下一次检查时拦截"，已在跑的那次会继续算完并写库；
3. 越线可被观测到——直方图看分布，越线计数供告警。

本文件不打桩业务逻辑，只替掉外部依赖（Redis / DB / 审计），被验证的是真实的
``SafetyActionHandler`` 与 ``ConsentWithdrawalConsumer``。
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.security.consent_withdrawal_consumer import (
    ConsentWithdrawalConsumer,
    ConsentWithdrawalEvent,
    ConsentWithdrawalHandler,
    SafetyActionHandler,
)
from app.services.inflight_diagnosis import get_inflight_registry


class _FakeRedis:
    def __init__(self):
        self.sets: list = []
        self.published: list = []

    async def set(self, key, value, ex=None):
        self.sets.append((key, value))

    async def publish(self, channel, payload):
        self.published.append((channel, payload))

    async def scan(self, cursor=0, match=None, count=100):
        return (0, [])


class _FakeScalars:
    def all(self):
        return []


class _FakeResult:
    def scalars(self):
        return _FakeScalars()


class _FakeDbSession:
    def __init__(self):
        self.committed = False

    async def execute(self, *args, **kwargs):
        return _FakeResult()

    async def commit(self):
        self.committed = True


async def _fake_get_session():
    yield _FakeDbSession()


class _RecordingCollector:
    def __init__(self):
        self.observations: list = []

    def record_consent_withdrawal_latency(self, seconds, target_seconds):
        self.observations.append((seconds, target_seconds))


class _EchoHandler(ConsentWithdrawalHandler):
    def __init__(self):
        self.events: list = []

    async def handle_withdrawal(self, event):
        self.events.append(event)

    def get_handler_name(self):
        return "echo"


@pytest.fixture(autouse=True)
def _clean_registry():
    get_inflight_registry().reset()
    yield
    get_inflight_registry().reset()


@pytest.fixture
def patched_deps(monkeypatch):
    """替掉 Redis / DB / 审计三类外部依赖，保留处理器的真实编排。"""
    fake_redis = _FakeRedis()

    async def _get_client():
        return fake_redis

    import app.config.database as database_module
    import app.config.redis as redis_module

    monkeypatch.setattr(redis_module.RedisPool, "get_client", staticmethod(_get_client))
    monkeypatch.setattr(database_module, "get_session", _fake_get_session)

    class _StubAuditService:
        def __init__(self, db):
            self.db = db

        async def log_action(self, **kwargs):
            return None

    import app.services.audit_service as audit_module

    monkeypatch.setattr(audit_module, "AuditService", _StubAuditService)
    return fake_redis


def _event(patient_id="pat_e2e", withdrawn_at=None) -> ConsentWithdrawalEvent:
    return ConsentWithdrawalEvent(
        patient_id=patient_id,
        operator_id="doc_01",
        withdrawn_at=withdrawn_at or datetime.now(timezone.utc),
        consent_version="v1",
        event_id="evt_1",
    )


class TestInFlightCancellation:
    async def test_withdrawal_cancels_running_diagnosis(self, patched_deps):
        """在途诊断必须被真的打断，而不是等它自己跑完。"""
        started = asyncio.Event()
        finished = asyncio.Event()

        async def _long_diagnosis():
            # 与真实管线一致：由被取消的协程**自己**登记（register 捕获调用者任务）
            get_inflight_registry().register(patient_id="pat_e2e", request_id="req_1")
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                finished.set()

        task = asyncio.create_task(_long_diagnosis(), name="inflight-diagnosis")
        await started.wait()
        assert get_inflight_registry().in_flight_request_ids() == ["req_1"]

        await SafetyActionHandler().handle_withdrawal(_event())

        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert finished.is_set(), "被取消的任务其 finally 必须执行（用于摘牌）"

    async def test_cancellation_only_affects_that_patient(self, patched_deps):
        """撤回只针对该患者，不能误伤其他患者的在途诊断。"""
        other_started = asyncio.Event()

        async def _diagnosis(patient_id, request_id, started_event=None):
            get_inflight_registry().register(patient_id=patient_id, request_id=request_id)
            if started_event is not None:
                started_event.set()
            await asyncio.sleep(30)

        other_task = asyncio.create_task(
            _diagnosis("pat_other", "req_other", other_started)
        )
        target_task = asyncio.create_task(_diagnosis("pat_e2e", "req_1"))
        await other_started.wait()

        await SafetyActionHandler().handle_withdrawal(_event())

        # cancel() 只是投递取消请求，需让事件循环跑一轮才能真正落到任务上
        await asyncio.sleep(0)
        with pytest.raises(asyncio.CancelledError):
            await target_task
        assert not other_task.done(), "其他患者的在途诊断不得被误取消"
        other_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await other_task

    async def test_no_inflight_is_not_an_error(self, patched_deps):
        """没有在途诊断时撤回照常完成（多数撤回都发生在诊断间隙）。"""
        await SafetyActionHandler().handle_withdrawal(_event(patient_id="pat_idle"))

        assert get_inflight_registry().in_flight_request_ids() == []

    async def test_cancelled_request_is_unregistered(self, patched_deps):
        """取消后必须摘牌，否则后续撤回会去 cancel 一个已结束的任务。"""
        async def _long_diagnosis():
            get_inflight_registry().register(patient_id="pat_e2e", request_id="req_1")
            try:
                await asyncio.sleep(30)
            finally:
                get_inflight_registry().unregister("req_1")

        task = asyncio.create_task(_long_diagnosis(), name="inflight-diagnosis")
        await asyncio.sleep(0)

        await SafetyActionHandler().handle_withdrawal(_event())

        with pytest.raises(asyncio.CancelledError):
            await task
        assert get_inflight_registry().in_flight_request_ids() == []


class TestE2ELatency:
    async def test_latency_measured_from_withdrawal_moment(self, patched_deps, monkeypatch):
        """计时起点必须是 withdrawn_at，而不是消费者开始处理的时刻。"""
        collector = _RecordingCollector()

        import app.observability.metrics_collector as metrics_module

        monkeypatch.setattr(metrics_module, "get_metrics_collector", lambda: collector)

        withdrawn_at = datetime.now(timezone.utc) - timedelta(milliseconds=200)
        await SafetyActionHandler().handle_withdrawal(_event(withdrawn_at=withdrawn_at))

        consumer = ConsentWithdrawalConsumer([_EchoHandler()])
        await consumer._process_event(
            b"1-0",
            {
                "patient_id": "pat_e2e",
                "operator_id": "doc_01",
                "withdrawn_at": withdrawn_at.isoformat(),
                "consent_version": "v1",
            },
        )

        assert len(collector.observations) == 1
        elapsed, target = collector.observations[0]
        assert elapsed >= 0.2, "时延应包含撤回动作到消费之间的间隔"
        assert target == pytest.approx(0.5)

    async def test_under_target_is_recorded_as_pass(self, patched_deps, monkeypatch):
        collector = _RecordingCollector()

        import app.observability.metrics_collector as metrics_module

        monkeypatch.setattr(metrics_module, "get_metrics_collector", lambda: collector)

        handler = _EchoHandler()
        consumer = ConsentWithdrawalConsumer([handler])
        await consumer._process_event(
            b"1-0",
            {
                "patient_id": "pat_e2e",
                "operator_id": "doc_01",
                "withdrawn_at": datetime.now(timezone.utc).isoformat(),
                "consent_version": "v1",
            },
        )

        elapsed, target = collector.observations[0]
        assert elapsed < target
        assert handler.events, "处理器必须真的被执行过"

    async def test_naive_timestamp_does_not_crash(self, patched_deps, monkeypatch):
        """上游可能写 naive 时间戳；不补时区会直接 TypeError 让整条撤回链断掉。"""
        collector = _RecordingCollector()

        import app.observability.metrics_collector as metrics_module

        monkeypatch.setattr(metrics_module, "get_metrics_collector", lambda: collector)

        naive = datetime.now(timezone.utc).replace(tzinfo=None)
        consumer = ConsentWithdrawalConsumer([_EchoHandler()])
        await consumer._process_event(
            b"1-0",
            {
                "patient_id": "pat_e2e",
                "operator_id": "doc_01",
                "withdrawn_at": naive.isoformat(),
                "consent_version": "v1",
            },
        )

        assert len(collector.observations) == 1
        assert collector.observations[0][0] < 5.0

    async def test_metric_failure_does_not_break_withdrawal(self, patched_deps, monkeypatch):
        """埋点失败不得影响撤回本身——安全动作优先于观测。"""
        import app.observability.metrics_collector as metrics_module

        def _boom():
            raise RuntimeError("metrics backend down")

        monkeypatch.setattr(metrics_module, "get_metrics_collector", _boom)

        handler = _EchoHandler()
        consumer = ConsentWithdrawalConsumer([handler])
        await consumer._process_event(
            b"1-0",
            {
                "patient_id": "pat_e2e",
                "operator_id": "doc_01",
                "withdrawn_at": datetime.now(timezone.utc).isoformat(),
                "consent_version": "v1",
            },
        )

        assert handler.events, "埋点炸了也必须已执行撤回处理器"

    async def test_target_from_settings(self, monkeypatch):
        """验收线取自配置，改配置即改判据（避免又一处硬编码）。"""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "consent_withdrawal_e2e_latency_target_ms", 250)

        collector = _RecordingCollector()
        import app.observability.metrics_collector as metrics_module

        monkeypatch.setattr(metrics_module, "get_metrics_collector", lambda: collector)

        consumer = ConsentWithdrawalConsumer([_EchoHandler()])
        await consumer._process_event(
            b"1-0",
            {
                "patient_id": "pat_e2e",
                "operator_id": "doc_01",
                "withdrawn_at": datetime.now(timezone.utc).isoformat(),
                "consent_version": "v1",
            },
        )

        assert collector.observations[0][1] == pytest.approx(0.25)


class TestConsumerDispatch:
    async def test_all_handlers_run_before_latency_is_recorded(self, patched_deps, monkeypatch):
        """时延必须是"全部处理器执行完毕"的时刻，早记会漏掉真正的生效时间。"""
        order: list = []

        class _SlowHandler(ConsentWithdrawalHandler):
            async def handle_withdrawal(self, event):
                await asyncio.sleep(0.05)
                order.append("handler_done")

            def get_handler_name(self):
                return "slow"

        class _OrderCollector(_RecordingCollector):
            def record_consent_withdrawal_latency(self, seconds, target_seconds):
                order.append("metric_recorded")
                super().record_consent_withdrawal_latency(seconds, target_seconds)

        collector = _OrderCollector()
        import app.observability.metrics_collector as metrics_module

        monkeypatch.setattr(metrics_module, "get_metrics_collector", lambda: collector)

        consumer = ConsentWithdrawalConsumer([_SlowHandler()])
        await consumer._process_event(
            b"1-0",
            {
                "patient_id": "pat_e2e",
                "operator_id": "doc_01",
                "withdrawn_at": datetime.now(timezone.utc).isoformat(),
                "consent_version": "v1",
            },
        )

        assert order == ["handler_done", "metric_recorded"]
        assert collector.observations[0][0] >= 0.05


class TestSafetyHandlerStillMarksSessions:
    async def test_withdrawal_flag_written_to_redis(self, patched_deps):
        """取消在途不能顶掉原有动作：撤回标记仍要落 Redis 供后续请求拦截。"""
        await SafetyActionHandler().handle_withdrawal(_event(patient_id="pat_mark"))

        published_channels = [channel for channel, _ in patched_deps.published]
        assert any("withdrawal" in str(channel) for channel in published_channels)


class TestPipelineCancelStatusWrite:
    async def test_mark_cancelled_writes_terminal_status(self):
        """被取消的请求不能停在 in_progress：前端会一直转圈且无从解释。"""
        from app.services import diagnosis_pipeline

        captured: dict = {}

        class _StubRepo:
            def __init__(self, db):
                self.db = db

            async def update_result(self, **kwargs):
                captured.update(kwargs)

        with patch.object(diagnosis_pipeline, "DiagnosisRepository", _StubRepo):
            await diagnosis_pipeline._mark_result_cancelled("req_cancel")

        assert captured["request_id"] == "req_cancel"
        assert captured["status"] == "cancelled"
        assert captured["findings"]["error"] == "consent_withdrawn"
