import structlog
from fastapi import APIRouter
from app.observability.metrics_collector import get_metrics_collector
from app.observability.cusum import AISafetyCircuitBreaker
from app.observability.alert_escalation import get_alert_escalation_manager
from app.observability.cache_monitor import get_cache_monitor
from app.config.settings import settings

logger = structlog.get_logger()

router = APIRouter(tags=["observability-internal"])


@router.get("/dashboard")
async def dashboard_data():
    collector = get_metrics_collector()
    breaker = AISafetyCircuitBreaker.get_instance()
    alert_mgr = get_alert_escalation_manager()
    cache_mon = get_cache_monitor()

    gpu = _read_gpu_metrics(collector)
    hallucination = _read_hallucination_metrics(collector)
    latency = _read_latency_metrics(collector)
    service = _read_service_metrics(collector)
    cache = _read_cache_metrics(cache_mon)

    breaker_info = breaker.suspension_info
    breaker_data = {
        "state": breaker_info.get("state", "unknown"),
        "cusum_s_plus": None,
        "active_alerts": len(alert_mgr.get_active_alerts()),
    }

    return {
        "gpu": gpu,
        "hallucination": hallucination,
        "latency": latency,
        "service": service,
        "cache": cache,
        "breaker": breaker_data,
    }


def _read_gpu_metrics(collector) -> dict:
    try:
        return {
            "memory_utilization": collector.get_gauge_value("gpu_memory_utilization"),
            "fragmentation_rate": collector.get_gauge_value("gpu_fragmentation_rate"),
            "temperature": collector.get_gauge_value("gpu_temperature"),
        }
    except Exception:
        logger.warning("obs_dashboard_gpu_metrics_read_failed", exc_info=True)
        return {}


def _read_hallucination_metrics(collector) -> dict:
    try:
        return {
            "severe_rate": collector.get_gauge_value("severe_hallucination_rate"),
            "overall_rate": collector.get_gauge_value("overall_hallucination_rate"),
            "high_risk_rate": collector.get_gauge_value("high_risk_hallucination_rate"),
        }
    except Exception:
        logger.warning("obs_dashboard_hallucination_metrics_read_failed", exc_info=True)
        return {}


def _read_latency_metrics(collector) -> dict:
    try:
        return {
            "nav_p99": collector.get_histogram_p99("nav_latency"),
            "assess_p99": collector.get_histogram_p99("assessment_latency"),
            "diag_p99": collector.get_histogram_p99("diagnosis_latency"),
            "multi_p99": collector.get_histogram_p99("multi_organ_latency"),
        }
    except Exception:
        logger.warning("obs_dashboard_latency_metrics_read_failed", exc_info=True)
        return {}


def _read_service_metrics(collector) -> dict:
    try:
        return {
            "health_rate": collector.get_gauge_value("service_health_rate"),
            "sg_healthy": collector.get_gauge_value("safety_gateway_health") == 1,
            "cache_hit_rate": collector.get_gauge_value("cache_hit_rate"),
            "retrieval_empty_rate": collector.get_gauge_value("retrieval_empty_rate"),
        }
    except Exception:
        logger.warning("obs_dashboard_service_metrics_read_failed", exc_info=True)
        return {}


def _read_cache_metrics(cache_mon) -> dict:
    try:
        metrics = cache_mon.get_all_metrics()
        return {
            "bloom_interception_rate": metrics.get("penetration", {}).get("bloom_filter_interception_rate"),
            "db_penetration_rate": metrics.get("penetration", {}).get("db_penetration_rate"),
            "mutex_success_rate": metrics.get("breakdown", {}).get("mutex_lock_success_rate"),
            "simultaneous_expiry_5min": metrics.get("avalanche", {}).get("simultaneous_expiry_5min"),
        }
    except Exception:
        logger.warning("obs_dashboard_cache_metrics_read_failed", exc_info=True)
        return {}
