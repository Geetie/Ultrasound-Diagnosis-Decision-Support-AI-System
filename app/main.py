import asyncio
import os

# ⚠️ 必须在任何 huggingface_hub / open_clip / transformers import 之前设离线
# 闸门。实测 2026-10-04：属性提示在请求路径上装载 CLIP 时向 huggingface.co 发
# HEAD 探测，网络不可达 → 5 次同步重试 → event loop 卡死 → 18000 端口无响应。
# 详见 app/config/settings.py 的 ai_offline_mode 注释。
if os.environ.get("AI_OFFLINE_MODE", "1") not in ("0", "false", "False"):
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import FileResponse

from app.api.v1.alert_admin import router as alert_admin_router
from app.api.v1.alerts import router as internal_alerts_router
from app.api.v1.annotations import router as annotations_router
from app.api.v1.attributes import router as attributes_router
from app.api.v1.auth import router as auth_router
from app.api.v1.consent import router as consent_router
from app.api.v1.device import router as device_router
from app.api.v1.diagnosis import router as diagnosis_router
from app.api.v1.errors import (
    AppBaseException,
    ServerErrorException,
    app_exception_handler,
    general_exception_handler,
    http_exception_handler,
)
from app.api.v1.eval_set import router as eval_set_router
from app.api.v1.feedback import router as feedback_router
from app.api.v1.fhir import router as fhir_router
from app.api.v1.health import router as health_router
from app.api.v1.imaging import router as imaging_router
from app.api.v1.obs_dashboard import router as obs_dashboard_router
from app.api.v1.patients import router as patients_router
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
        logger.warning("_detect_worker_count: 未预期异常，按原逻辑降级", exc_info=True)
        return 1


async def _warm_up_speech_engine(engine) -> None:
    """预热语音引擎（后台任务，失败只告警——懒加载语义保留）。"""
    try:
        await engine.initialize()
        logger.info("speech_engine_warmup_ok")
    except Exception as exc:
        logger.warning("speech_engine_warmup_failed", error=str(exc))


async def _build_lifespan_dispatcher(
    *,
    inference_engine,
    knowledge_agent,
    real_dependencies: bool,
):
    """lifespan 建 dispatcher 的唯一入口（A.88 配置接线③）。

    ## 为什么需要这一层

    `app/agents/specialist/factory.py` 里有两个建 dispatcher 的入口：
    `build_dispatcher`（不查库 → 器官阈值恒硬编码 0.85/0.88/0.92）与
    `build_dispatcher_with_thresholds`（先 `await load_organ_thresholds()`）。
    lifespan 此前用的是前者，因此 `organ_disease_thresholds` 表**从未被读过**
    ——"阈值可配置"是壳（《架构真相》§3.3）。

    ## 降级与留痕（两个失败点各自留真实原因）

    * 阈值加载失败 → `load_organ_thresholds` 内部已按 `DEFAULT_ORGAN_THRESHOLDS`
      降级并 warning；这里再把 `threshold_source` 的 `reason` 打进启动日志，
      让运维能区分"表里没数据/未配置"与"PG 连不上"；
    * 建 dispatcher 本身抛错 → 回落到 `build_dispatcher`（硬编码阈值），
      并留下**真实异常文本**（AGENTS.md 硬规则 8：不许一律写死一个 reason）。

    Args:
        inference_engine: 已创建的推理引擎（``None`` 表示初始化失败）。
        knowledge_agent: 知识查询智能体。
        real_dependencies: 是否具备真实依赖（RAG + inference）。为假时用
            占位 specialist，不查库（保持改动前的启动路径完全一致）。

    Returns:
        可用的 dispatcher 实例。
    """
    from app.agents.specialist import build_dispatcher, build_dispatcher_with_thresholds

    if not real_dependencies:
        # 依赖缺失（无 RAG 或 inference 初始化失败）→ 占位 specialist 兜底，
        # 与改动前的分支完全一致（不查库：没有意义，且会拖慢启动）。
        return build_dispatcher(
            inference_engine=None,
            knowledge_agent=None,
            use_placeholder_on_missing=True,
        )

    threshold_source: dict = {}
    try:
        dispatcher = await build_dispatcher_with_thresholds(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            threshold_source=threshold_source,
            use_placeholder_on_missing=False,
        )
        threshold_source.setdefault("source", "unknown")
        threshold_source.setdefault("reason", None)
        if threshold_source["source"] == "defaults":
            logger.warning(
                "organ_thresholds_degraded_to_hardcoded",
                reason=threshold_source["reason"],
                defaults={
                    "thyroid": 0.85,
                    "liver": 0.88,
                    "cardiac": 0.92,
                },
            )
        else:
            logger.info(
                "organ_thresholds_source",
                source=threshold_source["source"],
            )
        return dispatcher
    except Exception as exc:
        # 启动期兜底：任何建 dispatcher 的失败都要能降级启动（不是只接某个已知异常）。
        # 留痕在下面 warning 里（error + error_type + threshold_source 真实原因）。
        logger.warning(
            "dispatcher_threshold_init_failed_using_hardcoded",
            error=str(exc),
            error_type=type(exc).__name__,
            threshold_source=threshold_source.get("source"),
        )
        return build_dispatcher(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            use_placeholder_on_missing=False,
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.inference.factory import create_inference_engine_from_settings
    from app.services.safety_gateway_client import safety_gateway_client
    from app.speech.funasr import FunASROnnxEngine

    errors = validate_config()
    if errors:
        raise ConfigValidationError(errors)

    # A.88 配置接线①：生成引擎配置在**启动期**就自检，而不是等第一次判读才炸。
    # `vllm_require_model=True` + 空模型名会让每次 generate 抛 GENERATION_FAILED
    # （app/inference/vllm.py:155-164），医生侧只会看到"AI 不可用"。
    # 注意：这些错误**不**阻断启动（引擎可能是刚被 compact 覆盖过的 llama.cpp，
    # 或部署方故意留空后自行注入 model），但必须留下可读原因与两条出路。
    _inference_config_errors = settings.inference_config_errors()
    if _inference_config_errors:
        for _err in _inference_config_errors:
            logger.error("inference_config_invalid", reason=_err)

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
    # A.50：语音引擎启动预热。懒加载（只在首次 recognize 时 initialize）会让
    # /speech/health 在首次识别前恒报 engine_ready=false——p9 探针曾据此把
    # 语音链路整体记为部署缺口。模型缺失时只告警不阻断启动（首次 recognize
    # 仍会重试 initialize，保持既有懒加载语义）。引用存 app.state 防 GC（RUF006）。
    app.state.speech_warmup_task = asyncio.create_task(
        _warm_up_speech_engine(speech_engine)
    )

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

    # 判读性能：分割权重启动预热（后台，不阻塞 /health 就绪判定）。
    # 首次判读的分割冷启动实测 5.7s（权重加载 + CUDA kernel 首次编译/显存池
    # 分配），热身后 50ms —— 用户等 AI 分析的 33s 里这是最大单一收益点。
    # best-effort：预热失败不阻塞主服务（与上方 safety warmup 同惯例）；
    # 逐器官 try/except，thyroid/cardiac 有权重，liver 无权重如实跳过。
    async def _warmup_segmenters() -> None:
        import asyncio as _asyncio

        from app.inference.nodule_segmentation import NoduleSegmenter

        for organ in ("thyroid", "cardiac"):
            try:
                seg = NoduleSegmenter.get(organ)
                result = await _asyncio.to_thread(seg.warmup)
                if result.get("available"):
                    logger.info(
                        "segmenter_warmup_done",
                        organ=organ,
                        warmup_ms=result.get("warmup_ms"),
                    )
                else:
                    logger.warning(
                        "segmenter_warmup_skipped",
                        organ=organ,
                        reason=result.get("reason"),
                    )
            except Exception as e:
                logger.warning("segmenter_warmup_failed", organ=organ, error=str(e))

    app.state.segmenter_warmup_task = asyncio.create_task(_warmup_segmenters())

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

    # 02-5 链B：重启恢复熔断状态（Redis 快照；读失败/无快照保持 ACTIVE，
    # 不比现状差 —— "连续重启即永不熔断"缺口由此闭合）。
    from app.observability.cusum.persistence import restore_breaker_state

    try:
        await restore_breaker_state(breaker)
    except Exception as e:
        logger.warning("cusum_state_restore_failed", error=str(e))

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
    # 01-2：构造门只剩 rag_use_query_enhancement —— 原 `or rag_use_reranking`
    # 是恒真摆设（query_enhancement 默认 True），且该开关已从 settings 删除。
    # 精排服务照常装配；是否执行由 RetrievalProfile.use_reranking 逐请求决定。
    if settings.rag_use_query_enhancement:
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
                    logger.warning("lifespan._kb_version_resolver: 未预期异常，按原逻辑降级", exc_info=True)
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

        # dispatcher：经 `_build_lifespan_dispatcher` 注入 inference + knowledge
        # **并真的从 DB 读器官阈值**（A.88 配置接线③）。
        # 依赖任一缺失时自动用占位 specialist 兜底（dispatcher 仍可启动）。
        # 知识查询接口 ``app.agents.protocols.KnowledgeAgent`` 是 ABC，
        # 我们的占位 ``_KnowledgeUnavailable`` 没有显式继承，但提供同名
        # ``query`` / ``health_check`` 协程 → 满足 duck-typing 契约；
        # 工厂在 knowledge_agent 异常时也会回退到占位路径。
        #
        # 改动前这里是直接调 `build_dispatcher(...)`（不查库）→
        # `organ_disease_thresholds` 表从未被读过，器官阈值恒为硬编码
        # 0.85/0.88/0.92。lifespan 本来就是 async，`build_dispatcher_with_thresholds`
        # 也是 async，所以这是最直接的一处真接线；失败降级到硬编码并留真实原因。
        dispatcher = await _build_lifespan_dispatcher(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            real_dependencies=(
                isinstance(knowledge_agent, KnowledgeAgentImpl)
                and inference_engine is not None
            ),
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
            SelfRAGEvaluator(
                inference_engine=inference_engine,
                # A.51 降载：评估重试与 CRAG 兜底功能重叠，默认 0（单次评估）。
                # 失败仍走 CRAG 重生成 + needs_review，临床语义不变。
                max_retries=int(getattr(settings, "self_rag_max_retries", 0)),
                retry_delay=float(getattr(settings, "self_rag_retry_delay_s", 0.0)),
            )
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
            # A.51：validate 级预算（超时 → needs_review 优雅降级）
            validate_budget_s=float(getattr(settings, "pipeline_validate_budget_s", 0.0)),
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
    # 分割预热任务若仍在跑（权重加载很慢时），取消后再退出，避免悬挂警告
    warmup_task = getattr(app.state, "segmenter_warmup_task", None)
    if warmup_task is not None and not warmup_task.done():
        warmup_task.cancel()

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
# 评测集进度（只读；数据来自 scripts/langfuse/collect_eval_set.py 的报告文件）
app.include_router(eval_set_router, prefix="/api/v1/eval-set")
app.include_router(imaging_router, prefix="/api/v1/imaging")
app.include_router(annotations_router, prefix="/api/v1/images")
# 结节分割（app/inference/nodule_segmentation.py；权重缺失时端点 503 + 原因）
# router 自带 prefix="/segmentation"，此处不再叠加，否则路径会变成
# /api/v1/segmentation/segmentation/*
app.include_router(segmentation_router, prefix="/api/v1")
# 属性提示（app/inference/attribute_hints.py；ULC 文本塔 zero-shot，仅 6/9 维）
app.include_router(attributes_router, prefix="/api/v1")
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
# A.51：患者列表/检索（走查实证缺失的端点；路由自带 /patients 路径）
app.include_router(patients_router, prefix="/api/v1")
app.include_router(report_router, prefix="/api/v1/report")

app.add_exception_handler(AppBaseException, app_exception_handler)
app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(Exception, general_exception_handler)

_dashboard_dir = Path(__file__).parent / "static" / "dashboard"
if _dashboard_dir.is_dir():
    app.mount("/dashboard", StaticFiles(directory=str(_dashboard_dir), html=True), name="dashboard")


# 临床前端（React + Cornerstone3D，2026-10-04 交付部署）
# 产物在 app/static/ui/（由 `cd frontend && VITE_BASE=/ui/ npm run build` 生成，
# 仓库随包分发——与 app/static/dashboard 同一惯例）。
#
# 为什么不直接 mount("/ui")：Starlette 按**注册顺序**匹配路由，mount("/ui") 一旦
# 先注册就会吃掉所有 /ui/*，SPA 回落路由永远匹配不到（实测 /ui/workspace 全 404）。
# 所以拆成：静态资源挂 /ui/assets（精确路径），/ui/{path} 由回落路由接管。
_ui_dir = Path(__file__).parent / "static" / "ui"
if _ui_dir.is_dir() and (_ui_dir / "index.html").is_file():
    # 只挂 assets 目录（打包产物都在这里）；其余路径走下面的 SPA 回落，
    # 那里会先判断是不是真实文件（favicon.svg 等根级静态文件也能取到）。
    _ui_assets = _ui_dir / "assets"
    if _ui_assets.is_dir():
        app.mount(
            "/ui/assets",
            StaticFiles(directory=str(_ui_assets)),
            name="ui-assets",
        )

    @app.get("/ui/{spa_path:path}", include_in_schema=False)
    async def ui_spa_fallback(spa_path: str):
        """SPA history 路由回落：非静态文件路径一律交回 index.html。

        医生在 /ui/workspace、/ui/report/xxx 这类前端路由上**刷新页面**是常态，
        静态目录里没有对应文件 → 必须回 index.html，否则一刷新就 404。
        """
        # 防目录穿越：解析后必须仍落在 ui 目录内
        candidate = (_ui_dir / spa_path).resolve()
        if candidate.is_file() and str(candidate).startswith(str(_ui_dir.resolve())):
            return FileResponse(str(candidate))
        return FileResponse(str(_ui_dir / "index.html"))

    @app.get("/ui", include_in_schema=False)
    async def ui_index():
        return FileResponse(str(_ui_dir / "index.html"))

else:  # pragma: no cover - 未构建时给出可执行的提示而非 404 谜语
    @app.get("/ui", include_in_schema=False)
    async def ui_not_built():
        raise ServerErrorException(
            error_message=(
                "临床前端未构建：在 frontend/ 执行 "
                "`VITE_BASE=/ui/ npm ci && VITE_BASE=/ui/ npm run build`，"
                "再把 dist/ 内容复制到 app/static/ui/"
            ),
        )


@app.get("/")
async def root():
    return {"message": "Ultrasound Diagnosis Decision Support AI System"}
