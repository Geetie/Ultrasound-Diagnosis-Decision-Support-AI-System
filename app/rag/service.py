"""T-GA-RAG-01/02/03: RAG service orchestrator.

Composes the full RAG pipeline:

    enhance (QueryEnhancer) -> retrieve (Retriever) -> rerank (RerankService)
    -> bm25_fallback (BM25Fallback, conditional)

P99 latency is observed against settings.rag_p99_warning_threshold_ms
and settings.rag_p99_critical_threshold_ms; violations are logged.

The service is intentionally constructed with explicit dependencies
(no module-level singletons) so it can be assembled in tests with
mocks and in production via the FastAPI lifespan.
"""
from __future__ import annotations

import asyncio
import time

import structlog

from app.config.settings import settings
from app.knowledge.authority import rank_indices as authority_rank_indices
from app.knowledge.version import parse_kb_version
from app.rag.bm25_fallback import BM25Fallback
from app.rag.cache import RetrievalCache
from app.rag.protocols import (
    EnhancedQuery,
    RAGError,
    RAGErrorCode,
    RAGService,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)
from app.rag.query_enhancer import QueryEnhancer
from app.rag.reranker import HeuristicReranker, RerankError, RerankService
from app.rag.retriever import Retriever

logger = structlog.get_logger("app.rag.service")


def _comparison_score(sc: ScoredChunk) -> float:
    """取一条命中**用于阈值判定**的分数。

    有 rerank 分就用 rerank 分（cross-encoder，0~1），否则用 ``score``
    （RRF 融合分或 BM25 分）。这是 A.88 A1 之前就存在的取分口径，抽成函数
    只是为了让"判据"与"参考最大值"用**同一把尺子**（两处各写一遍必然走散）。
    """
    return sc.rerank_score if sc.rerank_score is not None else sc.score


def _threshold_mode(n_absolute: int, n_relative: int) -> str:
    """本批阈值实际生效的量纲（写进 ``min_score_filter_applied`` 留痕）。"""
    if n_absolute and n_relative:
        return "mixed"
    return "relative" if n_relative else "absolute"


class RAGServiceImpl(RAGService):
    """Concrete RAG service.

    Args:
        query_enhancer: T-GA-RAG-01 query enhancer.
        retriever: T-GA-RAG-02 multi-modal retriever.
        reranker: T-GA-RAG-03 cross-encoder reranker (bge-reranker).
        heuristic_reranker: T-GA-RAG-03 deterministic fallback reranker.
        bm25_fallback: T-GA-RAG-03 BM25 fallback policy.
        rerank_output_top_k: How many chunks the rerank step keeps.
        p99_warning_ms: Latency threshold for the warning log.
        p99_critical_ms: Latency threshold for the P99 violation flag.
    """

    def __init__(
        self,
        query_enhancer: QueryEnhancer,
        retriever: Retriever,
        reranker: RerankService,
        heuristic_reranker: HeuristicReranker,
        bm25_fallback: BM25Fallback,
        rerank_output_top_k: int | None = None,
        p99_warning_ms: float | None = None,
        p99_critical_ms: float | None = None,
        kb_version: str | None = None,
        cache: RetrievalCache | None = None,
    ):
        self._enhancer = query_enhancer
        self._retriever = retriever
        self._reranker = reranker
        self._heuristic_reranker = heuristic_reranker
        self._bm25_fallback = bm25_fallback
        self._rerank_output_top_k = (
            rerank_output_top_k
            if rerank_output_top_k is not None
            else settings.rag_rerank_output_top_k
        )
        self._p99_warning_ms = (
            p99_warning_ms
            if p99_warning_ms is not None
            else settings.rag_p99_warning_threshold_ms
        )
        self._p99_critical_ms = (
            p99_critical_ms
            if p99_critical_ms is not None
            else settings.rag_p99_critical_threshold_ms
        )
        self._kb_version = kb_version or settings.phase0a_mock_kb_version
        # 检索结果缓存（T-GA-RAG 缓存层）：None 表示不启用（行为与之前一致）。
        # 覆盖 fusion 的两条直连 RAG 路径（consistency / crag），
        # 它们绕过知识查询智能体的两层缓存。
        self._cache = cache

    @staticmethod
    def _request_has_image_bytes(request: RetrievalRequest) -> bool:
        """本次请求是否**可能**带影像腿输入（显式字节或请求作用域槽）。

        缓存正确性的前置判断（A.88 T4-①）：返回 True 时整条缓存旁路。
        作用域槽的查询是纯内存读（有 TTL），不产生 IO。
        """
        if request.image_data is not None:
            return True
        try:
            from app.rag.image_leg import resolve_image_leg_slot

            slot = resolve_image_leg_slot(request.request_id)
        except Exception as exc:
            # 槽不可读（极端情况）→ 按"有影像"保守处理（旁路缓存比串号安全）。
            logger.warning(
                "image_leg_slot_lookup_failed_treating_as_present",
                request_id=request.request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return True
        return bool(slot is not None and slot.image_data is not None)

    # ------------------------------------------------------------------
    # KB 版本契约（知识查询智能体缓存键依赖）
    # ------------------------------------------------------------------ ``app/main.py`` 构造 KnowledgeAgentImpl 时优先从 RAG 服务解析当前
    # KB 版本（见 main.py 中的 ``_kb_version_resolver`` 四级回退）；KB 版本
    # 变更会嵌入 ``KnowledgeAgentImpl._make_cache_key``，实现缓存自然失效。
    # 因此 RAG 服务必须暴露这两个方法。

    def current_kb_version(self) -> str:
        """返回当前知识库版本（形如 ``kb_v1.0.0``）。"""
        return self._kb_version

    def update_kb_version(self, new_version: str) -> bool:
        """切换知识库版本，返回是否真的发生了变更。

        非法格式（不符合 ``kb_v{major}.{minor}.{patch}``）、空字符串、
        或与当前版本相同 → 返回 ``False`` 且不改动。
        """
        if not new_version or parse_kb_version(new_version) is None:
            return False
        if new_version == self._kb_version:
            return False
        self._kb_version = new_version
        return True

    # ------------------------------------------------------------------
    # RAGService interface
    # ------------------------------------------------------------------

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        start = time.monotonic()

        # A.88 T4-①（缓存正确性，**必须与影像腿接线一起做**）：
        # 影像腿的字节现在可能来自**请求作用域**（``app/rag/image_leg.py`` 的注入槽
        # / ContextVar），而 ``request.image_data`` 仍是 None。
        # ``RetrievalCache.is_cacheable`` 只看得见 ``request.image_data``，于是会把
        # 这类请求当普通文本检索缓存 —— 缓存键里**没有** session/影像，一旦影像腿
        # 真的贡献了 chunk，同一句 query 就会把 A 会话的检索结果发给 B 会话
        # （A.78 病人隔离红线）。所以：带影像腿输入 → **读与写都旁路缓存**，
        # 与 ``cache.py`` 既有的"image_data 非空不缓存"同口径。
        image_leg_active = self._request_has_image_bytes(request)

        # Stage 0: 缓存查询（命中则跳过 enhance/retrieve/rerank/fallback 全流水线）
        cache_key: str | None = None
        if (
            self._cache is not None
            and not image_leg_active
            and self._cache.is_cacheable(request)
        ):
            cache_key = self._cache.make_key(request, self._kb_version)
            cached = await self._cache.get(cache_key)
            if cached is not None:
                return self._finalize_cache_hit(cached, request, start)
        elif image_leg_active and self._cache is not None:
            logger.info(
                "rag_cache_bypassed_image_leg",
                request_id=request.request_id,
                organ=request.organ,
            )

        # Stage 1: query enhancement.
        # T-GB-AGT-02：``use_llm_enhancement`` 决定是否允许 LLM 改写查询，
        # 使"标准路径不调用 LLM"这一档位差异真正生效。
        enhanced: EnhancedQuery | None = None
        if request.use_query_enhancement:
            try:
                enhanced = await self._enhancer.enhance(
                    request.query,
                    organ_hint=request.organ,
                    use_llm=request.use_llm_enhancement,
                )
            except Exception as exc:
                logger.warning(
                    "query_enhancement_failed_using_raw",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                enhanced = None
        query_for_retrieval = enhanced.enhanced if enhanced else request.query

        # Stage 2: retrieval.
        try:
            result = await self._retriever.retrieve(
                request, enhanced_query=query_for_retrieval
            )
        except Exception as exc:
            logger.error(
                "retrieval_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                request_id=request.request_id,
            )
            raise RAGError(
                error_code=RAGErrorCode.RETRIEVAL_FAILED,
                message=f"retrieval failed: {exc}",
                detail=type(exc).__name__,
            ) from exc

        # Stage 3: reranking.
        rerank_used = "none"
        if request.use_reranking and result.has_results:
            candidates = [sc.chunk for sc in result.chunks]
            try:
                reranked = await self._reranker.rerank(
                    query=query_for_retrieval,
                    chunks=candidates,
                    top_k=self._rerank_output_top_k,
                )
                rerank_used = "bge"
            except RerankError as exc:
                logger.warning(
                    "rerank_failed_using_heuristic",
                    error=str(exc),
                    detail=exc.detail,
                )
                reranked = await self._heuristic_reranker.rerank(
                    query=query_for_retrieval,
                    chunks=candidates,
                    top_k=self._rerank_output_top_k,
                )
                rerank_used = "heuristic"
            except Exception as exc:
                logger.warning(
                    "rerank_unexpected_using_heuristic",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                reranked = await self._heuristic_reranker.rerank(
                    query=query_for_retrieval,
                    chunks=candidates,
                    top_k=self._rerank_output_top_k,
                )
                rerank_used = "heuristic"

            # Preserve original retrieval_source from the input chunks when
            # possible (look up by chunk_id), otherwise mark as HYBRID.
            source_by_id = {sc.chunk.chunk_id: sc.retrieval_source for sc in result.chunks}
            final_chunks: list[ScoredChunk] = []
            for sc in reranked:
                src = source_by_id.get(sc.chunk.chunk_id, sc.retrieval_source)
                final_chunks.append(
                    ScoredChunk(
                        chunk=sc.chunk,
                        score=sc.rerank_score if sc.rerank_score is not None else sc.score,
                        rerank_score=sc.rerank_score,
                        retrieval_source=src,
                    )
                )
            result.chunks = final_chunks
        else:
            # No rerank: just keep top rerank_output_top_k.
            result.chunks = result.chunks[: self._rerank_output_top_k]

        # Stage 4: BM25 fallback (if enabled and triggered).
        if request.use_bm25_fallback and self._bm25_fallback.should_trigger(result):
            reason = self._bm25_fallback.trigger_reason(result)
            logger.info(
                "bm25_fallback_triggered",
                reason=reason,
                request_id=request.request_id,
            )
            fallback_chunks = await self._bm25_fallback.fallback_search(
                query=query_for_retrieval,
                top_k=self._rerank_output_top_k,
                organ=request.organ,
            )
            if fallback_chunks:
                result.chunks = fallback_chunks
                result.retrieval_method = RetrievalMethod.BM25_FALLBACK
                result.triggered_bm25_fallback = True
            else:
                logger.info(
                    "bm25_fallback_no_results",
                    reason=reason,
                    request_id=request.request_id,
                )

        # Finalize: latency, metadata, P99 flag.
        latency_ms = (time.monotonic() - start) * 1000.0
        result.latency_ms = latency_ms
        if enhanced is not None:
            result.enhanced_query = enhanced.enhanced
            result.llm_enhanced = enhanced.llm_enhanced
        result.p99_violation = latency_ms > self._p99_critical_ms
        if result.p99_violation:
            logger.warning(
                "rag_p99_violation",
                request_id=request.request_id,
                latency_ms=round(latency_ms, 2),
                retrieval_method=result.retrieval_method.value,
                rerank=rerank_used,
                triggered_bm25_fallback=result.triggered_bm25_fallback,
                n_results=len(result.chunks),
            )
        elif latency_ms > self._p99_warning_ms:
            logger.info(
                "rag_p99_warning",
                request_id=request.request_id,
                latency_ms=round(latency_ms, 2),
                retrieval_method=result.retrieval_method.value,
                rerank=rerank_used,
                n_results=len(result.chunks),
            )

        # Apply min_score_threshold filter as the last step.
        #
        # A.63 批 WP1：**只对图谱来源显式豁免**。图谱邻居扩展
        # （`Retriever._expand_graph_neighbors`，`app/rag/retriever.py:247-306`）以
        # `score=0.0` + `retrieval_source=GRAPH` 追加：它承载的是"结构化补充证据"，
        # 不是"按相似度入选"，若一并按阈值过滤，开启图谱后邻居会被整批丢掉
        # （表现为"开了等于没开"，且日志里看不出原因）。
        # 这里不整体放宽阈值、也不给邻居伪造分数，只在过滤条件上排除图谱来源；
        # 剔除条数与豁免条数都留痕（固定事件名，便于探针/守卫读取）。
        # A.88 A1（量纲错配修复）：阈值的**解释**必须跟随分数实际所处的量纲。
        #
        # 缺陷：``min_score_threshold`` 默认 0.5（``app/rag/protocols.py:119``）是按
        # rerank（cross-encoder，0~1 相似度）口径定的；但 standard 档
        # （``use_reranking=False``）与 BM25 兜底路径的 ``score`` 是 **RRF** 融合分
        # （``Retriever._rrf_fuse``：1/(60+rank)，量级 0.016~0.033），拿 0.5 去比
        # 恒不成立 → 整批被剔空 → 一致性校验「与指南比」永远拿不到 chunk
        # （``app/fusion/consistency.py:837-863`` 的 ``no_guideline_chunks``）。
        #
        # 修法（按每条命中自身携带的量纲选判据，不改 rerank 路径语义）：
        #   * 该条有 ``rerank_score`` → **绝对**判据（阈值语义原样保留）；
        #   * 没有 ``rerank_score``（RRF / BM25_FALLBACK）→ **相对**判据
        #     ``score >= threshold * max_score_of_batch``（max 取本批**非图谱**项
        #     的 score 最大值；max <= 0 时整批保留）。相对判据保证"头部不会被
        #     一个来自另一种量纲的常数整批判死"，同时仍按同一比例裁掉长尾。
        # 图谱来源（``RetrievalMethod.GRAPH``）的豁免**不动**，见上段说明与
        # ``tests/unit/rag/test_service_graph_threshold.py`` 的钉子。
        if request.min_score_threshold > 0:
            non_graph = [
                sc for sc in result.chunks if sc.retrieval_source != RetrievalMethod.GRAPH
            ]
            reference_max = max((_comparison_score(sc) for sc in non_graph), default=0.0)
            kept: list[ScoredChunk] = []
            dropped_normal = 0
            graph_exempt = 0
            n_absolute = 0
            n_relative = 0
            relative_floor = request.min_score_threshold * reference_max
            for sc in result.chunks:
                if sc.retrieval_source == RetrievalMethod.GRAPH:
                    kept.append(sc)
                    graph_exempt += 1
                    continue
                score = _comparison_score(sc)
                if sc.rerank_score is not None:
                    # 有 rerank 分：绝对阈值，语义与修复前完全一致。
                    n_absolute += 1
                    passed = score >= request.min_score_threshold
                else:
                    # 无 rerank 分（RRF / BM25）：相对阈值；max<=0 时整批保留。
                    n_relative += 1
                    passed = reference_max <= 0.0 or score >= relative_floor
                if passed:
                    kept.append(sc)
                else:
                    dropped_normal += 1
            result.chunks = kept
            if graph_exempt or dropped_normal:
                logger.info(
                    "min_score_filter_applied",
                    request_id=request.request_id,
                    threshold=request.min_score_threshold,
                    # mode：本批阈值是按哪种量纲解释的（探针/守卫读这个字段）
                    mode=_threshold_mode(n_absolute, n_relative),
                    # 相对判据下的实际生效下界（absolute-only 时为 None）
                    relative_floor=round(relative_floor, 6) if n_relative else None,
                    graph_exempt=graph_exempt,
                    dropped=dropped_normal,
                    kept=len(kept),
                )

        # A.88 T4-④：知识来源**权威性评分**参与排序。
        #
        # 缺陷：《架构真相》§3.3 登记"知识来源权威性评分 **无实现**" —— 指南条文、
        # 教科书段落、既往病例报告被当同一类证据排序，引用顺序里看不出
        # "这条是 ACR 指南、那条是单个病例"。
        #
        # 修法（``app/knowledge/authority.py``）：按来源类型给可解释权重
        # （指南 1.00 > 教科书 0.95 > 文献 0.90 > 规程 0.85 > 病例报告 0.70；
        # 未知来源 1.00，**不拿缺失数据当证据扣分**），并按模式参与排序：
        #
        # * 默认 ``tie_break``：只在**分数完全相同**的相邻段内按权重重排。
        #   等权 RRF 浮点分几乎不撞 → 对现有临床输出近乎无影响（这是刻意的：
        #   先让能力真实存在且可审计，改变结论顺序必须单独过评测）；
        # * ``weighted``：``score * weight`` 整体重排 —— **会改变临床输出**，
        #   必须显式开 ``request.extra["authority_ranking"]="weighted"``，
        #   开启前应先跑检索评测（hit@10 基线见 16 号附录 A.65）。
        #
        # 权限位在 ``request.extra`` 而不是新增 settings 字段：本任务的写权限
        # 不含 ``app/config/settings.py``，而"按请求选档"本来就比全局开关更合适
        # （可灰度、可按调用方区分）。留痕 ``authority_ranking_applied`` 带模式、
        # 来源分布与次序变动条数 —— 不做"静默重排"。
        authority_mode = str(
            (request.extra or {}).get("authority_ranking", "tie_break")
        ).strip().lower()
        if result.chunks and authority_mode != "off":
            try:
                outcome = authority_rank_indices(
                    [sc.chunk for sc in result.chunks],
                    [_comparison_score(sc) for sc in result.chunks],
                    mode=authority_mode,
                )
                if outcome.changed:
                    result.chunks = [result.chunks[i] for i in outcome.order]
                logger.info(
                    "authority_ranking_applied",
                    request_id=request.request_id,
                    mode=outcome.mode,
                    changed=outcome.changed,
                    source_kinds=outcome.breakdown,
                    kept=len(result.chunks),
                )
            except Exception as exc:
                # 排序增强失败 → **保持原顺序**（不阻断检索，但必须留痕：
                # 静默降级会让"权威性没生效"永远查不出来）。
                logger.warning(
                    "authority_ranking_failed",
                    request_id=request.request_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )

        # A.88 T4-④：分级下限评估（**默认不生效**，但必须真的被调用）。
        #
        # 缺陷：《架构真相》§3.3 登记「分级下限 **无实现**」。
        #
        # 修法：``app/knowledge/grading_floor.py`` 提供显式规则表 +
        # ``apply_grading_floor``（只抬不降、解析不出等级不猜）。这里在**每次检索**
        # 都评估一次并留痕 —— 所以"能力被生产路径调用"是可验证的，而
        # ``GRADING_FLOOR_ENABLED`` 默认 False + 规则表当前为空 ⇒ **临床输出零变化**。
        #
        # 为什么挂在 RAG 服务而不是专科结论处：本任务的写权限不含
        # ``app/agents/specialist/base.py``（专科落分级的地方）。挂在检索边界的好处
        # 是三条检索入口（知识智能体 / 一致性 / CRAG）都经过这里，留痕完整；
        # 代价是**分级要由调用方提供**（``request.extra["report_grading"]``），
        # 而目前没有调用方提供 ⇒ 默认路径的评估结果是 ``disabled``。
        # 要让规则真正作用于报告，需要（1）开启开关 （2）在规则表登记经专家复核
        # 的条目 （3）结论侧把分级传进 extra —— 三件事都属**临床输出变更**，
        # 必须单独立项与评测，不在本轮范围内。
        try:
            from app.knowledge.grading_floor import apply_grading_floor

            floor_outcome = apply_grading_floor(
                organ=request.organ,
                grading=str((request.extra or {}).get("report_grading") or "") or None,
            )
            logger.info(
                "grading_floor_evaluated",
                request_id=request.request_id,
                organ=request.organ,
                applied=floor_outcome.applied,
                reason=floor_outcome.reason or None,
                original_grading=floor_outcome.original_grading,
                grading=floor_outcome.grading,
            )
        except Exception as exc:
            # 规则评估失败 → 绝不阻断检索（它本身是"默认不生效"的增强项），
            # 但必须留痕：静默吞掉会让"下限规则坏了"永远查不出来。
            logger.warning(
                "grading_floor_evaluation_failed",
                request_id=request.request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # A.88 T4-⑤：RAG 三指标（precision / recall / nDCG）的**真实生产者**。
        #
        # 缺陷：《架构真相》§3.3 登记这三项"**无生产者**"—— 规格里有、指标定义
        # 里没有、代码里零写入点，看板永远读不到。
        #
        # 诚实口径（这是本项的关键，不能偷懒）：三项指标**必须有 ground truth**
        # 才算得出来。生产检索请求没有 ground truth，用"检索到的结果"当自己的
        # 真值是循环证明（→ 恒为 1.0 的假指标）。所以：
        #   * 调用方（评测脚本 / 离线回放）经 ``request.extra["relevant_chunk_ids"]``
        #     提供真值 → 真算真写（``ultrasound_retrieval_quality``）；
        #   * 没提供 → 写 ``not_measured_no_ground_truth`` 计数，**绝不 set(0.0)**
        #     （那会让"召回率为 0"与"从没测过"混成一件事）。
        # 两种情况都由同一个生产调用点覆盖，所以"生产者存在且被调用"可验证。
        try:
            from app.observability.metrics_collector.rag_quality import (
                REASON_NO_GROUND_TRUTH,
                evaluate_and_publish,
                report_unavailable,
            )

            relevant_ids = (request.extra or {}).get("relevant_chunk_ids")
            if relevant_ids:
                evaluate_and_publish(
                    [sc.chunk.chunk_id for sc in result.chunks],
                    relevant_ids,
                    k=self._rerank_output_top_k,
                )
            else:
                report_unavailable(REASON_NO_GROUND_TRUTH)
        except Exception as exc:
            # 观测链路故障不得影响检索返回。
            logger.warning(
                "rag_quality_metrics_failed",
                request_id=request.request_id,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        # Stage 5: 写缓存。缓存"阈值过滤后的最终结果"仍然安全，原因有两条：
        #  ① ``min_score_threshold`` 与各 ``use_*`` 开关都已在缓存键中；
        #  ② A.88 起阈值语义依赖"分数所处的量纲"，故键里额外嵌入了
        #     ``MIN_SCORE_FILTER_SEMANTICS``（见 ``app/rag/cache.py``）：语义一变
        #     旧键即自然失效，不会把"旧绝对判据剔空的空结果"继续喂给调用方。
        if cache_key is not None:
            await self._cache.set(cache_key, result)

        return result

    def _finalize_cache_hit(
        self,
        cached: RetrievalResult,
        request: RetrievalRequest,
        start: float,
    ) -> RetrievalResult:
        """命中缓存的收尾：用本次实际耗时覆盖延迟字段。

        ``RetrievalCache.get`` 的返回值总是 JSON 反序列化出的**新对象**，
        与缓存内部不共享引用，因此这里无需深拷贝即可安全返回/被调用方修改。
        """
        latency_ms = (time.monotonic() - start) * 1000.0
        cached.latency_ms = latency_ms
        cached.p99_violation = latency_ms > self._p99_critical_ms
        logger.info(
            "rag_cache_hit",
            request_id=request.request_id,
            organ=request.organ,
            latency_ms=round(latency_ms, 3),
            n_results=len(cached.chunks),
        )
        return cached

    def cache_stats(self) -> dict | None:
        """缓存命中统计（未启用缓存时返回 None）。"""
        return self._cache.stats() if self._cache is not None else None

    async def health_check(self) -> bool:
        """Probe all dependencies. Logs (does not raise) on failures.

        Returns True only if every probe succeeds.
        """
        results: dict[str, bool] = {}

        # Retriever (embedding + vector index) via public health_check.
        try:
            retriever_health = await self._retriever.health_check()
            results["embedding"] = retriever_health.get("embedding", False)
            results["vector_index"] = retriever_health.get("vector_index", False)
        except Exception as exc:
            logger.warning("rag_health_retriever_failed", error=str(exc))
            results["embedding"] = False
            results["vector_index"] = False

        # Reranker.
        try:
            ok = await self._reranker.health_check()
            results["reranker"] = bool(ok)
        except Exception as exc:
            logger.warning("rag_health_reranker_failed", error=str(exc))
            results["reranker"] = False

        # BM25 index.
        try:
            ok = await self._bm25_fallback._index.health_check()
            results["bm25"] = bool(ok)
        except Exception as exc:
            logger.warning("rag_health_bm25_failed", error=str(exc))
            results["bm25"] = False

        all_ok = all(results.values())
        if not all_ok:
            logger.warning("rag_health_partial_failure", **results)
        return all_ok

    async def close(self) -> None:
        """Close all resources."""
        close_tasks = []
        # Close retriever via public close method.
        try:
            res = self._retriever.close()
            if asyncio.iscoroutine(res):
                close_tasks.append(res)
        except Exception as exc:
            logger.warning(
                "rag_close_failed",
                obj="Retriever",
                error=str(exc),
            )
        # Close other components.
        for obj in (
            self._reranker,
            self._heuristic_reranker,
        ):
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    res = close()
                    if asyncio.iscoroutine(res):
                        close_tasks.append(res)
                except Exception as exc:
                    logger.warning(
                        "rag_close_failed",
                        obj=type(obj).__name__,
                        error=str(exc),
                    )
        try:
            res = self._bm25_fallback._index.close()
            if asyncio.iscoroutine(res):
                close_tasks.append(res)
        except Exception as exc:
            logger.warning(
                "rag_close_failed",
                obj="BM25Index",
                error=str(exc),
            )
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)


__all__ = ["RAGServiceImpl"]
