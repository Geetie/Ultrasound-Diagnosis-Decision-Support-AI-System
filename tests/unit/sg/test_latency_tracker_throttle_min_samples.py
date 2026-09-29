"""守卫：P99 节流必须有足够样本才允许触发。

## 为什么需要它（2026-09-29 真机死锁）

SG 启动初期样本只有 2~3 个（冷启动慢请求，p99≈max=8.11ms，超 5ms critical 阈值）
→ 连续 3 次轮询后开启节流 → 所有 check 请求 503（E4005 sg_p99_throttling）
→ 被拒的请求不会产生新样本 → p99 冻结在超限值 → **节流永远无法恢复**。
真机表现：服务刚重启后第一例诊断 completed，第二例起恒 needs_review
（review=安全校验服务不可用）。

判据：
- 样本数 < `sg_p99_min_samples_for_throttle` 时，p99_status 必须是 unknown，
  且不得进入节流（哪怕样本值全部超阈值）；
- 样本数达标且 p99 持续超限时，节流必须触发（保护语义不回退）；
- 样本充足但延迟回落时，节流必须能解除（恢复路径仍在）。
"""
from __future__ import annotations

import asyncio

import pytest

from safety_gateway.health.latency_tracker import LatencyTracker
from safety_gateway.sg_config import sg_settings as settings


def _feed(tracker: LatencyTracker, latencies_ms: list[float]) -> None:
    async def _run() -> None:
        for value in latencies_ms:
            await tracker.record(value)

    asyncio.run(_run())


def _compute(tracker: LatencyTracker) -> dict:
    return asyncio.run(tracker.compute_stats())


def test_样本不足时永不节流():
    """复现真机死锁场景：2 个冷启动慢样本不得触发节流。"""
    tracker = LatencyTracker()
    _feed(tracker, [8.11, 8.18])

    for _ in range(10):  # 远超原来的 3 次即节流阈值
        stats = _compute(tracker)
        assert stats["p99_status"] == "unknown"
        assert tracker.is_throttling is False


def test_样本不足返回unknown并清零计数():
    tracker = LatencyTracker()
    _feed(tracker, [settings.sg_p99_latency_critical_threshold_ms + 1] * 5)

    stats = _compute(tracker)
    assert stats["p99_status"] == "unknown"
    # 用整窗快样本把慢样本全部挤出 deque，p99 才会真正回落
    _feed(tracker, [1.0] * settings.sg_latency_tracker_max_samples)
    stats = _compute(tracker)
    assert stats["p99_status"] == "ok"
    assert tracker.is_throttling is False


def test_样本充足且持续超限时节流生效():
    """保护语义不回退：真实持续超限仍必须节流。"""
    slow = settings.sg_p99_latency_critical_threshold_ms + 1.0
    tracker = LatencyTracker()
    _feed(tracker, [slow] * settings.sg_p99_min_samples_for_throttle)

    for i in range(1, 5):
        stats = _compute(tracker)
        if i < 3:
            assert tracker.is_throttling is False
        else:
            assert tracker.is_throttling is True
            assert stats["p99_status"] == "critical"


def test_节流后样本回落可恢复():
    fast = settings.sg_p99_latency_warning_threshold_ms - 1.0
    slow = settings.sg_p99_latency_critical_threshold_ms + 1.0
    tracker = LatencyTracker()
    _feed(tracker, [slow] * settings.sg_p99_min_samples_for_throttle)

    for _ in range(4):
        _compute(tracker)
    assert tracker.is_throttling is True

    # deque 滚动：新快样本把慢样本挤出去，p99 回落后节流解除
    _feed(tracker, [fast] * settings.sg_latency_tracker_max_samples)
    _compute(tracker)
    _compute(tracker)
    assert tracker.is_throttling is False


@pytest.mark.parametrize("polls", [3])
def test_临界次数边界(polls: int):
    slow = settings.sg_p99_latency_critical_threshold_ms + 1.0
    tracker = LatencyTracker()
    _feed(tracker, [slow] * settings.sg_p99_min_samples_for_throttle)

    for _ in range(polls):
        _compute(tracker)
    assert tracker.is_throttling is True
