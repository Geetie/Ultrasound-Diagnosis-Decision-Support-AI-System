import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db_session
from app.config.settings import settings
from app.observability.alert_escalation import get_alert_escalation_manager
from app.observability.cache_monitor import get_cache_monitor
from app.observability.cusum import AISafetyCircuitBreaker
from app.observability.metrics_collector import get_metrics_collector
from app.security.auth import PermissionChecker
from app.services.cold_archive import ColdArchiveService

logger = structlog.get_logger()

# P0-5（15 号计划）：/internal/obs/* 端点此前无认证——暴露指标仪表盘、
# 冷归档状态与**可触发归档运行**的 POST 端点。加角色认证：系统管理员 +
# 质控管理员（归档操作），只读仪表盘同样需登录。
#
# A.88 配置接线②：改为按**权限位**准入（``check_permission`` 的生产消费者）。
# 角色集合由 ``RoleChecker.for_permission("view_qc_report")`` 从 PERMISSIONS
# 推导 = ["QUALITY_CONTROL", "SYSTEM_ADMIN"]，与原硬编码白名单**逐字相同**
# （零行为漂移；403 文案与错误码 E1008 也不变）。
_require_admin = Depends(PermissionChecker("view_qc_report"))

router = APIRouter(
    tags=["observability-internal"],
    dependencies=[_require_admin],
)


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


@router.get("/cold-archive")
async def cold_archive_status(
    category: str | None = Query(default=None, description="audit_logs | session_archives"),
    limit: int = Query(default=50, ge=1, le=500),
    db: AsyncSession = Depends(get_db_session),
):
    """T-GB-SEC-02：冷归档清单与批次总览（热/冷分层的唯一可观测入口）。

    返回按 category+status 的批次统计，以及最近的清单（含 checksum 与
    记录数），运维据此确认"冷层是否真的存住了"。
    """
    service = ColdArchiveService(db)
    stats = await service.stats()
    manifests = await service.list_manifests(category=category, limit=limit)
    return {
        "adapter": service.storage.name,
        "hot_retention_days": settings.audit_hot_retention_days,
        "stats": stats,
        "manifests": [
            {
                "manifest_id": m.manifest_id,
                "category": m.category,
                "status": m.status,
                "object_key": m.object_key,
                "record_count": m.record_count,
                "size_bytes": m.size_bytes,
                "checksum": m.checksum,
                "adapter": m.adapter,
                "archived_at": m.archived_at.isoformat() if m.archived_at else None,
                "purged_at": m.purged_at.isoformat() if m.purged_at else None,
                "message": m.message,
            }
            for m in manifests
        ],
    }


@router.get("/restore-rate")
async def restore_rate_status():
    """T-G1-SEC-02：脱敏还原频率监控（阈值、告警数与最近一次告警）。"""
    from app.security.restore_rate_monitor import get_restore_rate_monitor

    return get_restore_rate_monitor().stats()


@router.post("/cold-archive/run")
async def run_cold_archive(
    category: str = Query(
        default="audit_logs", description="audit_logs | session_archives"
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """手动触发一次冷归档（运维用；等价于周期任务中的冷层步骤）。"""
    service = ColdArchiveService(db)
    if category == "session_archives":
        result = await service.archive_session_archives()
    else:
        result = await service.archive_audit_logs()
    return result.as_dict()


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
