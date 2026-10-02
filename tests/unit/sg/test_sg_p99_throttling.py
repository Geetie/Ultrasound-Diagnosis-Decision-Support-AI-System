from collections import deque

import pytest

from safety_gateway.health.latency_tracker import LatencyTracker
from safety_gateway.sg_config import sg_settings as settings

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


@pytest.fixture
def tracker():
    return LatencyTracker()


async def _record_and_compute(tracker: LatencyTracker, latency_ms: float) -> dict:
    # 2026-09-29 起节流判定要求最小样本数（sg_p99_min_samples_for_throttle）：
    # 样本不足时 p99_status=unknown，不参与状态机（真机死锁修复，见
    # test_latency_tracker_throttle_min_samples.py）。本 helper 用整窗同值样本
    # 让 p99 恰等于 latency_ms，状态机语义与原单样本版本完全一致。
    # ⚠️ 固定 20，不得读 settings——2026-09-30 起全局默认为 10^9（节流停用），
    # 读全局默认会让本 helper 构造 10 亿样本挂死。
    tracker._samples = deque(
        [latency_ms] * 20,
        maxlen=settings.sg_latency_tracker_max_samples,
    )
    return await tracker.compute_stats()


@pytest.mark.asyncio
async def test_three_consecutive_critical_triggers_throttling(tracker):
    await _record_and_compute(tracker, 6.0)
    assert not tracker.is_throttling

    await _record_and_compute(tracker, 6.0)
    assert not tracker.is_throttling

    await _record_and_compute(tracker, 6.0)
    assert tracker.is_throttling
    assert tracker._critical_count == 3


@pytest.mark.asyncio
async def test_one_ok_does_not_recover_throttling(tracker):
    tracker._throttling = True
    tracker._critical_count = 3

    await _record_and_compute(tracker, 3.0)

    assert tracker.is_throttling
    assert tracker._recovery_count == 1


@pytest.mark.asyncio
async def test_two_consecutive_ok_recovers_throttling(tracker):
    tracker._throttling = True
    tracker._critical_count = 3

    await _record_and_compute(tracker, 3.0)
    assert tracker.is_throttling
    assert tracker._recovery_count == 1

    await _record_and_compute(tracker, 3.0)
    assert not tracker.is_throttling
    assert tracker._recovery_count == 0


@pytest.mark.asyncio
async def test_warning_resets_critical_count(tracker):
    tracker._critical_count = 2

    await _record_and_compute(tracker, 4.5)

    assert tracker._critical_count == 0


@pytest.mark.asyncio
async def test_critical_after_warning_starts_from_zero(tracker):
    tracker._critical_count = 2

    await _record_and_compute(tracker, 4.5)
    assert tracker._critical_count == 0

    await _record_and_compute(tracker, 6.0)
    assert tracker._critical_count == 1
    assert not tracker.is_throttling


@pytest.mark.asyncio
async def test_warning_resets_recovery_count(tracker):
    tracker._throttling = True
    tracker._critical_count = 3
    tracker._recovery_count = 1

    await _record_and_compute(tracker, 4.5)

    assert tracker._recovery_count == 0
    assert tracker.is_throttling


@pytest.mark.asyncio
async def test_is_throttling_property(tracker):
    assert not tracker.is_throttling

    tracker._throttling = True
    assert tracker.is_throttling

    tracker._throttling = False
    assert not tracker.is_throttling


@pytest.mark.asyncio
async def test_critical_resets_recovery_count(tracker):
    tracker._throttling = True
    tracker._critical_count = 3
    tracker._recovery_count = 1

    await _record_and_compute(tracker, 6.0)

    assert tracker._recovery_count == 0


@pytest.mark.asyncio
async def test_ok_resets_critical_count_when_not_throttling(tracker):
    tracker._critical_count = 2

    await _record_and_compute(tracker, 3.0)

    assert tracker._critical_count == 0


@pytest.mark.asyncio
async def test_interleaved_critical_ok_does_not_trigger_throttling(tracker):
    await _record_and_compute(tracker, 6.0)
    assert tracker._critical_count == 1

    await _record_and_compute(tracker, 3.0)
    assert tracker._critical_count == 0

    await _record_and_compute(tracker, 6.0)
    assert tracker._critical_count == 1
    assert not tracker.is_throttling
