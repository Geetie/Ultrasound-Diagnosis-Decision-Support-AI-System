from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.auth import router as auth_router
from app.api.v1.consent import router as consent_router
from app.api.v1.diagnosis import router as diagnosis_router
from app.api.v1.device import router as device_router
from app.api.v1.errors import AppBaseException, app_exception_handler, http_exception_handler, general_exception_handler
from app.api.v1.feedback import router as feedback_router
from app.api.v1.fhir import router as fhir_router
from app.api.v1.health import router as health_router
from app.api.v1.imaging import router as imaging_router
from app.api.v1.security import router as security_router
from app.api.v1.session import router as session_router
from app.api.v1.speech import router as speech_router
from app.api.v1.voice import router as voice_router
from app.api.v1.alert_admin import router as alert_admin_router
from app.api.v1.obs_dashboard import router as obs_dashboard_router
from app.api.websocket.dicom import router as ws_dicom_router
from app.api.websocket.nav import router as ws_nav_router
from app.config.redis import RedisPool
from app.config.rate_limit import RateLimitMiddleware
from app.config.settings import settings
from app.config.validator import ConfigValidationError, validate_config


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.inference.factory import create_inference_engine_from_settings
    from app.inference.gpu_monitor import GPUScheduleStrategy
    from app.services.safety_gateway_client import safety_gateway_client
    from app.speech.funasr import FunASROnnxEngine

    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)

    await RedisPool.get_client()

    inference_engine = create_inference_engine_from_settings()
    app.state.inference_engine = inference_engine

    speech_engine = FunASROnnxEngine(
        model_dir=settings.funasr_model_dir,
        device=settings.funasr_device,
        num_threads=settings.funasr_num_threads,
    )
    app.state.speech_engine = speech_engine

    gpu_schedule = GPUScheduleStrategy()
    app.state.gpu_schedule = gpu_schedule

    consent_consumer = None
    if settings.consent_withdrawal_consumer_enabled:
        try:
            from app.security.consent_withdrawal_consumer import create_consent_withdrawal_consumer
            consent_consumer = await create_consent_withdrawal_consumer()
            await consent_consumer.start()
            app.state.consent_consumer = consent_consumer
        except Exception as e:
            import logging
            logging.getLogger(__name__).error("failed_to_start_consent_withdrawal_consumer", error=str(e))

    await safety_gateway_client.warmup()
    await safety_gateway_client.start_health_probe()

    from app.observability.cusum import CUSUMMonitor, AISafetyCircuitBreaker, HallucinationCUSUMBridge
    from app.observability.alert_escalation import get_alert_escalation_manager

    alert_mgr = get_alert_escalation_manager()

    def _on_monthly_alert(level, metric, value, threshold, message) -> None:
        alert_mgr.emit_alert(level=level, metric=metric, value=value, threshold=threshold, message=message)

    def _on_safety_meltdown(metric, value, threshold, reason) -> None:
        alert_mgr.emit_safety_meltdown(metric=metric, value=value, threshold=threshold, reason=reason)

    def _on_pending_notification(reason: str, confirm_deadline_seconds: int) -> None:
        alert_mgr.emit_alert(
            level="critical",
            metric="ai_safety_circuit_breaker_pending",
            value=1.0,
            threshold=0.0,
            message=f"AI辅助暂停待确认: {reason}，请在{confirm_deadline_seconds}秒内确认",
        )

    bridge = HallucinationCUSUMBridge.get_instance()
    bridge.monitor.register_alert_escalation_callback(_on_monthly_alert)
    AISafetyCircuitBreaker.register_safety_meltdown_callback(_on_safety_meltdown)

    breaker = AISafetyCircuitBreaker.get_instance()
    breaker.register_pending_notification_callback(_on_pending_notification)

    from app.api.websocket.nav import manager as nav_manager
    await nav_manager.start_redis_subscriber()

    # T-GA-INT-01: 启动后台诊断 worker 池
    # N 个 worker 协程持续 BLPop 队列，调用 run_diagnosis_pipeline 执行完整管线。
    # 任何启动失败都吞掉 — worker 池是可选组件，不阻塞主服务。
    try:
        from app.services.diagnosis_worker import start_worker_pool
        await start_worker_pool(app, n=settings.int_worker_pool_size)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("worker_pool_start_failed", extra={"error": str(e)})

    yield

    from app.api.websocket.nav import manager as nav_manager
    await nav_manager.stop_redis_subscriber()
    # T-GA-INT-01: 关闭 worker 池（取消所有 task + 等待 5s 超时）
    try:
        from app.services.diagnosis_worker import stop_worker_pool
        await stop_worker_pool(timeout=5.0)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("worker_pool_stop_failed", extra={"error": str(e)})
    await safety_gateway_client.close()
    if consent_consumer:
        await consent_consumer.stop()
    if hasattr(app.state, "inference_engine") and app.state.inference_engine:
        await app.state.inference_engine.close()
    if hasattr(app.state, "speech_engine") and app.state.speech_engine:
        await app.state.speech_engine.close()
    await RedisPool.close()


app = FastAPI(
    title="Ultrasound Diagnosis Decision Support AI System",
    description="超声诊疗决策支持AI系统 API",
    version="1.0.0",
    lifespan=lifespan,
)

allowed_origins = [origin.strip() for origin in settings.allowed_origins.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With", "X-Priority"],
)

app.add_middleware(RateLimitMiddleware)

app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(diagnosis_router, prefix="/api/v1/diagnosis")
app.include_router(session_router, prefix="/api/v1/sessions")
app.include_router(consent_router, prefix="/api/v1/consent")
app.include_router(device_router, prefix="/api/v1/admin/device")
app.include_router(feedback_router, prefix="/api/v1/feedback")
app.include_router(imaging_router, prefix="/api/v1/imaging")
app.include_router(speech_router, prefix="/api/v1/speech")
app.include_router(voice_router, prefix="/api/v1/voice")
app.include_router(security_router, prefix="/api/v1/security")
app.include_router(alert_admin_router, prefix="/api/v1/admin/alert")
app.include_router(obs_dashboard_router, prefix="/api/v1/internal/obs")
app.include_router(ws_nav_router)
app.include_router(ws_dicom_router)
app.include_router(fhir_router, prefix="/fhir")

app.add_exception_handler(AppBaseException, app_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

_dashboard_dir = Path(__file__).parent / "static" / "dashboard"
if _dashboard_dir.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")


@app.get("/")
async def root():
    return {"message": "Ultrasound Diagnosis Decision Support AI System"}
