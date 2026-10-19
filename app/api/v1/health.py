import asyncio

from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.config.database import async_engine
from app.config.redis import RedisPool
from app.security.hsm import hsm_manager

router = APIRouter(tags=["health"])


async def _check_redis():
    try:
        healthy = await RedisPool.health_check()
        return healthy, ""
    except Exception as e:  # silent-ok: 可读降级（返回 False + 原因，端点据此给 503 与说明）
        return False, f"{type(e).__name__}: {e!s}"


def _refresh_store_health() -> dict:
    """刷新令牌存储的部署适配性（并发审查 P0 最后一项，2026-10-04）。

    MemoryRefreshTokenStore 把 token 存在**进程内字典**，多 worker 各持一份。
    后果不是"竞态"而是**跨 worker 直接不可用**——worker A 签发的 refresh token
    到 worker B 上会报 Refresh token not found。当前单 worker 部署无影响。

    这里如实报告后端与进程局部性，让误开 --workers > 1 的配置立刻暴露，
    而不是变成"刷新偶发失败"这种极难定位的现象。
    """
    import os

    from app.security.refresh_token import RedisRefreshTokenStore, refresh_token_store

    store = refresh_token_store
    # 进程局部 = 不是 Redis 后端（内存字典多 worker 各持一份，跨 worker 不可用）
    process_local = not isinstance(store, RedisRefreshTokenStore)
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:  # silent-ok: 解析 WEB_CONCURRENCY 失败即按单进程（保守，不影响健康判定）
        workers = 1
    return {
        "backend": type(store).__name__,
        "process_local": process_local,
        "workers": workers,
        "multi_worker_safe": not (process_local and workers > 1),
        "warning": (
            "进程内存储 + 多 worker：刷新令牌跨 worker 不可用（需先改为 Redis 后端）"
            if process_local and workers > 1
            else None
        ),
    }


async def _check_db():
    try:
        async with async_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True, ""
    except Exception as e:  # silent-ok: 可读降级（同上，数据库不可用由健康端点显式暴露）
        return False, f"{type(e).__name__}: {e!s}"


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

    from app.rag.cache import get_retrieval_cache

    cache = get_retrieval_cache()

    # A.68 / R1：图像检索腿的**能力状态**必须可见（不参与 all_healthy —— 它关着不影响正确性，
    # 但"开着却空转"必须能被一眼看出来：集合不存在 / 维度不匹配 / 开关关闭）。
    from app.rag.image_leg import probe_image_leg

    image_leg = await probe_image_leg()

    services = {
        "redis": "healthy" if redis_healthy else "unhealthy",
        "database": "healthy" if db_healthy else "unhealthy",
        "hsm": hsm_status,
        "image_leg": image_leg,
        # 仅作信息展示，不参与 all_healthy：缓存不可用时检索仍正确，只是变慢
        "retrieval_cache": (
            cache.health() if cache is not None else {"status": "disabled"}
        ),
        # 刷新令牌存储（并发审查 P0 的最后一项，2026-10-04）：
        # MemoryRefreshTokenStore 是**进程内**字典，多 worker 下各持一份 →
        # worker A 签发的 refresh token 到 worker B 上会 "not found"。
        # 单 worker 部署下无影响，故不参与 all_healthy，但必须**显式可见**，
        # 否则运维误开 --workers > 1 会得到"刷新偶发失败"这种极难定位的现象。
        "refresh_token_store": _refresh_store_health(),
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


@router.get("/health/cache")
async def retrieval_cache_health_check():
    """检索缓存健康信息（二期 §4.2）。

    缓存不可用**不影响**整体健康判定——它是优化项，不是正确性依赖；降级到无缓存
    仍能正确检索。这里只做可观测性，好让多副本部署时能看出共享层是否生效。
    """
    from app.rag.cache import get_retrieval_cache

    cache = get_retrieval_cache()
    if cache is None:
        return {"status": "disabled", "detail": "检索缓存未启用（rag_cache_enabled=false）"}
    return {"status": "ok", "cache": cache.health()}


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
    except Exception as e:  # silent-ok: 可读降级（HSM 不可用以 status=error + 原因显式暴露）
        return {
            "status": "error",
            "error": f"{type(e).__name__}: {e!s}"
        }
