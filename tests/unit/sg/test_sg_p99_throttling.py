from collections import deque

import pytest

from safety_gateway.health.latency_tracker import LatencyTracker
from safety_gateway.sg_config import sg_settings as settings

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


@pytest.fixture
def tracker():
    return LatencyTracker()


async def _record_and_compute(tracker: LatencyTracker, latency_ms: float) -> dict:
    tracker._samples = deque(maxlen=settings.sg_latency_tracker_max_samples)
    await tracker.record(latency_ms)
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
