import os
from typing import Any

import structlog
from sqlalchemy import text

from safety_gateway.circuit_breaker import circuit_breaker_manager
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.redis_pool import SafetyGatewayRedisPool
from safety_gateway.threshold import get_threshold_engine

logger = structlog.get_logger()


async def check_redis() -> dict[str, Any]:
    from datetime import datetime, timezone
    try:
        start = datetime.now(timezone.utc)
        is_healthy = await SafetyGatewayRedisPool.health_check()
        elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        return {
            "status": "healthy" if is_healthy else "unhealthy",
            "latency_ms": round(elapsed, 2),
        }
    except Exception as e:
        logger.error("sg_redis_health_check_failed", error=str(e))
        return {"status": "unhealthy", "error": str(e)}


async def check_database() -> dict[str, Any]:
    from datetime import datetime, timezone

    from safety_gateway.sg_db import _get_async_engine
    try:
        start = datetime.now(timezone.utc)
        engine = await _get_async_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        elapsed = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        return {
            "status": "healthy",
            "latency_ms": round(elapsed, 2),
        }
    except Exception as e:
        logger.error("sg_db_health_check_failed", error=str(e))
        return {"status": "unhealthy", "error": str(e)}


def check_rules_engine() -> dict[str, Any]:
    try:
        rules = sg_desensitization_engine.get_rules()
        enabled_count = sum(1 for r in rules if r.enabled)
        patch_loaded = getattr(sg_desensitization_engine, '_patch_loaded', True)
        if enabled_count == 0:
            status = "unhealthy"
        elif not patch_loaded:
            status = "degraded"
        else:
            status = "healthy"
        return {
            "status": status,
            "total_rules": len(rules),
            "enabled_rules": enabled_count,
            "patch_rules_loaded": patch_loaded,
        }
    except Exception as e:
        logger.error("sg_rules_health_check_failed", error=str(e))
        return {"status": "unhealthy", "error": str(e)}


def check_circuit_breaker() -> dict[str, Any]:
    try:
        all_stats = circuit_breaker_manager.get_all_stats()
        open_breakers = [
            name for name, stats in all_stats.items()
            if stats["state"] == "open"
        ]
        return {
            "status": "unhealthy" if open_breakers else "healthy",
            "total_breakers": len(all_stats),
            "open_breakers": open_breakers,
        }
    except Exception as e:
        logger.error("sg_circuit_breaker_health_check_failed", error=str(e))
        return {"status": "unhealthy", "error": str(e)}


def check_oom_protection() -> dict[str, Any]:
    try:
        with open(f"/proc/{os.getpid()}/oom_score_adj") as f:
            value = int(f.read().strip())
            return {
                "status": "protected" if value == -1000 else "warning",
                "oom_score_adj": value,
                "expected": -1000,
            }
    except Exception as exc:
        # /proc 不可读（容器/非 Linux）→ 回退读 docker-compose 期望值；回退要可查
        logger.debug("oom_score_adj_proc_unavailable", error=str(exc))
        configured_value = _read_oom_from_docker_compose()
        if configured_value is not None:
            return {
                "status": "configured" if configured_value == -1000 else "warning",
                "oom_score_adj": configured_value,
                "source": "docker-compose",
                "expected": -1000,
            }
        is_windows = os.name == "nt"
        return {
            "status": "unknown",
            "oom_score_adj": None,
            "expected": -1000,
            "configured_in_compose": -1000,
            "note": "OOM protection not verifiable on Windows — must be verified in Docker/Linux deployment" if is_windows else None,
        }


def check_threshold_engine() -> dict[str, Any]:
    try:
        engine = get_threshold_engine()
        stats = engine.get_stats()
        return {
            "status": "healthy" if stats.get("loaded") else "degraded",
            "loaded": stats.get("loaded", False),
            "threshold_count": stats.get("threshold_count", 0),
        }
    except Exception as e:
        logger.error("sg_threshold_health_check_failed", error=str(e))
        return {"status": "unhealthy", "error": str(e)}


def _read_oom_from_docker_compose() -> int | None:
    try:
        import yaml
        compose_paths = [
            os.path.join(os.getcwd(), "docker-compose.dev.yml"),
            os.path.join(os.getcwd(), "docker-compose.yml"),
        ]
        for compose_path in compose_paths:
            if not os.path.isfile(compose_path):
                continue
            with open(compose_path) as f:
                compose_data = yaml.safe_load(f)
            services = compose_data.get("services", {})
            sg_service = services.get("safety-gateway", {})
            oom_value = sg_service.get("oom_score_adj")
            if oom_value is not None:
                return int(oom_value)
    except Exception as exc:
        logger.debug("oom_score_adj_compose_unavailable", error=str(exc))
    return None
