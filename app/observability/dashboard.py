from typing import Any

import structlog
from fastapi import APIRouter
from prometheus_client import Gauge

from app.observability.cache_monitor import get_cache_monitor
from app.observability.metrics_collector import get_metrics_collector

_logger = structlog.get_logger("app.observability.dashboard")

router = APIRouter()


def _get_labeled_gauge_values(metric_name: str) -> dict[str, float]:
    collector = get_metrics_collector()
    gauge = getattr(collector, metric_name, None)
    if gauge is None or not isinstance(gauge, Gauge):
        return {}
    try:
        result: dict[str, float] = {}
        for labels, child in gauge._metrics.items():
            key = labels[0] if labels else "unknown"
            result[key] = child._value.get()
        return result
    except Exception as exc:
        # 监控面板读不到指标时要可见（返回空字典会让运维以为「真的没有指标」）
        _logger.warning("metrics_gauge_read_failed", error=str(exc))
        return {}


@router.get("/summary")
async def get_summary() -> dict[str, Any]:
    collector = get_metrics_collector()
    cache_mon = get_cache_monitor()
    return {
        "gpu": _build_gpu_metrics(collector),
        "hallucination": _build_hallucination_metrics(collector),
        "latency": _build_latency_metrics(collector),
        "cache": _build_cache_metrics(collector, cache_mon),
        "infra": _build_infra_metrics(collector),
    }


@router.get("/gpu")
async def get_gpu() -> dict[str, Any]:
    collector = get_metrics_collector()
    return _build_gpu_metrics(collector)


@router.get("/hallucination")
async def get_hallucination() -> dict[str, Any]:
    collector = get_metrics_collector()
    return _build_hallucination_metrics(collector)


@router.get("/latency")
async def get_latency() -> dict[str, Any]:
    collector = get_metrics_collector()
    return _build_latency_metrics(collector)


@router.get("/cache")
async def get_cache() -> dict[str, Any]:
    collector = get_metrics_collector()
    cache_mon = get_cache_monitor()
    return _build_cache_metrics(collector, cache_mon)


@router.get("/infra")
async def get_infra() -> dict[str, Any]:
    collector = get_metrics_collector()
    return _build_infra_metrics(collector)


def _build_gpu_metrics(collector) -> dict[str, Any]:
    try:
        return {
            "memory_utilization": _get_labeled_gauge_values("gpu_memory_utilization"),
            "fragmentation_rate": _get_labeled_gauge_values("gpu_fragmentation_rate"),
            "temperature": _get_labeled_gauge_values("gpu_temperature"),
            "overheat_duration_seconds": _get_labeled_gauge_values("gpu_overheat_duration_seconds"),
            "fragmentation_duration_seconds": _get_labeled_gauge_values("gpu_fragmentation_duration_seconds"),
        }
    except Exception:
        _logger.warning("dashboard_gpu_metrics_read_failed", exc_info=True)
        return {}


def _build_hallucination_metrics(collector) -> dict[str, Any]:
    try:
        return {
            "severe_rate": collector.get_gauge_value("severe_hallucination_rate"),
            "overall_rate": collector.get_gauge_value("overall_hallucination_rate"),
            "high_risk_rate": collector.get_gauge_value("high_risk_hallucination_rate"),
            "retrieval_empty_rate": collector.get_gauge_value("retrieval_empty_rate"),
        }
    except Exception:
        _logger.warning("dashboard_hallucination_metrics_read_failed", exc_info=True)
        return {}


def _build_latency_metrics(collector) -> dict[str, Any]:
    try:
        return {
            "nav_p99_seconds": collector.get_histogram_p99("nav_latency"),
            "assessment_p99_seconds": collector.get_histogram_p99("assessment_latency"),
            "diagnosis_p99_seconds": collector.get_histogram_p99("diagnosis_latency"),
            "multi_organ_p99_seconds": collector.get_histogram_p99("multi_organ_latency"),
            "edge_nav_p99_seconds": collector.get_histogram_p99("edge_nav_latency_seconds"),
        }
    except Exception:
        _logger.warning("dashboard_latency_metrics_read_failed", exc_info=True)
        return {}


def _build_cache_metrics(collector, cache_mon) -> dict[str, Any]:
    try:
        cache_data = cache_mon.get_all_metrics()
        return {
            "hit_rate": collector.get_gauge_value("cache_hit_rate"),
            "bloom_filter_interception_rate": collector.get_gauge_value("cache_bloom_filter_interception_rate"),
            "null_value_hit_rate": collector.get_gauge_value("cache_null_value_hit_rate"),
            "db_penetration_rate": collector.get_gauge_value("cache_db_penetration_rate"),
            "mutex_lock_success_rate": collector.get_gauge_value("cache_mutex_lock_success_rate"),
            "mutex_lock_timeout_count": collector.get_gauge_value("cache_mutex_lock_timeout_count"),
            "hotkey_permanent_coverage": collector.get_gauge_value("cache_hotkey_permanent_coverage"),
            "simultaneous_expiry_5min": collector.get_gauge_value("cache_simultaneous_expiry_5min"),
            "warmup_completion_rate": collector.get_gauge_value("cache_warmup_completion_rate"),
            "write_latency_p99_seconds": collector.get_gauge_value("cache_write_latency_p99_seconds"),
            "read_latency_p99_seconds": collector.get_gauge_value("cache_read_latency_p99_seconds"),
            "penetration": cache_data.get("penetration", {}),
            "breakdown": cache_data.get("breakdown", {}),
            "avalanche": cache_data.get("avalanche", {}),
            "overall": cache_data.get("overall", {}),
        }
    except Exception:
        _logger.warning("dashboard_cache_metrics_read_failed", exc_info=True)
        return {}


def _build_infra_metrics(collector) -> dict[str, Any]:
    try:
        return {
            "service_health_rate": collector.get_gauge_value("service_health_rate"),
            "safety_gateway_healthy": collector.get_gauge_value("safety_gateway_health"),
            "redis_alive": collector.get_gauge_value("redis_process_alive"),
            "pg_connection_pool_utilization": collector.get_gauge_value("pg_connection_pool_utilization"),
            "victoriametrics_rss_bytes": collector.get_gauge_value("victoriametrics_process_resident_memory_bytes"),
        }
    except Exception:
        _logger.warning("dashboard_infra_metrics_read_failed", exc_info=True)
        return {}
