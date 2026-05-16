from dataclasses import dataclass
from enum import Enum


class CacheEventType(str, Enum):
    BLOOM_FILTER_LOADED = "bloom_filter_loaded"
    NULL_CACHE_WRITE = "null_cache_write"
    MUTEX_LOCK_FAILED = "mutex_lock_failed"
    WARMUP_COMPLETED = "warmup_completed"
    WARMUP_FAILED = "warmup_failed"
    TTL_RANDOMIZATION_CHECK = "ttl_randomization_check"


@dataclass
class CachePenetrationMetrics:
    bloom_filter_interception_rate: float = 0.0
    null_value_hit_rate: float = 0.0
    db_penetration_rate: float = 0.0
    total_queries: int = 0
    bloom_intercepted: int = 0
    null_cache_hits: int = 0
    db_penetrations: int = 0


@dataclass
class CacheBreakdownMetrics:
    mutex_lock_success_rate: float = 0.0
    mutex_lock_timeout_count: int = 0
    hotkey_permanent_coverage: float = 0.0
    total_lock_attempts: int = 0
    lock_successes: int = 0
    lock_timeouts: int = 0
    hotkey_total: int = 0
    hotkey_permanent: int = 0


@dataclass
class CacheAvalancheMetrics:
    ttl_randomization_offset_percent: float = 0.0
    simultaneous_expiry_5min: int = 0
    warmup_completion_rate: float = 0.0
    warmup_duration_seconds: float = 0.0
    total_keys: int = 0
    warmed_keys: int = 0


@dataclass
class CacheOverallMetrics:
    overall_hit_rate: float = 0.0
    write_latency_p99_seconds: float = 0.0
    read_latency_p99_seconds: float = 0.0
    total_reads: int = 0
    total_hits: int = 0
    total_writes: int = 0
