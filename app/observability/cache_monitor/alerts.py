from typing import Any, Dict, List

from app.observability.constants import CacheAlertThresholds

ESCALATION_STEPS = {
    "cache_hit_rate": [
        "check_redis_availability",
        "check_bloom_filter_loaded",
        "check_hotkey_permanent_config",
        "trigger_manual_warmup",
    ],
    "db_penetration_rate": [
        "check_bloom_filter_loaded",
        "check_null_cache_ttl_config",
        "check_new_invalid_query_patterns",
    ],
    "simultaneous_expiry_5min": [
        "confirm_ttl_randomization_enabled",
        "check_batch_key_clear_events",
        "trigger_emergency_warmup",
    ],
    "mutex_lock_timeout": [
        "check_hotkey_load_latency",
        "consider_increase_mutex_timeout",
        "check_db_query_performance",
    ],
    "bloom_filter_interception_rate": [
        "rebuild_bloom_filter",
    ],
    "ttl_randomization_offset_percent": [
        "enable_ttl_randomization",
    ],
}


def get_escalation_steps(alert: Dict[str, Any]) -> List[str]:
    metric = alert.get("metric", "")
    return ESCALATION_STEPS.get(metric, [])


def check_alerts(
    overall_hit_rate: float,
    db_penetration_rate: float,
    simultaneous_expiry_5min: int,
    hourly_timeouts: int,
    bloom_rate: float,
    total_queries: int,
    null_hit_rate: float,
    lock_success_rate: float,
    total_lock_attempts: int,
    hotkey_coverage: float,
    hotkey_total: int,
    ttl_offset: float,
    total_keys: int,
    warmup_rate: float,
    warmup_duration: float,
    write_p99: float,
    read_p99: float,
) -> List[Dict[str, Any]]:
    alerts = []

    if overall_hit_rate < CacheAlertThresholds.HIT_RATE_CRITICAL:
        alerts.append({
            "level": "critical",
            "metric": "cache_hit_rate",
            "value": overall_hit_rate,
            "threshold": CacheAlertThresholds.HIT_RATE_CRITICAL,
            "action": "check_redis->check_bloom_filter->manual_warmup",
        })
    elif overall_hit_rate < CacheAlertThresholds.HIT_RATE_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "cache_hit_rate",
            "value": overall_hit_rate,
            "threshold": CacheAlertThresholds.HIT_RATE_WARNING,
        })

    if db_penetration_rate > CacheAlertThresholds.DB_PENETRATION_CRITICAL:
        alerts.append({
            "level": "critical",
            "metric": "db_penetration_rate",
            "value": db_penetration_rate,
            "threshold": CacheAlertThresholds.DB_PENETRATION_CRITICAL,
            "action": "check_bloom_filter->check_null_cache_ttl",
        })
    elif db_penetration_rate > CacheAlertThresholds.DB_PENETRATION_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "db_penetration_rate",
            "value": db_penetration_rate,
            "threshold": CacheAlertThresholds.DB_PENETRATION_WARNING,
        })

    if simultaneous_expiry_5min > CacheAlertThresholds.SIMULTANEOUS_EXPIRY_CRITICAL:
        alerts.append({
            "level": "critical",
            "metric": "simultaneous_expiry_5min",
            "value": simultaneous_expiry_5min,
            "threshold": CacheAlertThresholds.SIMULTANEOUS_EXPIRY_CRITICAL,
            "action": "confirm_ttl_randomization->emergency_warmup",
        })
    elif simultaneous_expiry_5min > CacheAlertThresholds.SIMULTANEOUS_EXPIRY_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "simultaneous_expiry_5min",
            "value": simultaneous_expiry_5min,
            "threshold": CacheAlertThresholds.SIMULTANEOUS_EXPIRY_WARNING,
        })

    if hourly_timeouts > CacheAlertThresholds.MUTEX_TIMEOUT_CRITICAL:
        alerts.append({
            "level": "critical",
            "metric": "mutex_lock_timeout",
            "value": hourly_timeouts,
            "threshold": CacheAlertThresholds.MUTEX_TIMEOUT_CRITICAL,
            "action": "check_hotkey_load_latency",
        })
    elif hourly_timeouts > CacheAlertThresholds.MUTEX_TIMEOUT_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "mutex_lock_timeout",
            "value": hourly_timeouts,
            "threshold": CacheAlertThresholds.MUTEX_TIMEOUT_WARNING,
        })

    if bloom_rate < CacheAlertThresholds.BLOOM_RATE_CRITICAL and total_queries > 0:
        alerts.append({
            "level": "critical",
            "metric": "bloom_filter_interception_rate",
            "value": bloom_rate,
            "threshold": CacheAlertThresholds.BLOOM_RATE_CRITICAL,
            "action": "rebuild_bloom_filter",
        })
    elif bloom_rate < CacheAlertThresholds.BLOOM_RATE_WARNING and total_queries > 0:
        alerts.append({
            "level": "warning",
            "metric": "bloom_filter_interception_rate",
            "value": bloom_rate,
            "threshold": CacheAlertThresholds.BLOOM_RATE_WARNING,
        })

    if null_hit_rate < CacheAlertThresholds.NULL_HIT_RATE_WARNING and total_queries > 0:
        alerts.append({
            "level": "warning",
            "metric": "null_value_hit_rate",
            "value": null_hit_rate,
            "threshold": CacheAlertThresholds.NULL_HIT_RATE_WARNING,
        })

    if lock_success_rate < CacheAlertThresholds.LOCK_SUCCESS_RATE_CRITICAL and total_lock_attempts > 0:
        alerts.append({
            "level": "critical",
            "metric": "mutex_lock_success_rate",
            "value": lock_success_rate,
            "threshold": CacheAlertThresholds.LOCK_SUCCESS_RATE_CRITICAL,
        })
    elif lock_success_rate < CacheAlertThresholds.LOCK_SUCCESS_RATE_WARNING and total_lock_attempts > 0:
        alerts.append({
            "level": "warning",
            "metric": "mutex_lock_success_rate",
            "value": lock_success_rate,
            "threshold": CacheAlertThresholds.LOCK_SUCCESS_RATE_WARNING,
        })

    if hotkey_coverage < CacheAlertThresholds.HOTKEY_COVERAGE_WARNING and hotkey_total > 0:
        alerts.append({
            "level": "warning",
            "metric": "hotkey_permanent_coverage",
            "value": hotkey_coverage,
            "threshold": CacheAlertThresholds.HOTKEY_COVERAGE_WARNING,
        })

    if ttl_offset == 0 and total_keys > 0:
        alerts.append({
            "level": "critical",
            "metric": "ttl_randomization_offset_percent",
            "value": 0.0,
            "threshold": 0.0,
            "action": "enable_ttl_randomization",
        })

    if warmup_rate < CacheAlertThresholds.WARMUP_RATE_CRITICAL and total_keys > 0:
        alerts.append({
            "level": "critical",
            "metric": "warmup_completion_rate",
            "value": warmup_rate,
            "threshold": CacheAlertThresholds.WARMUP_RATE_CRITICAL,
        })
    elif warmup_rate < CacheAlertThresholds.WARMUP_RATE_WARNING and total_keys > 0:
        alerts.append({
            "level": "warning",
            "metric": "warmup_completion_rate",
            "value": warmup_rate,
            "threshold": CacheAlertThresholds.WARMUP_RATE_WARNING,
        })

    if warmup_duration > CacheAlertThresholds.WARMUP_DURATION_CRITICAL:
        alerts.append({
            "level": "critical",
            "metric": "warmup_duration_seconds",
            "value": warmup_duration,
            "threshold": CacheAlertThresholds.WARMUP_DURATION_CRITICAL,
        })
    elif warmup_duration > CacheAlertThresholds.WARMUP_DURATION_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "warmup_duration_seconds",
            "value": warmup_duration,
            "threshold": CacheAlertThresholds.WARMUP_DURATION_WARNING,
        })

    if write_p99 > CacheAlertThresholds.WRITE_P99_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "write_latency_p99",
            "value": write_p99,
            "threshold": CacheAlertThresholds.WRITE_P99_WARNING,
        })

    if read_p99 > CacheAlertThresholds.READ_P99_WARNING:
        alerts.append({
            "level": "warning",
            "metric": "read_latency_p99",
            "value": read_p99,
            "threshold": CacheAlertThresholds.READ_P99_WARNING,
        })

    return alerts
