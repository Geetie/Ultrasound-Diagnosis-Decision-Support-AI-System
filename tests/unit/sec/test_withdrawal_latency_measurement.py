"""A.140 / G49：撤回生效时延的**离线实测**（此前无实测）。

G49 的缺口是"撤回 ≤500ms 无实测"。真实链路要 Redis 流 + 各处理器，本机没有；
但**可测量的部分**是确定的：

1. 计时起点口径 —— 用流里的 `withdrawn_at`（医生点撤回的时刻），不是消费者读到消息的时刻。
   否则队列积压被排除在计时外，指标永远好看而医生实际等更久；
2. 超时分支 —— 超过 `consent_withdrawal_e2e_latency_target_ms` 必须留 warning；
3. 在进程内传播的**下界** —— 用假处理器跑一遍事件分发，量的是"除 Redis/模型外的最小开销"。

第 3 条的数字必须标注为**下界**：它不含 Redis 往返与真实处理器（DB/缓存/模型）耗时，
拿它当"满足 500ms"的证明就是拿半个证据当全部证据。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.security.consent_withdrawal_consumer import (
    ConsentWithdrawalConsumer,
    ConsentWithdrawalEvent,
)

pytestmark = pytest.mark.track_sec

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("withdrawal_latency")


def _event(age_seconds: float) -> ConsentWithdrawalEvent:
    return ConsentWithdrawalEvent(
        patient_id="p1",
        operator_id="doc1",
        withdrawn_at=datetime.now(timezone.utc) - timedelta(seconds=age_seconds),
        consent_version="v1",
        event_id="e1",
    )


def _recorded():
    collector = MagicMock()
    collector.record_consent_withdrawal_latency = MagicMock()
    return collector


def test_metric_records_elapsed_from_withdrawn_at():
    collector = _recorded()
    with patch(
        "app.observability.metrics_collector.get_metrics_collector", return_value=collector
    ):
        ConsentWithdrawalConsumer._record_e2e_latency(_event(0.12))

    elapsed, target = collector.record_consent_withdrawal_latency.call_args.args
    assert 0.10 <= elapsed <= 0.5, f"计时起点疑似用了'读到消息'而不是 withdrawn_at：{elapsed}"
    assert target == pytest.approx(settings.consent_withdrawal_e2e_latency_target_ms / 1000.0)


def test_exceeded_target_warns():
    """超时必须留 warning —— 用 structlog 的 logger 打桩断言（caplog 看不到 structlog）。"""
    from app.security import consent_withdrawal_consumer as mod

    collector = _recorded()
    fake_logger = MagicMock()
    with patch(
        "app.observability.metrics_collector.get_metrics_collector", return_value=collector
    ), patch.object(mod, "logger", fake_logger):
        ConsentWithdrawalConsumer._record_e2e_latency(_event(3.0))

    events = [c.args[0] for c in fake_logger.warning.call_args_list]
    assert "consent_withdrawal_e2e_latency_exceeded_target" in events
    assert collector.record_consent_withdrawal_latency.called, "超时也必须记录指标（否则看不见）"


def test_naive_withdrawn_at_does_not_crash():
    """上游可能写 naive UTC；补时区而不是抛 TypeError（否则这条链路静默失效）。"""
    collector = _recorded()
    event = _event(0.1)
    event.withdrawn_at = event.withdrawn_at.replace(tzinfo=None)
    with patch(
        "app.observability.metrics_collector.get_metrics_collector", return_value=collector
    ):
        ConsentWithdrawalConsumer._record_e2e_latency(event)
    assert collector.record_consent_withdrawal_latency.called


@pytest.mark.asyncio
async def test_in_process_dispatch_lower_bound(caplog):
    """进程内分发下界（不含 Redis 往返与真实处理器）——数字进日志，不当成"满足 500ms"。"""
    handlers = []
    for _ in range(3):
        h = MagicMock()
        h.get_handler_name = MagicMock(return_value="fake")
        h.handle_withdrawal = AsyncMock()
        handlers.append(h)

    consumer = ConsentWithdrawalConsumer(handlers=handlers)
    collector = _recorded()
    fields = {
        b"patient_id": b"p1",
        b"operator_id": b"doc1",
        b"withdrawn_at": (datetime.now(timezone.utc) - timedelta(milliseconds=50)).isoformat().encode(),
        b"consent_version": b"v1",
    }

    started = time.perf_counter()
    with patch(
        "app.observability.metrics_collector.get_metrics_collector", return_value=collector
    ), caplog.at_level(logging.INFO):
        await consumer._process_event(b"1-0", fields)
    in_process_ms = (time.perf_counter() - started) * 1000.0

    logger.info("withdrawal in-process dispatch lower bound: %.2f ms", in_process_ms)
    assert all(h.handle_withdrawal.await_count == 1 for h in handlers)
    assert collector.record_consent_withdrawal_latency.called
    # 下界应当远小于目标；但这**不是**端到端达标证明
    assert in_process_ms < settings.consent_withdrawal_e2e_latency_target_ms
