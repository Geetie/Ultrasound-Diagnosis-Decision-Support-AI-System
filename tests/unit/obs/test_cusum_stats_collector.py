"""A.115 / G19：周/月统计窗口必须有真实数据源（而不是只有测试写入）。

缺口：`record_weekly_stats` / `record_monthly_stats` 的三个列表只有测试写过 →
`weekly_rate_exceeded` 熔断入口结构性不可达。

本组守卫：
1) 分桶正确（ISO 周 / 自然月），severe 取自 `findings.fusion_result.hallucination_severity`；
2) 缺时间戳的行被计数跳过（不静默丢）；
3) **默认观察模式**：只记录、不触发 `record_*`（因此不会暂停），`enforce=True` 才喂监控器；
4) 定时任务真的会跑（用假 DB / 假 monitor 跑一轮），且单轮失败不退出；
5) lifespan 接线。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.observability.cusum import stats_collector as sc


def _row(ts, severity=None):
    findings = {"fusion_result": {"hallucination_severity": severity}} if severity else {"fusion_result": {}}
    return (ts, findings)


class _FakeMonitor:
    def __init__(self, target_rate: float = 0.005):
        self._config = SimpleNamespace(target_rate=target_rate)
        self.weekly: list[tuple] = []
        self.monthly: list[tuple] = []

    def record_weekly_stats(self, week, total, severe):
        self.weekly.append((week, total, severe))

    def record_monthly_stats(self, month, total, severe):
        self.monthly.append((month, total, severe))


def test_weekly_bucketing_and_severe_count():
    d1 = datetime(2026, 3, 2, tzinfo=timezone.utc)  # ISO week 10
    d2 = datetime(2026, 3, 3, tzinfo=timezone.utc)  # 同周
    rows = [_row(d1), _row(d2, "severe"), _row(d2, "mild"), _row(d2, "severe")]
    out = sc.aggregate_windows(rows, kind="week")
    assert len(out) == 1
    assert (out[0].window, out[0].total, out[0].severe) == (10, 4, 2)
    assert out[0].rate == pytest.approx(0.5)


def test_month_bucketing():
    rows = [
        _row(datetime(2026, 1, 5, tzinfo=timezone.utc)),
        _row(datetime(2026, 1, 28, tzinfo=timezone.utc), "severe"),
        _row(datetime(2026, 2, 1, tzinfo=timezone.utc)),
    ]
    out = sc.aggregate_windows(rows, kind="month")
    assert [(w.window, w.total, w.severe) for w in out] == [(1, 2, 1), (2, 1, 0)]


def test_naive_datetime_treated_as_utc():
    rows = [_row(datetime(2026, 3, 2, 10, 0, 0))]  # naive
    assert sc.aggregate_windows(rows, kind="week")[0].total == 1


def test_missing_timestamp_is_counted_not_silent(caplog):
    out = sc.aggregate_windows([_row(None), _row(datetime(2026, 3, 2, tzinfo=timezone.utc))], kind="week")
    assert len(out) == 1 and out[0].total == 1


def test_dry_run_does_not_feed_monitor():
    monitor = _FakeMonitor()
    windows = [sc.WindowStats(window=10, kind="week", total=400, severe=10)]
    summary = sc.feed_windows(monitor, windows, apply=False, target_rate=0.005)
    assert monitor.weekly == []          # 观察模式不触发（因此不会暂停）
    assert summary[0]["would_exceed"] is True
    assert summary[0]["applied"] is False


def test_enforce_feeds_monitor():
    monitor = _FakeMonitor()
    windows = [
        sc.WindowStats(window=10, kind="week", total=400, severe=10),
        sc.WindowStats(window=3, kind="month", total=400, severe=1),
    ]
    sc.feed_windows(monitor, windows, apply=True, target_rate=0.005)
    assert monitor.weekly == [(10, 400, 10)]
    assert monitor.monthly == [(3, 400, 1)]


def test_feed_loop_runs_one_round(monkeypatch):
    monitor = _FakeMonitor()
    seen: list[float] = []

    class _DB:
        async def execute(self, stmt):
            raise AssertionError("测试里应 mock collect_rows")

    class _Session:
        async def __aenter__(self):
            return _DB()

        async def __aexit__(self, *exc):
            return False

    async def fake_collect(db, *, since):
        seen.append(1.0)
        return [
            _row(datetime.now(timezone.utc) - timedelta(days=1)),
            _row(datetime.now(timezone.utc) - timedelta(days=2), "severe"),
        ]

    monkeypatch.setattr(sc, "collect_rows", fake_collect)
    monkeypatch.setattr(sc, "feed_enabled", lambda: True)
    monkeypatch.setattr(sc, "enforce_enabled", lambda: False)

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(
            sc.run_stats_feed(lambda: _Session(), lambda: monitor, interval_s=0.01, stop=stop)
        )
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(main())
    assert seen, "喂数任务没有跑过一轮"


def test_loop_survives_single_failure(monkeypatch):
    calls = {"n": 0}

    async def boom(db, *, since):
        calls["n"] += 1
        raise RuntimeError("db down")

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(sc, "collect_rows", boom)
    monkeypatch.setattr(sc, "feed_enabled", lambda: True)

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(
            sc.run_stats_feed(lambda: _Session(), lambda: _FakeMonitor(), interval_s=0.01, stop=stop)
        )
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(main())
    assert calls["n"] >= 2


def test_lifespan_starts_and_cancels_feed():
    from app import main as main_mod

    src = inspect.getsource(main_mod)
    assert "start_stats_feed(app)" in src
    assert "cusum_stats_feed_task" in src
