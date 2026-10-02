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
from app.api.v1.report import router as report_router
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
from app.security.security_headers import SecurityHeadersMiddleware
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

    # P0-5（wiring audit）：AlertEscalationManager 启停接线——
    # 生产侧回调已注册（CUSUM/熔断），但 start() 此前从未被调用，
    # critical webhook flush 与超时升级循环实际不生效。best-effort：
    # 告警升级失败不阻塞主服务。
    try:
        await alert_mgr.start()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "alert_escalation_start_failed", extra={"error": str(e)}
        )

    # P1-2（wiring audit）：审计哈希链周期校验（含 verify_all_chains，
    # 断链即 critical 告警）。同样 best-effort。
    try:
        from app.services.audit_service import start_periodic_verification
        await start_periodic_verification()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "audit_periodic_verification_start_failed", extra={"error": str(e)}
        )

    # T-GA-INT-01: 启动后台诊断 worker 池
    # N 个 worker 协程持续 BLPop 队列，调用 run_diagnosis_pipeline 执行完整管线。
    # 任何启动失败都吞掉 — worker 池是可选组件，不阻塞主服务。
    try:
        from app.services.diagnosis_worker import start_worker_pool
        await start_worker_pool(app, n=settings.int_worker_pool_size)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("worker_pool_start_failed", extra={"error": str(e)})

    # OBS: 基础设施巡检循环（Redis / PG / 磁盘）。与 worker 池同样按
    # best-effort 处理——启动失败不阻塞主服务；关闭时经 shutdown event 退出，
    # 避免此前该模块只定义不接线（巡检能力实际未生效）。
    try:
        import asyncio as _asyncio
        from app.observability.infra_health import run_infra_health_checks

        app.state.infra_health_task = _asyncio.create_task(
            run_infra_health_checks()
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("infra_health_start_failed", extra={"error": str(e)})
        app.state.infra_health_task = None

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
            from app.rag.cache import RetrievalCache
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
                # 检索结果缓存：fusion 的 consistency / crag 直连检索路径
                # 绕过知识智能体缓存，这里补齐；Redis 不可用时自动只用 L2。
                cache=(
                    RetrievalCache(
                        RedisPool,
                        redis_ttl_seconds=settings.rag_cache_redis_ttl_seconds,
                        local_capacity=settings.rag_cache_local_capacity,
                        local_ttl_seconds=settings.rag_cache_local_ttl_seconds,
                        negative_ttl_seconds=settings.rag_cache_negative_ttl_seconds,
                        jitter_ratio=settings.rag_cache_ttl_jitter_ratio,
                    )
                    if settings.rag_cache_enabled
                    else None
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

    # AGT 智能体（T-GA-AGT-01/02/03/04/05/06/07）：
    # dispatcher / knowledge / monitor 三个智能体在 lifespan 启动期实例化，
    # 并通过 app.state 暴露给上层 endpoint。Wave 5+ 后 dispatcher 通过
    # :func:`app.agents.specialist.build_dispatcher` 工厂注入 inference + knowledge
    # 依赖，真实 specialist（ThyroidSpecialistAgent / LiverSpecialistAgent /
    # CardiacSpecialistAgent）替换 Wave 1+2 阶段的占位实现。
    # knowledge_agent 依赖 RAG Service（见上文 app.state.rag_service），
    # RAG 不可用时降级为「无 RAG」的占位实现（query 会直接抛 RuntimeError）。
    # 任何初始化失败都被吞掉 — AGT 模块在 Phase 0A 是可选项，不应阻塞主服务启动。
    import logging
    _logger = logging.getLogger(__name__)
    try:
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl
        from app.agents.specialist import build_dispatcher
        from app.agents.dispatcher import DispatcherAgentImpl
        from app.inference.factory import create_inference_engine_from_settings
        from app.models.schemas import Organ
        from app.agents.specialist import PlaceholderSpecialistAgent

        # knowledge_agent：RAG 不可用时降级为「仅占位」实现（query 抛 RuntimeError）
        if app.state.rag_service is not None:
            # KB 版本解析器：动态从 RAG 服务或 settings 获取当前 KB 版本。
            #
            # 优先级：
            #   1) RAG 服务暴露 ``current_kb_version()`` 方法 → 优先使用（最权威）
            #   2) RAG 服务暴露 ``kb_version`` 属性 → 次选
            #   3) settings.phase0a_mock_kb_version（运维可配置，Phase 0A 默认）
            #   4) 解析失败时回退到 ``"kb_v0.0.0"``（缓存降级但仍可用）
            #
            # KB 版本变更会嵌入缓存键（见 ``app/agents/knowledge.py:_make_cache_key``），
            # 实现自然失效，无需显式 ACK 事件。
            def _kb_version_resolver() -> str:
                fallback_version = settings.phase0a_mock_kb_version or "kb_v0.0.0"
                try:
                    rag = app.state.rag_service
                    if rag is not None:
                        # 1) 方法优先（与 RAG 服务契约对齐）
                        if hasattr(rag, "current_kb_version"):
                            v = rag.current_kb_version()
                            if isinstance(v, str) and v:
                                return v
                        # 2) 属性次选
                        if hasattr(rag, "kb_version"):
                            v = rag.kb_version
                            if isinstance(v, str) and v:
                                return v
                    return fallback_version
                except Exception:
                    return fallback_version

            knowledge_agent = KnowledgeAgentImpl(
                rag_service=app.state.rag_service,
                redis_client=RedisPool,
                kb_version_resolver=_kb_version_resolver,
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

        # inference_engine：用于 specialist 的 LLM 调用。
        # 创建失败（如配置缺失）→ dispatcher 自动降级为占位 specialist。
        inference_engine = None
        try:
            inference_engine = create_inference_engine_from_settings()
        except Exception as e:
            _logger.warning(
                "inference_engine_init_failed", extra={"error": str(e)}
            )
        app.state.inference_engine = inference_engine

        # dispatcher：通过 build_dispatcher 工厂注入 inference + knowledge。
        # 依赖任一缺失时自动用占位 specialist 兜底（dispatcher 仍可启动）。
        # 知识查询接口 ``app.agents.protocols.KnowledgeAgent`` 是 ABC，
        # 我们的占位 ``_KnowledgeUnavailable`` 没有显式继承，但提供同名
        # ``query`` / ``health_check`` 协程 → 满足 duck-typing 契约；
        # 工厂在 knowledge_agent 异常时也会回退到占位路径。
        if isinstance(knowledge_agent, KnowledgeAgentImpl) and inference_engine is not None:
            dispatcher = build_dispatcher(
                inference_engine=inference_engine,
                knowledge_agent=knowledge_agent,
                use_placeholder_on_missing=False,
            )
        else:
            # 依赖缺失（无 RAG 或无 inference）→ 使用占位 specialist 兜底
            dispatcher = build_dispatcher(
                inference_engine=None,
                knowledge_agent=None,
                use_placeholder_on_missing=True,
            )
        app.state.dispatcher = dispatcher

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

    # FUS 融合层（T-GA-FUS-01/02）：
    # 协调 conflict / consistency / self-rag / crag 四大组件。依赖任一
    # 缺失（如 RAG / inference）→ 降级为"无 Self-RAG / 无 CRAG"模式。
    # 任何初始化失败都吞掉 — 融合层是可选组件，不应阻塞主服务启动。
    # P2-8 修复：CRAG 优先经 DispatcherAgent 路由（含 specialist_invoker 注入）；
    # P2-6 修复：CRAG 失败时真实调用 specialist LLM 重生成（而非仅 +0.2 confidence）。
    try:
        from app.fusion.conflict import ConflictDetector
        from app.fusion.consistency import ConsistencyChecker
        from app.fusion.crag import CRAGHandler
        from app.fusion.self_rag import SelfRAGEvaluator
        from app.fusion.service import FusionEngineImpl

        rag_service = getattr(app.state, "rag_service", None)
        inference_engine = getattr(app.state, "inference_engine", None)
        dispatcher = getattr(app.state, "dispatcher", None)

        conflict_detector = ConflictDetector()
        consistency_checker = ConsistencyChecker(rag_service=rag_service)
        self_rag_evaluator = (
            SelfRAGEvaluator(inference_engine=inference_engine)
            if inference_engine is not None
            else None
        )

        # P2-8 修复：注入 dispatcher（优先路径）+ knowledge_agent
        # P2-6 修复：注入 specialist_invoker（真实重生成 LLM）
        knowledge_agent = getattr(app.state, "knowledge_agent", None)
        specialist_invoker = None
        if dispatcher is not None and hasattr(dispatcher, "invoke_specialist"):
            specialist_invoker = dispatcher.invoke_specialist
        elif dispatcher is not None and hasattr(dispatcher, "dispatch"):
            # 兼容占位 dispatcher：使用 dispatch 路径
            async def _placeholder_specialist_invoker(context):  # type: ignore[no-untyped-def]
                return await dispatcher.dispatch(context)

            specialist_invoker = _placeholder_specialist_invoker

        crag_handler = (
            CRAGHandler(
                rag_service=rag_service,
                knowledge_agent=knowledge_agent,
                dispatcher_agent=dispatcher,
                specialist_invoker=specialist_invoker,
            )
            if (rag_service is not None or knowledge_agent is not None or dispatcher is not None)
            else None
        )

        fusion_engine = FusionEngineImpl(
            conflict_detector=conflict_detector,
            consistency_checker=consistency_checker,
            self_rag_evaluator=self_rag_evaluator,
            crag_handler=crag_handler,
            rag_service=rag_service,
        )
        app.state.fusion_engine = fusion_engine
        _logger.info(
            "fusion_engine_initialized",
            has_self_rag=self_rag_evaluator is not None,
            has_crag=crag_handler is not None,
            crag_via_dispatcher=dispatcher is not None,
            crag_specialist_reinvoke=specialist_invoker is not None,
        )
    except Exception as e:
        _logger.warning("fusion_engine_init_failed", extra={"error": str(e)})
        app.state.fusion_engine = None

    yield

    from app.api.websocket.nav import manager as nav_manager
    await nav_manager.stop_redis_subscriber()
    # P0-5/P1-2：停止告警升级循环与审计链周期校验（与 lifespan 启动配对）
    try:
        from app.observability.alert_escalation import get_alert_escalation_manager
        await get_alert_escalation_manager().stop()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "alert_escalation_stop_failed", extra={"error": str(e)}
        )
    try:
        from app.services.audit_service import stop_periodic_verification
        await stop_periodic_verification()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(
            "audit_periodic_verification_stop_failed", extra={"error": str(e)}
        )
    # T-GA-INT-01: 关闭 worker 池（取消所有 task + 等待 5s 超时）
    try:
        from app.services.diagnosis_worker import stop_worker_pool
        await stop_worker_pool(timeout=5.0)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("worker_pool_stop_failed", extra={"error": str(e)})
    # OBS: 停止基础设施巡检（置位 shutdown event → 等待 task 退出 → 兜底取消）
    try:
        import asyncio as _asyncio
        from app.observability.infra_health import stop_infra_health_checks

        await stop_infra_health_checks()
        infra_task = getattr(app.state, "infra_health_task", None)
        if infra_task is not None and not infra_task.done():
            await _asyncio.wait_for(infra_task, timeout=5.0)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("infra_health_stop_failed", extra={"error": str(e)})
        infra_task = getattr(app.state, "infra_health_task", None)
        if infra_task is not None and not infra_task.done():
            infra_task.cancel()
    await safety_gateway_client.close()
    if consent_consumer:
        await consent_consumer.stop()
    # AGT 智能体显式关闭阶段（按 monitor → knowledge → dispatcher 顺序）：
    # 反向拓扑序关闭，先释放依赖方，再释放被依赖方，确保每个 close()
    # 看到的依赖状态都是有效的。
    import logging
    _shutdown_logger = logging.getLogger(__name__)
    for agent_attr in ("monitor_agent", "knowledge_agent", "dispatcher"):
        agent = getattr(app.state, agent_attr, None)
        if agent is not None and hasattr(agent, "close"):
            try:
                await agent.close()
            except Exception as e:
                _shutdown_logger.warning(
                    f"agents_close_failed", extra={"agent": agent_attr, "error": str(e)}
                )
    if hasattr(app.state, "inference_engine") and app.state.inference_engine:
        await app.state.inference_engine.close()
    if hasattr(app.state, "speech_engine") and app.state.speech_engine:
        await app.state.speech_engine.close()
    if hasattr(app.state, "fusion_engine") and app.state.fusion_engine:
        try:
            await app.state.fusion_engine.close()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                "fusion_engine_close_failed", extra={"error": str(e)}
            )
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

# T-G1-INT-04：安全响应头（CSP / 点击劫持防护 / 患者数据禁缓存）
app.add_middleware(
    SecurityHeadersMiddleware,
    csp_policy=settings.csp_policy or None,
    report_only=settings.csp_report_only,
    hsts_max_age=settings.hsts_max_age_seconds if settings.hsts_enabled else 0,
)

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
app.include_router(report_router, prefix="/api/v1/report")

app.add_exception_handler(AppBaseException, app_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

_dashboard_dir = Path(__file__).parent / "static" / "dashboard"
if _dashboard_dir.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")


@app.get("/")
async def root():
    return {"message": "Ultrasound Diagnosis Decision Support AI System"}
