import asyncio
import functools
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Literal

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from safety_gateway.validation_result import ConsentStatus
from safety_gateway.errors import (
    SafetyGatewayError,
    ServiceUnavailableError,
    HealthCheckFailureError,
    DesensitizationSafetyViolationError,
    ConsentSafetyViolationError,
    DatabaseUnavailableError,
    CombinedUnavailableError,
)
from safety_gateway.redis_pool import SafetyGatewayRedisPool
from safety_gateway.redis_subscriber import RedisSubscriber
from safety_gateway.sg_config import sg_settings as settings
from safety_gateway.health.latency_tracker import LatencyTracker
from safety_gateway.sg_validator import SGConfigValidationError as ConfigValidationError, validate_sg_config as validate_config
from safety_gateway.checker import CheckResultStatus, DegradedReason, get_safety_gateway_checker
from safety_gateway.desensitization import sg_desensitization_engine
from safety_gateway.threshold import start_threshold_subscribe, stop_threshold_subscribe

logger = structlog.get_logger()

# 必须与后端发布频道一致（app/config/redis_keys.py DESSENS_RULES_UPDATE），
# 可经 DESSENS_RULES_SYNC_CHANNEL 环境变量覆盖（wiring-audit P1-1）。
_RULES_SYNC_CHANNEL = settings.desens_rules_sync_channel
_rules_sync_subscriber: RedisSubscriber | None = None
_sg_concurrency_semaphore: asyncio.Semaphore | None = None


def sg_endpoint_handler(error_prefix: str):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await func(*args, **kwargs)
            except SafetyGatewayError:
                raise
            except (ValueError, KeyError) as e:
                logger.error("sg_input_error", prefix=error_prefix, error=str(e))
                raise ServiceUnavailableError(
                    error_message=f"输入数据异常: {e!s}",
                    detail=f"sg_{error_prefix}_input_error",
                )
            except TypeError as e:
                logger.error("sg_type_error", prefix=error_prefix, error=str(e))
                raise ServiceUnavailableError(
                    error_message=f"处理失败: {e!s}",
                    detail=f"sg_{error_prefix}_type_error",
                )
            except Exception as e:
                logger.error("sg_unexpected_error", prefix=error_prefix, error=str(e), error_type=type(e).__name__)
                raise ServiceUnavailableError(
                    error_message=f"处理失败: {e!s}",
                    detail=f"sg_{error_prefix}_error",
                )
        return wrapper
    return decorator


async def _handle_rules_sync_message(data: dict[str, Any]) -> None:
    logger.info("sg_rules_sync_received")
    try:
        await asyncio.to_thread(sg_desensitization_engine.reload_rules)
    except Exception as e:
        logger.error("sg_rules_sync_reload_failed", error=str(e))


class SafetyCheckRequest(BaseModel):
    patient_id: str = Field(description="患者唯一标识")
    output_data: dict[str, Any] = Field(description="待校验的诊断输出数据")
    session_id: str | None = Field(default=None, description="会话标识，用于日志追踪")
    consent_status_at_input: Literal["granted", "denied", "withdrawn", "pending"] | None = Field(default=None, description="入口阶段的同意状态，用于快速拒绝")
    timestamp: str | None = Field(default=None, description="请求时间戳")
    edge_connected: bool = Field(default=True, description="边缘后端是否连接，断连时进入本地降级")
    desensitization_degraded: bool = Field(default=False, description="融合层脱敏服务是否已降级")
    conclusion_suppressed: bool = Field(default=False, description="融合层是否已主动抑制诊断结论")
    hallucination_severity: str | None = Field(default=None, description="幻觉严重程度：severe时附加安全标记")


class SafetyCheckResponse(BaseModel):
    passed: bool
    all_passed: bool = False  # 对齐04-安全合规§2: 第1/2层modified=True属正常脱敏不影响all_passed; 仅第3层SG校验failed或degraded_mode时all_passed=False。当前实现: SG是第3层防线，PASSED且非降级=三层全部通过
    consent_status: ConsentStatus | None = None
    consent_status_at_output: str | None = None
    consent_status_source: str | None = None
    sensitive_data_detected: list[str] = []
    message: str
    degraded_mode: bool = False
    degraded_reason: str | None = None
    degraded_output: dict[str, Any] | None = None
    elapsed_ms: float = 0.0
    consent_check_timeout: bool = False
    hallucination_warning: bool = False


class ConsistencyCheckRequest(BaseModel):
    fusion_output: dict[str, Any]
    specialist_conclusions: list[dict[str, Any]]
    session_id: str | None = None


class ConsistencyCheckResponse(BaseModel):
    consistent: bool
    conflicts: list[dict[str, Any]] = []
    message: str


class ContraindicationCheckRequest(BaseModel):
    patient_id: str
    organ: str
    diagnosis_data: dict[str, Any]
    session_id: str | None = None


class ContraindicationCheckResponse(BaseModel):
    has_contraindication: bool
    contraindications: list[dict[str, Any]] = []
    message: str


class ConsentStatusResponse(BaseModel):
    patient_id: str
    consent_status: str | None = None
    checked_at: str
    source: str = "postgresql"


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    import sys

    workers_env = os.environ.get("UVICORN_WORKERS", os.environ.get("WEB_CONCURRENCY", None))
    if workers_env is not None and workers_env != "1":
        logger.error("sg_workers_must_be_1_from_env", workers=workers_env)
        raise ConfigValidationError([f"SafetyGateway must run with exactly 1 worker (got {workers_env}). Use uvicorn --workers 1."])

    actual_workers = _get_actual_worker_count()
    if actual_workers is not None and actual_workers != 1:
        logger.error("sg_workers_must_be_1_actual", actual_workers=actual_workers)
        raise ConfigValidationError([f"SafetyGateway must run with exactly 1 worker (detected {actual_workers}). SafetyGateway is not designed for multi-worker deployment."])

    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)

    await SafetyGatewayRedisPool.get_client()

    logger.info(
        "safety_gateway_starting",
        host=settings.safety_gateway_host,
        port=settings.safety_gateway_port,
    )

    global _rules_sync_subscriber
    _rules_sync_subscriber = RedisSubscriber(
        channel=_RULES_SYNC_CHANNEL,
        handler=_handle_rules_sync_message,
    )
    await _rules_sync_subscriber.start()
    await start_threshold_subscribe()

    global _sg_concurrency_semaphore
    _sg_concurrency_semaphore = asyncio.Semaphore(settings.sg_max_concurrent_checks)

    yield

    if _rules_sync_subscriber:
        await _rules_sync_subscriber.stop()
        _rules_sync_subscriber = None

    await stop_threshold_subscribe()

    checker = get_safety_gateway_checker()
    await checker.close()
    from safety_gateway.checker import _get_health_checker
    health_checker = _get_health_checker()
    await health_checker.aclose()
    await SafetyGatewayRedisPool.close()
    from safety_gateway.sg_db import close_db
    await close_db()
    logger.info("safety_gateway_shutdown_complete")


def _get_actual_worker_count() -> int | None:
    """使用psutil获取当前进程的子进程数量

    返回值：
    - int: 子进程数量
    - None: 无法获取子进程数量（如Windows环境或权限不足）

    注意：此函数检测的是当前Python进程的直接子进程数量。
    uvicorn单worker模式下主进程会fork出工作进程，
    因此正常情况下应该只有0个子进程。
    如果检测到>=1个子进程，说明可能以多worker模式运行。
    """
    try:
        import psutil
        current_process = psutil.Process(os.getpid())
        children = current_process.children(recursive=False)
        return len(children)
    except (ImportError, PermissionError, OSError):
        return None


app = FastAPI(
    title="Safety Gateway",
    description="超声诊疗决策支持AI系统 - 安全网关服务（独立进程）",
    version="1.0.0",
    lifespan=lifespan,
)

allowed_origins = settings.sg_cors_allowed_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
)


@app.exception_handler(SafetyGatewayError)
async def sg_error_handler(request: Request, exc: SafetyGatewayError):
    if isinstance(exc, (DesensitizationSafetyViolationError, ConsentSafetyViolationError, DatabaseUnavailableError, CombinedUnavailableError, ServiceUnavailableError)):
        status_code = 503
    else:
        status_code = 500
    return JSONResponse(
        status_code=status_code,
        content=exc.to_dict(),
    )


@app.get("/")
async def root():
    return {
        "service": "safety-gateway",
        "version": "1.0.0",
        "status": "running",
    }


@app.get("/health")
async def health_check():
    from safety_gateway.checker import _get_health_checker
    health_checker = _get_health_checker()
    result = await health_checker.check_all()
    if result["status"] == "healthy":
        return JSONResponse(content=result, status_code=200)
    elif result["status"] == "degraded":
        return JSONResponse(content=result, status_code=200, headers={"X-SG-Status": "degraded"})
    else:
        result["error_code"] = HealthCheckFailureError.code
        return JSONResponse(content=result, status_code=503, headers={"X-SG-Status": "unhealthy"})


@app.get("/health/ready")
async def readiness_check():
    from safety_gateway.checker import _get_health_checker
    health_checker = _get_health_checker()
    result = await health_checker.check_all()
    components = result.get("components", {})
    core_ok = components.get("database", {}).get("status") == "healthy" and components.get("rules_engine", {}).get("status") == "healthy"
    if core_ok and result["status"] in ("healthy", "degraded"):
        return JSONResponse(content={"status": "ready", "components": components}, status_code=200)
    return JSONResponse(content={"status": "not_ready", "components": components}, status_code=503)


@app.get("/health/watchdog")
async def watchdog_check():
    checker = get_safety_gateway_checker()
    from safety_gateway.checker import _get_health_checker as _get_sg_health_checker
    health_checker = _get_sg_health_checker()
    is_alive = checker is not None
    latency_stats = await health_checker.compute_latency_stats()
    p99_status = latency_stats.get("p99_status", "unknown")
    return {
        "alive": is_alive,
        "p99_status": p99_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/consent-status/{patient_id}", response_model=ConsentStatusResponse)
async def get_consent_status(patient_id: str):
    checker = get_safety_gateway_checker()
    result = await checker.check_consent_status(patient_id)
    consent_status = result.status
    is_timeout = result.timeout
    is_db_error = result.error

    if is_db_error:
        raise ServiceUnavailableError(
            error_message="数据库查询异常，无法获取同意状态",
            detail="consent_check_db_error",
        )

    if is_timeout:
        logger.warning("sg_consent_status_timeout", patient_id=patient_id)

    return ConsentStatusResponse(
        patient_id=patient_id,
        consent_status=str(consent_status) if consent_status else None,
        checked_at=datetime.now(timezone.utc).isoformat(),
        source="postgresql",
    )


@app.post("/api/v1/safety/check", response_model=SafetyCheckResponse)
@sg_endpoint_handler("safety_check")
async def perform_safety_check(request: SafetyCheckRequest):
    from safety_gateway.health.memory_monitor import MemoryMonitor
    from safety_gateway.sg_config import sg_settings as _settings
    from safety_gateway.checker import _get_health_checker

    if MemoryMonitor.is_over_budget():
        raise ServiceUnavailableError(
            error_message="安全网关内存超预算，拒绝新请求（E4005）",
            detail="sg_memory_over_budget",
        )

    health_checker = _get_health_checker()
    if health_checker._latency_tracker.is_throttling:
        raise ServiceUnavailableError(
            error_message="安全网关P99延迟持续超限，拒绝新请求（E4005）",
            detail="sg_p99_throttling",
        )

    if _sg_concurrency_semaphore is None:
        raise ServiceUnavailableError(
            error_message="安全网关服务未就绪（E4005）",
            detail="sg_not_initialized",
        )

    try:
        await asyncio.wait_for(
            _sg_concurrency_semaphore.acquire(),
            timeout=_settings.safety_gateway_queue_timeout_seconds,
        )
    except asyncio.TimeoutError:
        raise ServiceUnavailableError(
            error_message=f"安全网关排队超时({settings.safety_gateway_queue_timeout_seconds}s)，拒绝请求（E4005）",
            detail="sg_queue_timeout",
        )

    try:
        return await _perform_safety_check_inner(request)
    finally:
        _sg_concurrency_semaphore.release()


async def _perform_safety_check_inner(request: SafetyCheckRequest) -> SafetyCheckResponse:
    checker = get_safety_gateway_checker()

    logger.info(
        "sg_safety_check_entered",
        patient_id=request.patient_id,
        session_id=request.session_id,
        desensitization_degraded=request.desensitization_degraded,
    )

    check_result = await checker.perform_full_check(
        patient_id=request.patient_id,
        output_data=request.output_data,
        edge_connected=request.edge_connected,
        consent_status_at_input=request.consent_status_at_input,
        desensitization_degraded=request.desensitization_degraded,
        conclusion_suppressed=request.conclusion_suppressed,
        hallucination_severity=request.hallucination_severity,
    )

    response = SafetyCheckResponse(
        passed=check_result.status == CheckResultStatus.PASSED,
        all_passed=check_result.status == CheckResultStatus.PASSED and not check_result.degraded_mode and not check_result.consent_check_timeout,
        consent_status=check_result.consent_status,
        consent_status_at_output=str(check_result.consent_status) if check_result.consent_status else None,
        consent_status_source=check_result.consent_status_source,
        sensitive_data_detected=check_result.sensitive_data_detected,
        message=check_result.message,
        degraded_mode=check_result.degraded_mode,
        degraded_reason=str(check_result.degraded_reason) if check_result.degraded_reason else None,
        elapsed_ms=check_result.elapsed_ms,
        consent_check_timeout=check_result.consent_check_timeout,
        hallucination_warning=check_result.hallucination_warning,
    )

    if check_result.degraded_mode:
        reason = check_result.degraded_reason or DegradedReason.SENSITIVE_DATA_DETECTED
        fusion_actually_available = not (
            check_result.degraded_reason in (
                DegradedReason.FUSION_UNAVAILABLE,
                DegradedReason.FUSION_UNAVAILABLE_SENSITIVE_DETECTED,
                DegradedReason.DUAL_DEGRADATION_FUSION_AND_SG_UNAVAILABLE,
            )
        )
        response.degraded_output = checker.apply_degraded_output(request.output_data, reason=reason, fusion_available=fusion_actually_available)

    logger.info(
        "sg_safety_check_completed",
        patient_id=request.patient_id,
        session_id=request.session_id,
        status=str(check_result.status),
        elapsed_ms=check_result.elapsed_ms,
    )

    return response


@app.post("/api/v1/safety/consistency", response_model=ConsistencyCheckResponse)
@sg_endpoint_handler("consistency_check")
async def perform_consistency_check(request: ConsistencyCheckRequest):
    checker = get_safety_gateway_checker()

    logger.info(
        "sg_consistency_check_entered",
        session_id=request.session_id,
        conclusion_count=len(request.specialist_conclusions),
    )

    consistency_result = await checker.check_consistency(
        fusion_output=request.fusion_output,
        specialist_conclusions=request.specialist_conclusions,
    )

    logger.info(
        "sg_consistency_check_completed",
        session_id=request.session_id,
        consistent=consistency_result["consistent"],
        conflict_count=len(consistency_result.get("conflicts", [])),
    )

    return ConsistencyCheckResponse(
        consistent=consistency_result["consistent"],
        conflicts=consistency_result.get("conflicts", []),
        message=consistency_result.get("message", ""),
    )


@app.post("/api/v1/safety/contraindication", response_model=ContraindicationCheckResponse)
@sg_endpoint_handler("contraindication_check")
async def perform_contraindication_check(request: ContraindicationCheckRequest):
    from safety_gateway.health.memory_monitor import MemoryMonitor

    if MemoryMonitor.is_over_budget():
        raise ServiceUnavailableError(
            error_message="安全网关内存超预算，拒绝新请求（E4005）",
            detail="sg_memory_over_budget",
        )

    checker = get_safety_gateway_checker()

    logger.info(
        "sg_contraindication_check_entered",
        patient_id=request.patient_id,
        organ=request.organ,
        session_id=request.session_id,
    )

    contra_result = await checker.check_contraindication(
        patient_id=request.patient_id,
        organ=request.organ,
        diagnosis_data=request.diagnosis_data,
    )

    logger.info(
        "sg_contraindication_check_completed",
        patient_id=request.patient_id,
        organ=request.organ,
        has_contraindication=contra_result["has_contraindication"],
    )

    return ContraindicationCheckResponse(
        has_contraindication=contra_result["has_contraindication"],
        contraindications=contra_result.get("contraindications", []),
        message=contra_result.get("message", ""),
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "safety_gateway.main:app",
        host=settings.safety_gateway_host,
        port=settings.safety_gateway_port,
        log_level=settings.log_level.lower(),
        access_log=True,
    )
