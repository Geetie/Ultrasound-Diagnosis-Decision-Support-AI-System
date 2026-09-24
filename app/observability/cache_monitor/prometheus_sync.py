from __future__ import annotations

import time
from typing import TYPE_CHECKING

import structlog

from app.observability.utils import calculate_percentile

if TYPE_CHECKING:
    from .monitor import CacheMonitor

_logger = structlog.get_logger("app.observability.cache_monitor")


def _sync_metrics_to_collector(monitor: CacheMonitor) -> None:
    from app.observability.metrics_collector import get_metrics_collector
    collector = get_metrics_collector()
    snapshot = monitor.get_metrics_snapshot()
    collector.set_cache_hit_rate(snapshot["overall_hit_rate"])
    collector.set_cache_bloom_filter_interception_rate(snapshot["bloom_filter_interception_rate"])
    collector.set_cache_null_value_hit_rate(snapshot["null_value_hit_rate"])
    collector.set_cache_db_penetration_rate(snapshot["db_penetration_rate"])
    collector.set_cache_mutex_lock_success_rate(snapshot["mutex_lock_success_rate"])
    collector.set_cache_mutex_lock_timeout_count(snapshot["mutex_lock_timeout_count"])
    collector.set_cache_hotkey_permanent_coverage(snapshot["hotkey_permanent_coverage"])
    collector.set_cache_ttl_randomization_offset_percent(snapshot["ttl_randomization_offset_percent"])
    collector.set_cache_simultaneous_expiry_5min(snapshot["simultaneous_expiry_5min"])
    collector.set_cache_warmup_completion_rate(snapshot["warmup_completion_rate"])
    collector.set_cache_warmup_duration_seconds(snapshot["warmup_duration_seconds"])
    collector.set_cache_write_latency_p99_seconds(snapshot["write_latency_p99_seconds"])
    collector.set_cache_read_latency_p99_seconds(snapshot["read_latency_p99_seconds"])


def update_prometheus(monitor: CacheMonitor) -> None:
    now = time.time()
    if not monitor._prometheus_dirty or (now - monitor._last_prometheus_sync) < monitor._prometheus_sync_interval:
        return
    monitor._prometheus_dirty = False
    monitor._last_prometheus_sync = now
    try:
        with monitor._lock:
            read_p99 = calculate_percentile(list(monitor._read_latencies), 99.0)
            write_p99 = calculate_percentile(list(monitor._write_latencies), 99.0)
            monitor._overall.read_latency_p99_seconds = read_p99
            monitor._overall.write_latency_p99_seconds = write_p99
        _sync_metrics_to_collector(monitor)
    except Exception:
        _logger.error("cache_prometheus_sync_failed", exc_info=True)


def sync_to_prometheus(monitor: CacheMonitor) -> None:
    now = time.time()
    monitor._prometheus_dirty = False
    monitor._last_prometheus_sync = now
    try:
        with monitor._lock:
            read_p99 = calculate_percentile(list(monitor._read_latencies), 99.0)
            write_p99 = calculate_percentile(list(monitor._write_latencies), 99.0)
            monitor._overall.read_latency_p99_seconds = read_p99
            monitor._overall.write_latency_p99_seconds = write_p99
        _sync_metrics_to_collector(monitor)
    except Exception:
        _logger.error("cache_prometheus_sync_failed", exc_info=True)
