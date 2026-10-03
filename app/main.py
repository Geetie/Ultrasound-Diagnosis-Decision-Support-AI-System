import os
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.alert_admin import router as alert_admin_router
from app.api.v1.alerts import router as internal_alerts_router
from app.api.v1.auth import router as auth_router
from app.api.v1.consent import router as consent_router
from app.api.v1.device import router as device_router
from app.api.v1.diagnosis import router as diagnosis_router
from app.api.v1.errors import AppBaseException, app_exception_handler, general_exception_handler, http_exception_handler
from app.api.v1.feedback import router as feedback_router
from app.api.v1.fhir import router as fhir_router
from app.api.v1.health import router as health_router
from app.api.v1.imaging import router as imaging_router
from app.api.v1.obs_dashboard import router as obs_dashboard_router
from app.api.v1.report import router as report_router
from app.api.v1.security import router as security_router
from app.api.v1.segmentation import router as segmentation_router
from app.api.v1.session import router as session_router
from app.api.v1.speech import router as speech_router
from app.api.v1.voice import router as voice_router
from app.api.websocket.dicom import router as ws_dicom_router
from app.api.websocket.nav import router as ws_nav_router
from app.config.rate_limit import RateLimitMiddleware
from app.config.redis import RedisPool
from app.config.settings import settings
from app.config.validator import ConfigValidationError, validate_config
from app.observability.request_id import RequestIdMiddleware
from app.security.security_headers import SecurityHeadersMiddleware

# 启动/关闭路径的诊断必须走 structlog。
# ## 为什么（2026-09-28 真机部署暴露）
# 原先这些地方用的是 `logger.warning("xxx_failed", extra={"error": ...})`，
# 而本项目配置的日志渲染器是 structlog，stdlib 侧没有能渲染 `extra` 的 formatter ——
# 于是真机上只剩一行光秃秃的 `rag_service_init_failed`，**失败原因被整个丢掉**，
# 排查全靠猜。改成 structlog 后 `error=...` 才会真正出现在日志里。
logger = structlog.get_logger(__name__)


def _detect_worker_count() -> int:
    """当前进程的 uvicorn worker 数（``WEB_CONCURRENCY``，缺省 1）。

    单进程是本系统的**硬前提**（见 lifespan 里的 nav 订阅放大与 rotate 锁告警），
    所以这个判定必须容错：缺失/空串/非数字一律按 1 处理，绝不抛。
    """
    try:
        return int(os.environ.get("WEB_CONCURRENCY", "1") or "1")
    except ValueError:
        return 1


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.inference.factory import create_inference_engine_from_settings
    from app.services.safety_gateway_client import safety_gateway_client
    from app.speech.funasr import FunASROnnxEngine

    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)

    # OBS: 分布式追踪接线（OTLP → Jaeger；obs_tracing_enabled 默认关闭，
    # full Profile 强制采样 ≥0.5）。init_tracing 内部自带开关与全量兜底，
    # OTel 未安装时只告警不阻断启动 —— 修复 05-可观测性 附录A §7
    # "init_tracing/instrument_fastapi 在 main.py 无调用" 的断链。
    try:
        from app.observability.tracing import init_tracing

        init_tracing()
    except Exception as e:
        logger.warning("tracing_init_failed", error=str(e))

    await RedisPool.get_client()

    inference_engine = create_inference_engine_from_settings()
    app.state.inference_engine = inference_engine

    speech_engine = FunASROnnxEngine(
        model_dir=settings.funasr_model_dir,
        device=settings.funasr_device,
        num_threads=settings.funasr_num_threads,
    )
    app.state.speech_engine = speech_engine

    # wiring-audit P1-5：删除死赋值 app.state.gpu_schedule（GPUScheduleStrategy
    # 实例全库无读取方）与 app.state.consent_consumer（shutdown 用局部变量，
    # state 上的副本零读取方）。
    consent_consumer = None
    if settings.consent_withdrawal_consumer_enabled:
        try:
            from app.security.consent_withdrawal_consumer import create_consent_withdrawal_consumer
            consent_consumer = await create_consent_withdrawal_consumer()
            await consent_consumer.start()
        except Exception as e:
            logger.error("failed_to_start_consent_withdrawal_consumer", error=str(e))

    await safety_gateway_client.warmup()
    await safety_gateway_client.start_health_probe()

    from app.observability.alert_escalation import get_alert_escalation_manager
    from app.observability.cusum import AISafetyCircuitBreaker, HallucinationCUSUMBridge

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

    # 并发审查 P2-9：多 worker 下每个进程都会建一条 Redis 订阅，同一条 nav 消息会被
    # 广播 N 次（每进程各过滤一次，功能正确但开销 N 倍）。生产是 --workers 1，
    # 这里在 workers>1 时显式告警，避免"哪天有人加了 --workers 4"静默劣化。
    _worker_count = _detect_worker_count()
    if _worker_count > 1:
        logger.warning(
            "multi_worker_nav_redis_subscription_amplified",
            workers=_worker_count,
            impact="每条 nav 消息被 Redis pub/sub 广播 N 次；且 refresh token rotate 的"
            " per-token 锁只在进程内闭合（跨进程需改 Lua/WATCH）",
            action="上线前先做 P2-9（抽独立订阅进程）与 P0-4 残留（Lua 原子轮换）",
        )

    # SEC：脱敏引擎的 HMAC 密钥 / 令牌映射 / 计数器必须从 Redis 恢复。
    # 这件事**不能**在 import 期做（那时已有事件循环，见
    # `get_desensitization_engine` 的说明），否则每次重启都换一把随机 HMAC 密钥，
    # 历史令牌与还原映射全部失配。这里 best-effort：载入失败只告警，
    # 引擎会退化为进程内状态（不阻断诊断）。
    try:
        from app.security.desensitization import get_desensitization_engine

        await get_desensitization_engine().ensure_state_loaded()
    except Exception as e:

        logger.warning("desens_state_load_on_startup_failed", error=str(e))

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
        logger.warning("worker_pool_start_failed", error=str(e))

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
        logger.warning("infra_health_start_failed", error=str(e))
        app.state.infra_health_task = None

    # OBS: 指标导出链路接线（/metrics 暴露 + VictoriaMetrics 推送）。
    # 此前 `start_metrics_server()` 只被测试调用、`VictoriaMetricsPusher` 在 app 内
    # 从未实例化 —— 运行期 Prometheus 抓不到、VM 收不到推送，看板再正确也没有数据。
    # 停启逻辑集中在 app.observability.wiring（可单测），此处只做调用与兜底。
    try:
        from app.observability.wiring import start_obs_export

        await start_obs_export(app)
    except Exception as e:
        logger.warning(
            "obs_export_start_failed", error=str(e)
        )
        app.state.vm_pusher = None

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
            # 2026-09-28 接线审计修复：这两个服务只接受 `base_url`，此前传 `host=/port=`
            # 必然抛 TypeError，而本块在 try 内 → **整个 RAG 初始化从未成功**
            # （rag_service 恒为 None，检索链路静默缺失，且只在日志里留一行 warning）。
            embedding_svc = BgeM3MedicalService(
                base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}",
            )
            clip_svc = UltrasoundClipService(
                base_url=(
                    f"http://{settings.ultrasound_clip_host}:{settings.ultrasound_clip_port}"
                ),
            )
            reranker_svc = BgeRerankerService(
                base_url=f"http://{settings.bge_reranker_host}:{settings.bge_reranker_port}",
                model=settings.bge_reranker_model,
                timeout_seconds=settings.bge_reranker_timeout_seconds,
                health_check_timeout_seconds=settings.bge_reranker_health_check_timeout_seconds,
            )

            # P0-RAG-01 fix: 注入真实的 AsyncSessionLocal。
            # Shared singleton so the retriever's sparse leg and the BM25
            # fallback path operate on the same in-memory index instance.
            #
            # 2026-09-28 真机部署暴露两处接线错误（RAG 因此从未初始化成功，
            # 启动期只留一行 `rag_service_init_failed`）：
            #   1) `BM25Index` **不接受** `vocabulary=` —— 它的分词是模块内自带的
            #      确定性实现（CJK 连续串 + 字母数字切分，见 bm25_fallback 模块
            #      docstring），传这个 kwarg 直接 `TypeError`，整个 RAG 装配失败；
            #   2) `Retriever` 的 `session_factory` 没传 —— 它据此把检索命中的
            #      chunk 回表取正文，缺了它只会走 "placeholder content" 分支
            #      （`retriever.py` 里标注为"测试用"的那条），线上等于检索出空壳。
            shared_bm25_index = BM25Index(
                session_factory=AsyncSessionLocal,
            )
            # BM25 索引是**懒构建**的，而 `BM25Index.health_check()` 只看 `_built`
            # 是否就绪；生产链路此前从不调用 `ensure_built` → 真机上
            # `rag_health_partial_failure` 的 `bm25` 恒为 False、稀疏腿恒空
            # （2026-09-28：embedding/reranker 修好后仍剩 bm25=False）。
            # 启动期预热一次；知识库为空或构建失败都只告警，不阻断启动。
            try:
                await shared_bm25_index.ensure_built()
                logger.info(
                    "bm25_index_warmed_up",
                    ready=await shared_bm25_index.health_check(),
                )
            except Exception as exc:
                logger.warning("bm25_index_warmup_failed", error=str(exc))

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
                    # 必须注入：否则命中的 chunk 无法回表取正文，检索会退化成
                    # "placeholder content"（见上面 2026-09-28 的说明）。
                    session_factory=AsyncSessionLocal,
                ),
                reranker=reranker_svc,
                heuristic_reranker=HeuristicReranker(),
                bm25_fallback=BM25Fallback(
                    shared_bm25_index,
                    min_top1_score=settings.rag_bm25_min_top1_score,
                    min_results=settings.rag_bm25_min_results,
                ),
                # 当前激活 KB 版本（默认回退链的源头；换 KB 走 .env
                # RAG_KB_VERSION，不再依赖 mock 占位配置）
                kb_version=settings.rag_kb_version,
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
                logger.info("rag_service_health_check", healthy=healthy)
            except Exception as e:
                logger.warning(
                    "rag_service_health_check_failed", error=str(e)
                )
            app.state.rag_service = rag_service
        except Exception as e:
            logger.warning(
                "rag_service_init_failed", error=str(e)
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
    _logger = logger
    try:
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl
        from app.agents.specialist import build_dispatcher
        from app.inference.factory import create_inference_engine_from_settings

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
                async def query(self, *args, **kwargs):
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
                "inference_engine_init_failed", error=str(e)
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
            _logger.warning("dispatcher_health_check_failed", error=str(e))
        try:
            knowledge_ok = await knowledge_agent.health_check()
        except Exception as e:
            knowledge_ok = False
            _logger.warning("knowledge_agent_health_check_failed", error=str(e))
        try:
            monitor_ok = await monitor_agent.health_check()
        except Exception as e:
            monitor_ok = False
            _logger.warning("monitor_agent_health_check_failed", error=str(e))
        _logger.info(
            "agents_health_check",
            dispatcher=dispatcher_ok,
            knowledge_agent=knowledge_ok,
            monitor_agent=monitor_ok,
        )
    except Exception as e:
        _logger.warning("agents_init_failed", error=str(e))
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
        # P2-6 修复：注入 specialist_invoker（真实重生成）
        #
        # 2026-09-28 接线审计修复：此前用 `hasattr(dispatcher, "invoke_specialist")`
        # 与 `hasattr(dispatcher, "dispatch")` **探测**能力，而 `DispatcherAgentImpl`
        # 这两个方法都不存在 —— 于是 specialist_invoker 恒为 None，CRAG **永远**
        # 走"仅合并证据 +0.2"的占位分支（`fusion/crag.py:483`），"真实重生成"从未发生。
        #
        # crag 期望的形状是 `Callable[[AgentContext], Awaitable[AgentResponse]]`
        # （`crag.py:93 SpecialistInvoker`），而 `DispatcherAgentImpl.invoke(context)`
        # 与之完全匹配，因此改为**直接取入口**，不再做能力探测（探测失败是静默的）。
        knowledge_agent = getattr(app.state, "knowledge_agent", None)
        specialist_invoker = dispatcher.invoke if dispatcher is not None else None

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
        _logger.warning("fusion_engine_init_failed", error=str(e))
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
        logger.warning("worker_pool_stop_failed", error=str(e))
    # OBS: 停止基础设施巡检（置位 shutdown event → 等待 task 退出 → 兜底取消）
    try:
        import asyncio as _asyncio

        from app.observability.infra_health import stop_infra_health_checks

        await stop_infra_health_checks()
        infra_task = getattr(app.state, "infra_health_task", None)
        if infra_task is not None and not infra_task.done():
            await _asyncio.wait_for(infra_task, timeout=5.0)
    except Exception as e:
        logger.warning("infra_health_stop_failed", error=str(e))
        infra_task = getattr(app.state, "infra_health_task", None)
        if infra_task is not None and not infra_task.done():
            infra_task.cancel()
    # OBS: 停止 VictoriaMetrics 推送器（指标 HTTP server 由进程退出回收）
    try:
        from app.observability.wiring import stop_obs_export

        await stop_obs_export(app)
    except Exception as e:
        logger.warning(
            "obs_export_stop_failed", error=str(e)
        )
    # OBS: 追踪关闭（flush BatchSpanProcessor 后再退出，避免尾部 span 丢失）
    try:
        from app.observability.tracing import shutdown_tracing

        shutdown_tracing()
    except Exception as e:
        logger.warning("tracing_shutdown_failed", error=str(e))
    await safety_gateway_client.close()
    if consent_consumer:
        await consent_consumer.stop()
    # AGT 智能体显式关闭阶段（按 monitor → knowledge → dispatcher 顺序）：
    # 反向拓扑序关闭，先释放依赖方，再释放被依赖方，确保每个 close()
    # 看到的依赖状态都是有效的。
    _shutdown_logger = logger
    for agent_attr in ("monitor_agent", "knowledge_agent", "dispatcher"):
        agent = getattr(app.state, agent_attr, None)
        if agent is not None and hasattr(agent, "close"):
            try:
                await agent.close()
            except Exception as e:
                _shutdown_logger.warning(
                    "agents_close_failed", agent=agent_attr, error=str(e)
                )
    # 并发审查 P1：每个 close() 独立兜底——原先 inference/speech 的 close() 一旦
    # 抛错会中断整条 shutdown 链（后面的 fusion/rag/RedisPool.close 永不执行，
    # 等于每次异常重启泄漏整个 Redis 连接池 + DB 连接池）。
    for attr in ("inference_engine", "speech_engine"):
        engine = getattr(app.state, attr, None)
        if engine is not None:
            try:
                await engine.close()
            except Exception as e:
                logger.warning(f"{attr}_close_failed", error=str(e))
    if hasattr(app.state, "fusion_engine") and app.state.fusion_engine:
        try:
            await app.state.fusion_engine.close()
        except Exception as e:
            logger.warning(
                "fusion_engine_close_failed", error=str(e)
            )
    if hasattr(app.state, "rag_service") and app.state.rag_service:
        try:
            await app.state.rag_service.close()
        except Exception as e:
            logger.warning(
                "rag_service_close_failed", error=str(e)
            )
    # 并发审查 P0-6：停机时释放 SQLAlchemy 连接池。原实现只关 Redis，PG 连接池
    # 靠进程退出回收——滚动发布/多副本场景下残留连接会打满 PG max_connections。
    try:
        from app.config.database import async_engine

        await async_engine.dispose()
    except Exception as e:
        logger.warning("db_engine_dispose_failed", error=str(e))
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

# OBS: request_id 注入 —— 注册在所有中间件之后（成为最外层），为每个请求
# 生成/透传 X-Request-ID、写入 structlog contextvars、回填错误响应体
# （修复 ErrorResponse.request_id 恒 None 的断链，见 15-技术升级规划 P0-1.5）。
app.add_middleware(RequestIdMiddleware)

# OBS: FastAPI 自动埋点（服务端 span；依赖 obs_tracing_enabled，未启用时内部静默跳过）。
# 必须在全部 add_middleware 之后、include_router 之前调用。
try:
    from app.observability.tracing import instrument_fastapi

    instrument_fastapi(app)
except Exception as e:
    logger.warning("tracing_instrument_failed", error=str(e))

app.include_router(health_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api/v1/auth")
app.include_router(diagnosis_router, prefix="/api/v1/diagnosis")
app.include_router(session_router, prefix="/api/v1/sessions")
app.include_router(consent_router, prefix="/api/v1/consent")
app.include_router(device_router, prefix="/api/v1/admin/device")
app.include_router(feedback_router, prefix="/api/v1/feedback")
app.include_router(imaging_router, prefix="/api/v1/imaging")
# 结节分割（app/inference/nodule_segmentation.py；权重缺失时端点 503 + 原因）
# router 自带 prefix="/segmentation"，此处不再叠加，否则路径会变成
# /api/v1/segmentation/segmentation/*
app.include_router(segmentation_router, prefix="/api/v1")
app.include_router(speech_router, prefix="/api/v1/speech")
app.include_router(voice_router, prefix="/api/v1/voice")
app.include_router(security_router, prefix="/api/v1/security")
app.include_router(alert_admin_router, prefix="/api/v1/admin/alert")
app.include_router(obs_dashboard_router, prefix="/api/v1/internal/obs")
# 告警回调（Alertmanager / vmbackup）——此前配置指向该路径但路由不存在，回调一直 404
app.include_router(internal_alerts_router, prefix="/api/v1/internal/alerts")
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
