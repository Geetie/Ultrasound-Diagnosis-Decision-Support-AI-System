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
from typing import List, Optional

import structlog

from app.config.settings import settings
from app.knowledge.version import parse_kb_version
from app.rag.bm25_fallback import BM25Fallback
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

    # ------------------------------------------------------------------
    # KB 版本契约（知识查询智能体缓存键依赖）
    # ------------------------------------------------------------------
    #
    # ``app/main.py`` 构造 KnowledgeAgentImpl 时优先从 RAG 服务解析当前
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

        # Stage 1: query enhancement.
        enhanced: Optional[EnhancedQuery] = None
        if request.use_query_enhancement:
            try:
                enhanced = await self._enhancer.enhance(
                    request.query, organ_hint=request.organ
                )
            except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
            except Exception as exc:  # noqa: BLE001
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
            final_chunks: List[ScoredChunk] = []
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
        if request.min_score_threshold > 0:
            result.chunks = [
                sc for sc in result.chunks
                if (sc.rerank_score if sc.rerank_score is not None else sc.score)
                >= request.min_score_threshold
            ]
        return result

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
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag_health_retriever_failed", error=str(exc))
            results["embedding"] = False
            results["vector_index"] = False

        # Reranker.
        try:
            ok = await self._reranker.health_check()
            results["reranker"] = bool(ok)
        except Exception as exc:  # noqa: BLE001
            logger.warning("rag_health_reranker_failed", error=str(exc))
            results["reranker"] = False

        # BM25 index.
        try:
            ok = await self._bm25_fallback._index.health_check()
            results["bm25"] = bool(ok)
        except Exception as exc:  # noqa: BLE001
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
        except Exception as exc:  # noqa: BLE001
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
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "rag_close_failed",
                        obj=type(obj).__name__,
                        error=str(exc),
                    )
        try:
            res = self._bm25_fallback._index.close()
            if asyncio.iscoroutine(res):
                close_tasks.append(res)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "rag_close_failed",
                obj="BM25Index",
                error=str(exc),
            )
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)


__all__ = ["RAGServiceImpl"]
