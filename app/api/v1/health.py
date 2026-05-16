import asyncio

from fastapi import APIRouter, HTTPException
from app.config.redis import RedisPool
from app.config.database import async_engine
from app.security.hsm import hsm_manager
from sqlalchemy import text

router = APIRouter(tags=["health"])


async def _check_redis():
    try:
        healthy = await RedisPool.health_check()
        return healthy, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)}"


async def _check_db():
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, ""
    except Exception as e:
        return False, f"{type(e).__name__}: {str(e)}"


@router.get("/health")
async def health_check():
    redis_result, db_result, hsm_status = await asyncio.gather(
        _check_redis(),
        _check_db(),
        get_hsm_health(),
    )
    redis_healthy, redis_error = redis_result
    db_healthy, db_error = db_result

    all_healthy = redis_healthy and db_healthy and hsm_status["status"] == "healthy"

    services = {
        "redis": "healthy" if redis_healthy else "unhealthy",
        "database": "healthy" if db_healthy else "unhealthy",
        "hsm": hsm_status
    }

    if all_healthy:
        return {"status": "healthy", "services": services}
    else:
        details = {"status": "unhealthy", "services": services}
        if redis_error:
            details["redis_error"] = redis_error
        if db_error:
            details["db_error"] = db_error
        raise HTTPException(status_code=503, detail=details)


@router.get("/health/hsm")
async def hsm_health_check():
    return await get_hsm_health()


async def get_hsm_health():
    try:
        is_degraded = hsm_manager.is_degraded()
        degraded_phase = hsm_manager.get_degraded_phase().value
        hsm_mode = hsm_manager.get_hsm_mode()
        hsm_available = hsm_manager.is_hsm_available()

        status = "healthy" if not is_degraded and hsm_available else "degraded" if is_degraded else "unavailable"

        result = {
            "status": status,
            "mode": hsm_mode,
            "available": hsm_available,
            "is_degraded": is_degraded,
            "degraded_phase": degraded_phase
        }

        degradation_info = hsm_manager.get_degradation_info()
        result.update(degradation_info)

        key_count = len(hsm_manager.list_keys())
        result["key_count"] = key_count

        return result
    except Exception as e:
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {str(e)}"
        }
