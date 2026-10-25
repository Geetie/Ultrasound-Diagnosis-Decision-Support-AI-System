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
            # `addr` 必须显式给：prometheus_client 不传时默认 `0.0.0.0`，
            # 共享机/边缘端上等于把 `/metrics` 暴露给整个网络
            # （2026-09-29 真机实测 `ss -ltn` 里是 `0.0.0.0:9090`）。
            start_http_server(p, addr=settings.obs_metrics_host, registry=self._registry)
            self._server_started = True
            _logger.info(
                "Metrics server started", port=p, addr=settings.obs_metrics_host
            )
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

    def set_retrieval_quality(self, metric: str, value: float) -> None:
        """写入**真的算出来**的检索质量指标（A.88 T4-⑤）。

        只应在有 ground truth 时调用；没有 ground truth 时**不许编数值**，
        改调 :meth:`record_retrieval_quality_not_measured`。
        """
        self.retrieval_quality.labels(metric=metric).set(value)

    def record_retrieval_quality_not_measured(self, metric: str, reason: str) -> None:
        """记录"这次没测到"（不是记 0）。

        为什么单独一个计数器：把"没测"写成 ``set(0.0)`` 会让看板把
        "召回率为 0"与"召回率从没测过"混成一件事（A.58/A.62 反复踩过的
        "幽灵指标"同类问题）。
        """
        self.retrieval_quality_measurements.labels(
            metric=metric, status=f"not_measured_{reason}"
        ).inc()

    def record_retrieval_quality_measured(self, metric: str) -> None:
        """记录一次成功测量（配合 :meth:`set_retrieval_quality` 使用）。"""
        self.retrieval_quality_measurements.labels(
            metric=metric, status="measured"
        ).inc()

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
        except Exception as exc:
            _logger.debug("gauge_value_read_failed", metric=metric_name, error=str(exc))
            return None

    def get_histogram_p99(self, metric_name: str) -> float | None:
        hist = getattr(self, metric_name, None)
        if hist is None or not isinstance(hist, Histogram):
            return None
        return self._calculate_histogram_p99(hist)

    @staticmethod
    def _calculate_histogram_p99(hist: Histogram) -> float | None:
        """按桶计数求 p99 上界。

        实测踩到的缺陷（已修）：旧实现写的是 ``list(hist._buckets.values())``，
        假设 ``_buckets`` 是 dict（老版本 prometheus_client 如此）。当前依赖版本是
        prometheus-client 0.25.0，``_buckets`` 已经是**列表**且与 ``_upper_bounds``
        一一对应（末位是 ``inf``），元素为 ``MutexValue`` 包装对象。于是旧代码抛
        ``AttributeError``，被 ``except Exception: pass`` 吞掉 ——
        **后果是任何直方图的 p99 恒为 None**，而看板的 4 个 p99 面板
        （nav/assess/diag/multi）一直显示为空，且没有任何错误痕迹。

        故这里做三件事：
        ① 按当前版本的真实结构取数（先 ``.get()`` 解包装）；
        ② **合并带标签直方图的各子指标**——`diagnosis_latency`、`multi_organ_latency`
           都是 `labels(...)` 出来的，父对象的 `_buckets` 是空的，桶计数在各子指标上。
           不合并的话看板的 `diag_p99` / `multi_p99` 两个面板无论怎么打点都是空——这是
           与上面同一族的"静默 None"，只是根因不同（实测一并抓到）；
        ③ 保留兜底但**改为留痕**——版本再变时要能在日志里看到，而不是又静默变成 None。
        """
        try:
            # _upper_bounds 末位是 +Inf，与 _buckets 一一对应；桶计数是"非累积"的
            upper_bounds = list(hist._upper_bounds)
            counts = _merge_histogram_bucket_counts(hist)
            if counts is None or len(counts) != len(upper_bounds):
                _logger.warning(
                    "histogram_p99_structure_unexpected",
                    metric=getattr(hist, "_name", "unknown"),
                    bucket_count=len(counts) if counts is not None else 0,
                    bound_count=len(upper_bounds),
                )
                return None

            total = sum(counts)
            if total == 0:
                return None

            threshold = total * 0.99
            cumulative = 0.0
            for index, count in enumerate(counts):
                cumulative += count
                if cumulative >= threshold:
                    bound = upper_bounds[index]
                    if bound == float("inf"):
                        # p99 落到 +Inf 桶：观测值超出最大有限桶上界，无法给出有限值。
                        # 返回 None 而不是"最大有限桶"——后者会被误读为真实 p99。
                        return None
                    return float(bound)
        except Exception as exc:
            _logger.warning(
                "histogram_p99_calculation_failed",
                metric=getattr(hist, "_name", "unknown"),
                error=str(exc),
                error_type=type(exc).__name__,
            )
        return None


def _merge_histogram_bucket_counts(hist: Histogram) -> list[float] | None:
    """取直方图的桶计数；带标签时把各子指标逐桶相加。

    - 无标签直方图：桶计数就在自身 ``_buckets`` 上；
    - 带标签直方图（``labels(...)``）：父对象 ``_buckets`` 为空，计数在各子指标上，
      必须合并——否则该指标的 p99 永远算不出来。
    子指标的桶数不一致（不该发生）时返回 None，由调用方留痕。
    """
    own_buckets = getattr(hist, "_buckets", None)
    if own_buckets:
        return [bucket.get() for bucket in own_buckets]

    children = list(getattr(hist, "_metrics", {}).values())
    if not children:
        return None

    merged: list[float] | None = None
    for child in children:
        child_counts = [bucket.get() for bucket in getattr(child, "_buckets", [])]
        if merged is None:
            merged = child_counts
        elif len(child_counts) == len(merged):
            # strict=True：长度上面已校验，显式声明意图，避免两列错位相加
            merged = [
                left + right for left, right in zip(merged, child_counts, strict=True)
            ]
        else:
            return None
    return merged
