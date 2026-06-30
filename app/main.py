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

    # RAG service (T-GA-RAG-01/02/03): query enhancement + multi-modal
    # retrieval + cross-encoder reranking + BM25 fallback. Best-effort
    # initialization — if any dependency is missing (vector store,
    # embedding service, bge-reranker), the app keeps running without RAG
    # and logs a warning. AGT-03 should check `app.state.rag_service`
    # before calling.
    if settings.rag_use_query_enhancement or settings.rag_use_reranking:
        try:
            from app.config.database import AsyncSessionLocal
            from app.embedding.bge_m3 import BgeM3MedicalService
            from app.embedding.index_factory import create_index_client_from_settings
            from app.embedding.ultrasound_clip import UltrasoundClipService
            from app.knowledge.udt_parser import UDTParser
            from app.knowledge.vocabulary import UDAFVocabulary
            from app.rag.bm25_fallback import BM25Fallback, BM25Index
            from app.rag.llm_enhancer import AgnesLLMEnhancer
            from app.rag.query_enhancer import QueryEnhancer
            from app.rag.reranker import BgeRerankerService, HeuristicReranker
            from app.rag.retriever import Retriever
            from app.rag.service import RAGServiceImpl

            llm_enhancer = None
            if settings.rag_use_llm_enhancement and settings.agnes_api_key:
                llm_enhancer = AgnesLLMEnhancer(
                    api_key=settings.agnes_api_key,
                    api_base=settings.agnes_api_base,
                    model=settings.agnes_api_model,
                    timeout_seconds=settings.agnes_timeout_seconds,
                )

            vector_client = await create_index_client_from_settings()
            embedding_svc = BgeM3MedicalService(
                host=settings.bge_m3_host,
                port=settings.bge_m3_port,
            )
            clip_svc = UltrasoundClipService(
                host=settings.ultrasound_clip_host,
                port=settings.ultrasound_clip_port,
            )
            reranker_svc = BgeRerankerService(
                base_url=f"http://{settings.bge_reranker_host}:{settings.bge_reranker_port}",
                model=settings.bge_reranker_model,
                timeout_seconds=settings.bge_reranker_timeout_seconds,
                health_check_timeout_seconds=settings.bge_reranker_health_check_timeout_seconds,
            )

            # P0-RAG-01 fix: inject real AsyncSessionLocal + UDAFVocabulary.
            # Shared singleton so the retriever's sparse leg and the BM25
            # fallback path operate on the same in-memory index instance.
            shared_bm25_index = BM25Index(
                session_factory=AsyncSessionLocal,
                vocabulary=UDAFVocabulary(),
            )

            rag_service = RAGServiceImpl(
                query_enhancer=QueryEnhancer(
                    vocabulary=UDAFVocabulary(),
                    udt_parser=UDTParser(),
                    llm_enhancer=llm_enhancer,
                ),
                retriever=Retriever(
                    vector_client=vector_client,
                    embedding_service=embedding_svc,
                    ultrasound_clip=clip_svc,
                    bm25_index=shared_bm25_index,
                ),
                reranker=reranker_svc,
                heuristic_reranker=HeuristicReranker(),
                bm25_fallback=BM25Fallback(
                    shared_bm25_index,
                    min_top1_score=settings.rag_bm25_min_top1_score,
                    min_results=settings.rag_bm25_min_results,
                ),
            )
            try:
                healthy = await rag_service.health_check()
                import logging
                logging.getLogger(__name__).info(
                    "rag_service_health_check", extra={"healthy": healthy}
                )
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning(
                    "rag_service_health_check_failed", extra={"error": str(e)}
                )
            app.state.rag_service = rag_service
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "rag_service_init_failed", extra={"error": str(e)}
            )
            app.state.rag_service = None
    else:
        app.state.rag_service = None

    # AGT 基础智能体（T-GA-AGT-01/02/03/07）：
    # dispatcher / knowledge / monitor 三个智能体在 lifespan 启动期实例化，
    # 并通过 app.state 暴露给上层 endpoint。dispatcher 当前用占位 specialist
    # 注册（AGT-04/05/06 真实实现交付后替换）。knowledge_agent 依赖 RAG
    # Service（见上文 app.state.rag_service），RAG 不可用时降级为「无 RAG」
    # 的占位实现（query 会直接抛 RuntimeError）。任何初始化失败都被吞掉 —
    # AGT 模块在 Phase 0A 是可选项，不应阻塞主服务启动。
    import logging
    _logger = logging.getLogger(__name__)
    try:
        from app.agents.dispatcher import DispatcherAgentImpl
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl
        from app.agents.specialist import PlaceholderSpecialistAgent
        from app.models.schemas import Organ

        # dispatcher：用占位 specialist 注册三个器官
        placeholder_specialists = {
            Organ.THYROID: PlaceholderSpecialistAgent(Organ.THYROID),
            Organ.LIVER: PlaceholderSpecialistAgent(Organ.LIVER),
            Organ.CARDIAC: PlaceholderSpecialistAgent(Organ.CARDIAC),
        }
        dispatcher = DispatcherAgentImpl(specialists=placeholder_specialists)
        app.state.dispatcher = dispatcher

        # knowledge_agent：RAG 不可用时降级为「仅占位」实现（query 抛 RuntimeError）
        if app.state.rag_service is not None:
            knowledge_agent = KnowledgeAgentImpl(
                rag_service=app.state.rag_service,
                redis_client=RedisPool,
            )
        else:
            class _KnowledgeUnavailable:
                async def query(self, *args, **kwargs):  # noqa: ANN001
                    raise RuntimeError(
                        "knowledge_agent unavailable: RAG service not initialized"
                    )

                async def health_check(self) -> bool:
                    return False

            knowledge_agent = _KnowledgeUnavailable()
        app.state.knowledge_agent = knowledge_agent

        # monitor_agent：独立组件，无需外部依赖
        monitor_agent = MonitorAgentImpl()
        app.state.monitor_agent = monitor_agent

        # 启动期健康检查（best-effort，不阻塞启动）
        try:
            dispatcher_ok = await dispatcher.health_check()
        except Exception as e:
            dispatcher_ok = False
            _logger.warning("dispatcher_health_check_failed", extra={"error": str(e)})
        try:
            knowledge_ok = await knowledge_agent.health_check()
        except Exception as e:
            knowledge_ok = False
            _logger.warning("knowledge_agent_health_check_failed", extra={"error": str(e)})
        try:
            monitor_ok = await monitor_agent.health_check()
        except Exception as e:
            monitor_ok = False
            _logger.warning("monitor_agent_health_check_failed", extra={"error": str(e)})
        _logger.info(
            "agents_health_check",
            extra={
                "dispatcher": dispatcher_ok,
                "knowledge_agent": knowledge_ok,
                "monitor_agent": monitor_ok,
            },
        )
    except Exception as e:
        _logger.warning("agents_init_failed", extra={"error": str(e)})
        app.state.dispatcher = None
        app.state.knowledge_agent = None
        app.state.monitor_agent = None

    yield

    from app.api.websocket.nav import manager as nav_manager
    await nav_manager.stop_redis_subscriber()
    await safety_gateway_client.close()
    if consent_consumer:
        await consent_consumer.stop()
    if hasattr(app.state, "inference_engine") and app.state.inference_engine:
        await app.state.inference_engine.close()
    if hasattr(app.state, "speech_engine") and app.state.speech_engine:
        await app.state.speech_engine.close()
    if hasattr(app.state, "rag_service") and app.state.rag_service:
        try:
            await app.state.rag_service.close()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "rag_service_close_failed", extra={"error": str(e)}
            )
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
