import threading
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.observability.constants import CacheMonitorConstants
from app.observability.utils import calculate_percentile

from .alerts import check_alerts
from .models import (
    CacheAvalancheMetrics,
    CacheBreakdownMetrics,
    CacheOverallMetrics,
    CachePenetrationMetrics,
)
from .prometheus_sync import sync_to_prometheus, update_prometheus

_logger = structlog.get_logger("app.observability.cache_monitor")


class CacheMonitor:
    _instance: Optional["CacheMonitor"] = None
    _instance_lock = threading.Lock()

    def __init__(self):
        self._penetration = CachePenetrationMetrics()
        self._breakdown = CacheBreakdownMetrics()
        self._avalanche = CacheAvalancheMetrics()
        self._overall = CacheOverallMetrics()
        self._write_latencies: deque = deque(maxlen=CacheMonitorConstants.LATENCY_DEQUE_MAXLEN)
        self._read_latencies: deque = deque(maxlen=CacheMonitorConstants.LATENCY_DEQUE_MAXLEN)
        self._expiry_timestamps: deque = deque(maxlen=CacheMonitorConstants.EXPIRY_DEQUE_MAXLEN)
        self._lock_timeout_timestamps: deque = deque(maxlen=CacheMonitorConstants.EXPIRY_DEQUE_MAXLEN)
        self._alert_callbacks: List[Callable] = []
        self._alert_escalation_callbacks: List[Callable] = []
        self._lock = threading.Lock()
        self._prometheus_dirty = False
        self._last_prometheus_sync: float = 0.0
        self._prometheus_sync_interval: float = CacheMonitorConstants.PROMETHEUS_SYNC_INTERVAL

    @classmethod
    def get_instance(cls) -> "CacheMonitor":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def record_bloom_filter_query(self, intercepted: bool) -> None:
        with self._lock:
            self._penetration.total_queries += 1
            if intercepted:
                self._penetration.bloom_intercepted += 1
            self._penetration.bloom_filter_interception_rate = (
                self._penetration.bloom_intercepted / max(self._penetration.total_queries, 1)
            )
        self._mark_prometheus_dirty()

    def record_null_cache_result(self, hit: bool) -> None:
        with self._lock:
            if hit:
                self._penetration.null_cache_hits += 1
            self._penetration.null_value_hit_rate = (
                self._penetration.null_cache_hits / max(self._penetration.total_queries, 1)
            )
        self._mark_prometheus_dirty()

    def record_db_penetration(self) -> None:
        with self._lock:
            self._penetration.db_penetrations += 1
            self._penetration.db_penetration_rate = (
                self._penetration.db_penetrations / max(self._penetration.total_queries, 1)
            )
        self._mark_prometheus_dirty()

    def record_mutex_lock_attempt(self, success: bool) -> None:
        with self._lock:
            self._breakdown.total_lock_attempts += 1
            if success:
                self._breakdown.lock_successes += 1
            else:
                self._breakdown.lock_timeouts += 1
                self._lock_timeout_timestamps.append(time.time())
            self._breakdown.mutex_lock_success_rate = (
                self._breakdown.lock_successes / max(self._breakdown.total_lock_attempts, 1)
            )
            self._breakdown.mutex_lock_timeout_count = self._get_hourly_lock_timeouts()
        self._mark_prometheus_dirty()

    def _get_hourly_lock_timeouts(self) -> int:
        now = time.time()
        cutoff = now - 3600
        while self._lock_timeout_timestamps and self._lock_timeout_timestamps[0] < cutoff:
            self._lock_timeout_timestamps.popleft()
        return len(self._lock_timeout_timestamps)

    def set_hotkey_coverage(self, total: int, permanent: int) -> None:
        with self._lock:
            self._breakdown.hotkey_total = total
            self._breakdown.hotkey_permanent = permanent
            self._breakdown.hotkey_permanent_coverage = (
                permanent / max(total, 1)
            )
        self._mark_prometheus_dirty()

    def set_ttl_randomization(self, offset_percent: float) -> None:
        with self._lock:
            self._avalanche.ttl_randomization_offset_percent = offset_percent
        self._mark_prometheus_dirty()

    def record_key_expiry(self) -> None:
        with self._lock:
            now = time.time()
            self._expiry_timestamps.append(now)
            cutoff = now - 300
            while self._expiry_timestamps and self._expiry_timestamps[0] < cutoff:
                self._expiry_timestamps.popleft()
            self._avalanche.simultaneous_expiry_5min = len(self._expiry_timestamps)
        self._mark_prometheus_dirty()

    def record_warmup(self, total_keys: int, warmed_keys: int, duration_seconds: float) -> None:
        with self._lock:
            self._avalanche.total_keys = total_keys
            self._avalanche.warmed_keys = warmed_keys
            self._avalanche.warmup_completion_rate = warmed_keys / max(total_keys, 1)
            self._avalanche.warmup_duration_seconds = duration_seconds

        if warmed_keys == 0:
            _logger.error(
                "cache_warmup_failed",
                total_keys=total_keys,
                warmed_keys=warmed_keys,
                duration_seconds=duration_seconds,
            )
        elif warmed_keys < total_keys:
            _logger.warning(
                "cache_warmup_partial",
                total_keys=total_keys,
                warmed_keys=warmed_keys,
                duration_seconds=duration_seconds,
            )
        else:
            _logger.info(
                "cache_warmup_completed",
                warmed_keys=warmed_keys,
                duration_seconds=duration_seconds,
            )
        self._mark_prometheus_dirty()

    def record_cache_read(self, hit: bool, latency_seconds: float) -> None:
        with self._lock:
            self._overall.total_reads += 1
            if hit:
                self._overall.total_hits += 1
            self._overall.overall_hit_rate = (
                self._overall.total_hits / max(self._overall.total_reads, 1)
            )
            self._read_latencies.append(latency_seconds)
        self._mark_prometheus_dirty()

    def record_cache_write(self, latency_seconds: float) -> None:
        with self._lock:
            self._overall.total_writes += 1
            self._write_latencies.append(latency_seconds)
        self._mark_prometheus_dirty()

    def log_bloom_filter_loaded(self, loaded_keys: int, bloom_size: int, load_time_ms: float) -> None:
        _logger.info(
            "bloom_filter_loaded",
            loaded_keys=loaded_keys,
            bloom_size=bloom_size,
            load_time_ms=load_time_ms,
        )

    def log_null_cache_write(self, key_hash: str, ttl_seconds: int) -> None:
        _logger.debug(
            "null_cache_write",
            key_hash=key_hash,
            ttl_seconds=ttl_seconds,
        )

    def log_mutex_lock_failure(self, key: str, retry_count: int, wait_time_ms: float) -> None:
        _logger.warning(
            "mutex_lock_failed",
            key=key,
            retry_count=retry_count,
            wait_time_ms=wait_time_ms,
        )

    def log_ttl_randomization_check(self, randomization_percent: float, actual_offsets_sample: List[float]) -> None:
        _logger.info(
            "ttl_randomization_check",
            randomization_percent=randomization_percent,
            actual_offsets_sample=actual_offsets_sample[:5],
        )

    def register_alert_escalation_callback(self, callback: Callable) -> None:
        self._alert_escalation_callbacks.append(callback)

    def check_alerts_and_escalate(self) -> List[Dict[str, Any]]:
        alerts = self.check_alerts()
        for alert in alerts:
            for callback in self._alert_escalation_callbacks:
                try:
                    callback(alert)
                except Exception as e:
                    _logger.error("cache_alert_escalation_callback_failed", error=str(e))
        return alerts

    def check_alerts(self) -> List[Dict[str, Any]]:
        with self._lock:
            overall_hit_rate = self._overall.overall_hit_rate
            db_penetration_rate = self._penetration.db_penetration_rate
            simultaneous_expiry_5min = self._avalanche.simultaneous_expiry_5min
            hourly_timeouts = self._get_hourly_lock_timeouts()
            bloom_rate = self._penetration.bloom_filter_interception_rate
            total_queries = self._penetration.total_queries
            null_hit_rate = self._penetration.null_value_hit_rate
            lock_success_rate = self._breakdown.mutex_lock_success_rate
            total_lock_attempts = self._breakdown.total_lock_attempts
            hotkey_coverage = self._breakdown.hotkey_permanent_coverage
            hotkey_total = self._breakdown.hotkey_total
            ttl_offset = self._avalanche.ttl_randomization_offset_percent
            total_keys = self._avalanche.total_keys
            warmup_rate = self._avalanche.warmup_completion_rate
            warmup_duration = self._avalanche.warmup_duration_seconds
            write_p99 = calculate_percentile(list(self._write_latencies), 99.0)
            read_p99 = calculate_percentile(list(self._read_latencies), 99.0)

        return check_alerts(
            overall_hit_rate=overall_hit_rate,
            db_penetration_rate=db_penetration_rate,
            simultaneous_expiry_5min=simultaneous_expiry_5min,
            hourly_timeouts=hourly_timeouts,
            bloom_rate=bloom_rate,
            total_queries=total_queries,
            null_hit_rate=null_hit_rate,
            lock_success_rate=lock_success_rate,
            total_lock_attempts=total_lock_attempts,
            hotkey_coverage=hotkey_coverage,
            hotkey_total=hotkey_total,
            ttl_offset=ttl_offset,
            total_keys=total_keys,
            warmup_rate=warmup_rate,
            warmup_duration=warmup_duration,
            write_p99=write_p99,
            read_p99=read_p99,
        )

    def get_all_metrics(self) -> Dict[str, Any]:
        with self._lock:
            read_p99 = calculate_percentile(list(self._read_latencies), 99.0)
            write_p99 = calculate_percentile(list(self._write_latencies), 99.0)
        return {
            "penetration": {
                "bloom_filter_interception_rate": self._penetration.bloom_filter_interception_rate,
                "null_value_hit_rate": self._penetration.null_value_hit_rate,
                "db_penetration_rate": self._penetration.db_penetration_rate,
                "total_queries": self._penetration.total_queries,
            },
            "breakdown": {
                "mutex_lock_success_rate": self._breakdown.mutex_lock_success_rate,
                "mutex_lock_timeout_count": self._breakdown.mutex_lock_timeout_count,
                "hotkey_permanent_coverage": self._breakdown.hotkey_permanent_coverage,
            },
            "avalanche": {
                "ttl_randomization_offset_percent": self._avalanche.ttl_randomization_offset_percent,
                "simultaneous_expiry_5min": self._avalanche.simultaneous_expiry_5min,
                "warmup_completion_rate": self._avalanche.warmup_completion_rate,
                "warmup_duration_seconds": self._avalanche.warmup_duration_seconds,
            },
            "overall": {
                "overall_hit_rate": self._overall.overall_hit_rate,
                "write_latency_p99_seconds": write_p99,
                "read_latency_p99_seconds": read_p99,
            },
        }

    def get_metrics_snapshot(self) -> Dict[str, Any]:
        return {
            "bloom_filter_interception_rate": self._penetration.bloom_filter_interception_rate,
            "null_value_hit_rate": self._penetration.null_value_hit_rate,
            "db_penetration_rate": self._penetration.db_penetration_rate,
            "mutex_lock_success_rate": self._breakdown.mutex_lock_success_rate,
            "mutex_lock_timeout_count": self._breakdown.mutex_lock_timeout_count,
            "hotkey_permanent_coverage": self._breakdown.hotkey_permanent_coverage,
            "ttl_randomization_offset_percent": self._avalanche.ttl_randomization_offset_percent,
            "simultaneous_expiry_5min": self._avalanche.simultaneous_expiry_5min,
            "warmup_completion_rate": self._avalanche.warmup_completion_rate,
            "warmup_duration_seconds": self._avalanche.warmup_duration_seconds,
            "overall_hit_rate": self._overall.overall_hit_rate,
            "write_latency_p99_seconds": self._overall.write_latency_p99_seconds,
            "read_latency_p99_seconds": self._overall.read_latency_p99_seconds,
        }

    def _update_prometheus(self) -> None:
        update_prometheus(self)

    def _sync_to_prometheus(self) -> None:
        sync_to_prometheus(self)

    def _mark_prometheus_dirty(self) -> None:
        self._prometheus_dirty = True

    def start_prometheus_sync(self) -> None:
        sync_to_prometheus(self)

    def stop_prometheus_sync(self) -> None:
        self._prometheus_dirty = False

    @staticmethod
    def _calculate_p99(data: List[float]) -> float:
        from app.observability.utils import calculate_percentile
        return calculate_percentile(data, 99.0)


def get_cache_monitor() -> CacheMonitor:
    return CacheMonitor.get_instance()
