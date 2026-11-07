import asyncio

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import text

from app.config.database import async_engine
from app.config.redis import RedisPool
from app.security.hsm import hsm_manager

logger = structlog.get_logger(__name__)

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

    # A.88：T2a 影像分类旁路的**能力状态**（同上口径：不参与 all_healthy）。
    # 为什么必须暴露：该能力此前只被审计脚本调用（`scripts/audit/p29_*`），
    # 生产链一行都没有 —— 于是"离线 AUC 0.8950"容易被误读成"系统能力"。
    # 接线后仍默认关（未做真机联调），所以"关着"与"开着但服务不可达 / 缺模型"
    # 必须一眼可分，否则运维会以为"配了就等于生效了"。
    from app.config.settings import settings as _settings
    from app.inference.t2a_classifier import probe_t2a_classifier

    t2a_classifier = await probe_t2a_classifier(
        host=_settings.ultrasound_clip_host,
        port=_settings.ultrasound_clip_port,
    )

    # N1 补齐（2026-11-03 真机）：CUSUM 熔断状态必须在**健康检查**里可见。
    # 事故背景：熔断因 `cusum_s_plus_exceeded` 触发后持久化 24h，运行中进程整体
    # suspended → **每一例判读都被强制 needs_review**，而 /health 当时完全看不出来
    # （只有需要权限的 obs 看板能查）。运维巡检查不到，系统"看起来正常但全在转人工"。
    # 与 image_leg/t2a 同口径：**不参与 all_healthy**（熔断是保护动作，不是服务故障），
    # 但必须一眼可见，并给出恢复入口提示。
    try:
        from app.observability.cusum.circuit_breaker import AISafetyCircuitBreaker

        _breaker_info = AISafetyCircuitBreaker.get_instance().suspension_info
        _breaker_state = str(
            getattr(_breaker_info.get("state"), "value", _breaker_info.get("state"))
        )
        safety_breaker = {
            "state": _breaker_state,
            "reason": _breaker_info.get("reason"),
            "degraded": _breaker_state != "active",
            "hint": (
                "AI 辅助已暂停，判读全部转人工；恢复：POST /api/v1/internal/obs/breaker/resume"
                if _breaker_state != "active"
                else None
            ),
        }
    except Exception as exc:  # silent-ok: 健康检查读不到熔断状态不应让 /health 失败
        logger.warning("health_breaker_state_read_failed", error=str(exc)[:120])
        safety_breaker = {"state": "unknown", "degraded": None}

    services = {
        "redis": "healthy" if redis_healthy else "unhealthy",
        "safety_breaker": safety_breaker,
        "database": "healthy" if db_healthy else "unhealthy",
        "hsm": hsm_status,
        "image_leg": image_leg,
        "t2a_classifier": t2a_classifier,
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
