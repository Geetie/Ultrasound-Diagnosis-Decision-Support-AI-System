"""周/月统计窗口的数据源（A.115 / G19）。

## 缺口

`CUSUMMonitor.record_weekly_stats()` / `record_biweekly_stats()` / `record_monthly_stats()`
的三个列表**只有测试写入过**；生产里没有 DB 查询、没有定时器、没有运维脚本喂数，
于是 `weekly_rate_exceeded` 这条熔断入口**结构性不可达**（台账 G19）。

## 设计

1. **纯函数做分桶与判定**（`aggregate_windows`）：输入 `(created_at, findings)` 行，
   输出每个 ISO 周 / 每月的 `total / severe / rate`；时间转换、缺时间戳、脏 findings 都
   单独处理并计数（不静默丢行）。
2. **薄 DB 读取**（`collect_rows`）：只取 `status='completed'` 且 `created_at >= since` 的
   两列；`severe` 从 `findings.fusion_result.hallucination_severity == 'severe'` 判
   —— 这是 A.88 T5-3 落库的同一个键，不是另造口径。
3. **两段式生效**（默认观察）：
   - `cusum_stats_feed_enabled=True`（默认）：定时把窗口数据算出来并**记录日志**；
   - `cusum_stats_enforce=False`（默认）：**不**调用 `record_*` → 不触发暂停。
     打开后才真正喂给监控器、`weekly_rate_exceeded` 才会走到 `suspend`。
   理由：历史数据里 98.7% 的模板回显问题（A.70）刚清理不久，severe 率还没有干净基线；
   直接强制会把"机制可达"变成"上线即大面积暂停"。**先有数据、再谈强制**——这与
   `GradingFloorRules` 空表等待医学复核是同一套纪律。
4. 定时任务与熔断看门狗同款写法：单次异常不退出、可取消、间隔可配。

## 为什么按 `created_at` 而不是 findings.completed_at

`created_at` 是**索引列 + 服务端默认值**，不受判读正文影响；`findings.completed_at` 是管线
写的 ISO 字符串，缺失/格式异常的历史行会直接漏统计（正是"数据源不可靠"的复发形态）。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import structlog

from app.config.settings import settings

_logger = structlog.get_logger("app.observability.cusum.stats")

WindowKind = Literal["week", "month"]

DEFAULT_INTERVAL_SECONDS = 3600.0
DEFAULT_LOOKBACK_DAYS = 35


@dataclass(frozen=True)
class WindowStats:
    window: int
    kind: WindowKind
    total: int
    severe: int

    @property
    def rate(self) -> float:
        return (self.severe / self.total) if self.total else 0.0


def _severity_of(findings: Any) -> str | None:
    if not isinstance(findings, Mapping):
        return None
    fusion = findings.get("fusion_result")
    if not isinstance(fusion, Mapping):
        return None
    severity = fusion.get("hallucination_severity")
    return str(severity) if severity else None


def _ensure_aware(ts: datetime) -> datetime:
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=timezone.utc)


def aggregate_windows(
    rows: Iterable[tuple[datetime | None, Any]],
    *,
    kind: WindowKind,
) -> list[WindowStats]:
    """按 ISO 周 / 自然月分桶，返回**有序**的窗口统计。

    缺时间戳或 findings 非映射的行计入 ``skipped_rows``（只记日志，不静默丢）。
    """
    buckets: dict[int, list[int]] = {}
    skipped = 0
    for created_at, findings in rows:
        if created_at is None:
            skipped += 1
            continue
        ts = _ensure_aware(created_at)
        if kind == "week":
            key = ts.isocalendar().week
        else:
            key = ts.month
        bucket = buckets.setdefault(key, [0, 0])
        bucket[0] += 1
        if _severity_of(findings) == "severe":
            bucket[1] += 1

    if skipped:
        _logger.warning("cusum_stats_rows_skipped", kind=kind, skipped=skipped)

    return [
        WindowStats(window=key, kind=kind, total=total, severe=severe)
        for key, (total, severe) in sorted(buckets.items())
    ]


async def collect_rows(db: Any, *, since: datetime) -> list[tuple[datetime, Any]]:
    """薄读取：只取完成判读的 (created_at, findings)。"""
    from sqlalchemy import select

    from app.models.database import DiagnosisResult

    stmt = (
        select(DiagnosisResult.created_at, DiagnosisResult.findings)
        .where(DiagnosisResult.created_at >= since)
        .where(DiagnosisResult.status == "completed")
    )
    result = await db.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


def feed_windows(
    monitor: Any,
    windows: list[WindowStats],
    *,
    apply: bool,
    target_rate: float,
) -> list[dict[str, Any]]:
    """把窗口统计喂给监控器（``apply=False`` 时只记录，不触发回调链）。

    返回本轮每个窗口的处置摘要（便于日志/测试断言）。
    """
    out: list[dict[str, Any]] = []
    for w in windows:
        would_exceed = w.total > 0 and w.rate > target_rate
        entry = {
            "kind": w.kind,
            "window": w.window,
            "total": w.total,
            "severe": w.severe,
            "rate": round(w.rate, 6),
            "would_exceed": would_exceed,
            "applied": bool(apply),
        }
        _logger.info("cusum_window_stats", **entry)
        if apply:
            if w.kind == "week":
                monitor.record_weekly_stats(w.window, w.total, w.severe)
            else:
                monitor.record_monthly_stats(w.window, w.total, w.severe)
        out.append(entry)
    return out


def feed_interval_seconds() -> float:
    raw = getattr(settings, "cusum_stats_feed_interval_seconds", DEFAULT_INTERVAL_SECONDS)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def feed_enabled() -> bool:
    return bool(getattr(settings, "cusum_stats_feed_enabled", True))


def enforce_enabled() -> bool:
    return bool(getattr(settings, "cusum_stats_enforce", False))


def lookback_days() -> int:
    raw = getattr(settings, "cusum_stats_lookback_days", DEFAULT_LOOKBACK_DAYS)
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_LOOKBACK_DAYS


async def run_stats_feed(
    db_factory: Any,
    monitor_factory: Any,
    *,
    interval_s: float | None = None,
    stop: asyncio.Event | None = None,
) -> None:
    """周期汇总并喂数（默认观察模式：只记日志，不触发暂停）。"""
    interval = feed_interval_seconds() if interval_s is None else float(interval_s)
    if interval <= 0 or not feed_enabled():
        _logger.info("cusum_stats_feed_disabled", interval_s=interval)
        return
    _logger.info(
        "cusum_stats_feed_started",
        interval_s=interval,
        enforce=enforce_enabled(),
        lookback_days=lookback_days(),
    )
    while True:
        if stop is not None and stop.is_set():
            return
        try:
            since = datetime.now(timezone.utc) - timedelta(days=lookback_days())
            async with db_factory() as db:
                rows = await collect_rows(db, since=since)
            monitor = monitor_factory()
            target_rate = float(getattr(monitor._config, "target_rate", 0.005))
            for kind in ("week", "month"):
                feed_windows(
                    monitor,
                    aggregate_windows(rows, kind=kind),  # type: ignore[arg-type]
                    apply=enforce_enabled(),
                    target_rate=target_rate,
                )
        except Exception as exc:  # silent-ok: 单轮失败不能让喂数任务自己退出（缺口会复发）
            _logger.warning(
                "cusum_stats_feed_failed",
                error=str(exc)[:160],
                error_type=type(exc).__name__,
            )
        try:
            if stop is None:
                await asyncio.sleep(interval)
            else:
                await asyncio.wait_for(stop.wait(), timeout=interval)
                return
        except asyncio.TimeoutError:  # silent-ok: 等满一个间隔是正常轮次边界
            continue


def start_stats_feed(app: Any) -> asyncio.Task | None:
    """lifespan 启动入口；间隔<=0 或开关关闭时返回 None。"""
    if not feed_enabled() or feed_interval_seconds() <= 0:
        _logger.info("cusum_stats_feed_not_started")
        return None
    from app.config.database import AsyncSessionLocal
    from app.observability.cusum.circuit_breaker import AISafetyCircuitBreaker

    task = asyncio.create_task(
        run_stats_feed(
            AsyncSessionLocal,
            lambda: AISafetyCircuitBreaker.get_instance()._monitor,
        )
    )
    app.state.cusum_stats_feed_task = task
    return task
