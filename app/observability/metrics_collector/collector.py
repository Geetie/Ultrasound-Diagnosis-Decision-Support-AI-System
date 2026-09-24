import asyncio
import threading
from typing import Optional

import structlog
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

from app.config.settings import settings
from app.observability.enums import DeploymentProfile

from .definitions import MetricsDefinitions

_logger = structlog.get_logger("app.observability.metrics_collector")


class MetricsCollector(MetricsDefinitions):
    _instance: Optional["MetricsCollector"] = None
    _lock = threading.Lock()

    def __init__(self, profile: DeploymentProfile | None = None):
        self._profile = profile or DeploymentProfile(settings.deployment_profile)
        self._registry = CollectorRegistry()
        self._server_started = False
        self._pusher_task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._init_metrics()

    @classmethod
    def get_instance(cls, profile: DeploymentProfile | None = None) -> "MetricsCollector":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(profile)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None

    def _init_metrics(self) -> None:
        self._init_latency_metrics()
        self._init_hallucination_metrics()
        self._init_gpu_metrics()
        self._init_model_metrics()
        self._init_cache_metrics()
        self._init_rag_metrics()
        self._init_safety_metrics()
        self._init_diagnosis_metrics()
        self._init_infrastructure_metrics()

    def start_metrics_server(self, port: int | None = None) -> None:
        if self._server_started:
            return
        p = port or settings.obs_metrics_port
        if not settings.obs_metrics_enabled:
            _logger.info("Metrics server disabled by config")
            return
        try:
            start_http_server(p, registry=self._registry)
            self._server_started = True
            _logger.info("Metrics server started", port=p)
        except OSError as e:
            _logger.error("Failed to start metrics server", port=p, error=str(e))

    def get_metrics_text(self) -> str:
        return generate_latest(self._registry).decode("utf-8")

    def get_content_type(self) -> str:
        return CONTENT_TYPE_LATEST

    def record_nav_latency(self, seconds: float) -> None:
        self.nav_latency.observe(seconds)

    def record_assessment_latency(self, seconds: float) -> None:
        self.assessment_latency.observe(seconds)

    def record_diagnosis_latency(self, seconds: float, with_retry: bool = False) -> None:
        self.diagnosis_latency.labels(
            with_retry=str(with_retry).lower()
        ).observe(seconds)

    def record_multi_organ_latency(
        self, seconds: float, with_retry: bool = False
    ) -> None:
        self.multi_organ_latency.labels(
            profile=self._profile.value,
            with_retry=str(with_retry).lower(),
        ).observe(seconds)

    def record_consent_withdrawal_latency(
        self, seconds: float, target_seconds: float
    ) -> None:
        """T-G1-INT-06：记录撤回端到端时延，并单独计数越线次数。

        越线单独计数而不是只看直方图：验收是"撤回后 <500ms 生效"，直方图能看
        分布，但"有没有越线、越了几次"是告警要用的量，必须能直接读。
        """
        self.consent_withdrawal_latency.observe(max(0.0, seconds))
        if seconds > target_seconds:
            self.consent_withdrawal_target_exceeded.inc()

    def set_severe_hallucination_rate(self, rate: float) -> None:
        self.severe_hallucination_rate.set(rate)

    def set_overall_hallucination_rate(self, rate: float) -> None:
        self.overall_hallucination_rate.set(rate)

    def set_high_risk_hallucination_rate(self, rate: float) -> None:
        self.high_risk_hallucination_rate.set(rate)

    def set_retrieval_empty_rate(self, rate: float) -> None:
        self.retrieval_empty_rate.set(rate)

    def set_service_health_rate(self, rate: float) -> None:
        self.service_health_rate.set(rate)

    def set_gpu_memory_utilization(self, device: str, value: float) -> None:
        self.gpu_memory_utilization.labels(device=device).set(value)

    def set_gpu_fragmentation_rate(self, device: str, value: float) -> None:
        self.gpu_fragmentation_rate.labels(device=device).set(value)

    def set_gpu_fragmentation_duration(self, device: str, seconds: float) -> None:
        self.gpu_fragmentation_duration_seconds.labels(device=device).set(seconds)

    def set_gpu_temperature(self, device: str, celsius: float) -> None:
        self.gpu_temperature.labels(device=device).set(celsius)

    def set_model_hot_reload_success_rate(self, rate: float) -> None:
        self.model_hot_reload_success_rate.set(rate)

    def record_model_hot_reload_duration(self, seconds: float) -> None:
        self.model_hot_reload_duration.observe(seconds)

    def set_cache_hit_rate(self, rate: float) -> None:
        self.cache_hit_rate.set(rate)

    def set_cache_bloom_filter_interception_rate(self, rate: float) -> None:
        self.cache_bloom_filter_interception_rate.set(rate)

    def set_cache_null_value_hit_rate(self, rate: float) -> None:
        self.cache_null_value_hit_rate.set(rate)

    def set_cache_db_penetration_rate(self, rate: float) -> None:
        self.cache_db_penetration_rate.set(rate)

    def set_cache_mutex_lock_success_rate(self, rate: float) -> None:
        self.cache_mutex_lock_success_rate.set(rate)

    def set_cache_mutex_lock_timeout_count(self, count: int) -> None:
        self.cache_mutex_lock_timeout_count.set(count)

    def set_cache_hotkey_permanent_coverage(self, coverage: float) -> None:
        self.cache_hotkey_permanent_coverage.set(coverage)

    def set_cache_ttl_randomization_offset_percent(self, percent: float) -> None:
        self.cache_ttl_randomization_offset_percent.set(percent)

    def set_cache_simultaneous_expiry_5min(self, count: int) -> None:
        self.cache_simultaneous_expiry_5min.set(count)

    def set_cache_warmup_completion_rate(self, rate: float) -> None:
        self.cache_warmup_completion_rate.set(rate)

    def set_cache_warmup_duration_seconds(self, seconds: float) -> None:
        self.cache_warmup_duration_seconds.set(seconds)

    def set_cache_write_latency_p99_seconds(self, seconds: float) -> None:
        self.cache_write_latency_p99_seconds.set(seconds)

    def set_cache_read_latency_p99_seconds(self, seconds: float) -> None:
        self.cache_read_latency_p99_seconds.set(seconds)

    def set_crag_supplement_rate(self, rate: float) -> None:
        self.crag_supplement_rate.set(rate)

    def set_self_rag_retry_rate(self, rate: float) -> None:
        self.self_rag_retry_rate.set(rate)

    def inc_adaptive_rag_route(self, route: str) -> None:
        self.adaptive_rag_route.labels(route=route).inc()

    def record_consent_query_latency(self, seconds: float) -> None:
        self.consent_query_latency.observe(seconds)

    def set_safety_gateway_health(self, healthy: bool) -> None:
        self.safety_gateway_health.set(1.0 if healthy else 0.0)

    def set_desensitization_sync_delay(self, seconds: float) -> None:
        self.desensitization_sync_delay.set(seconds)

    def set_circuit_breaker_state(self, service: str, is_open: bool) -> None:
        self.circuit_breaker_state.labels(service=service).set(1.0 if is_open else 0.0)

    def set_bm25_fallback_duration(self, seconds: float) -> None:
        self.bm25_fallback_duration.set(seconds)

    def set_dual_write_queue_backlog(self, count: int) -> None:
        self.dual_write_queue_backlog.set(count)

    def inc_diagnosis_requests(self, organ: str, tier: str) -> None:
        self.diagnosis_requests.labels(organ=organ, tier=tier).inc()

    def inc_diagnosis_errors(self, error_code: str) -> None:
        self.diagnosis_errors.labels(error_code=error_code).inc()

    def set_redis_process_alive(self, alive: bool) -> None:
        self.redis_process_alive.set(1.0 if alive else 0.0)

    def inc_redis_container_restarts(self) -> None:
        self.redis_container_restarts.inc()

    def inc_audit_log_degraded_writes(self) -> None:
        self.audit_log_degraded_writes.inc()

    def set_pg_connection_pool_utilization(self, ratio: float) -> None:
        self.pg_connection_pool_utilization.set(ratio)

    def inc_pg_slow_queries(self) -> None:
        self.pg_slow_queries.inc()

    def set_gpu_overheat_duration(self, device: str, seconds: float) -> None:
        self.gpu_overheat_duration_seconds.labels(device=device).set(seconds)

    def record_edge_nav_latency(self, seconds: float) -> None:
        self.edge_nav_latency_seconds.observe(seconds)

    def set_edge_gpu_temperature(self, celsius: float) -> None:
        self.edge_gpu_temperature_celsius.set(celsius)

    def set_disk_usage_ratio(self, mount_point: str, ratio: float) -> None:
        self.disk_usage_ratio.labels(mount_point=mount_point).set(ratio)

    def set_edge_gpu_overheat_duration(self, seconds: float) -> None:
        self.edge_gpu_overheat_duration_seconds.set(seconds)

    def set_victoriametrics_rss_bytes(self, bytes_value: float) -> None:
        self.victoriametrics_process_resident_memory_bytes.set(bytes_value)

    def record_consent_cache_warmup_latency(self, seconds: float) -> None:
        self.consent_cache_warmup_latency_seconds.observe(seconds)

    def get_gauge_value(self, metric_name: str) -> float | None:
        gauge = getattr(self, metric_name, None)
        if gauge is None or not isinstance(gauge, Gauge):
            return None
        try:
            return gauge._value.get()
        except Exception:
            return None

    def get_histogram_p99(self, metric_name: str) -> float | None:
        hist = getattr(self, metric_name, None)
        if hist is None or not isinstance(hist, Histogram):
            return None
        return self._calculate_histogram_p99(hist)

    @staticmethod
    def _calculate_histogram_p99(hist: Histogram) -> float | None:
        try:
            buckets = list(hist._buckets.values())
            if not buckets:
                return None
            total = sum(buckets)
            if total == 0:
                return None
            threshold = total * 0.99
            cumulative = 0
            for i, count in enumerate(buckets):
                cumulative += count
                if cumulative >= threshold:
                    upper_bounds = list(hist._upper_bounds)
                    if i < len(upper_bounds):
                        return upper_bounds[i]
        except Exception:
            pass
        return None
