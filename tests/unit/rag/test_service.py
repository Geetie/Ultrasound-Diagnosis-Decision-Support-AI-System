"""Unit tests for app.rag.service (T-GA-RAG-01/02/03 orchestrator).

Covers RAGServiceImpl:
- retrieve() pipeline: enhance → retrieve → rerank → bm25_fallback
- Reranker failure → HeuristicReranker fallback
- BM25 fallback trigger / no-result handling
- Query enhancement failure → raw query fallback
- Retrieval exception → RAGError(RETRIEVAL_FAILED)
- min_score_threshold filtering
- P99 violation / warning
- Latency measurement
- use_reranking=False / use_bm25_fallback=False
- retrieval_source preservation after rerank
- health_check: full / partial / all-fail
- close: dependency cleanup (resilient to individual close failures)

All dependencies are mocked with AsyncMock / MagicMock; no real DB / HTTP.
"""
from __future__ import annotations

import asyncio
import time
from typing import List, Optional
from unittest.mock import AsyncMock, MagicMock, call

import pytest

pytestmark = [pytest.mark.track_rag]

from app.knowledge.protocols import KnowledgeChunk
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
from app.rag.service import RAGServiceImpl


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _mk_chunk(
    chunk_id: str,
    organ: str = "thyroid",
    content: str = "x",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ=organ,
        content=content,
        metadata={},
        embedding=None,
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )


def _mk_scored(
    chunk_id: str,
    score: float,
    source: RetrievalMethod = RetrievalMethod.DENSE,
) -> ScoredChunk:
    return ScoredChunk(
        chunk=_mk_chunk(chunk_id),
        score=score,
        rerank_score=None,
        retrieval_source=source,
    )


def _mk_enhanced(
    original: str = "x",
    enhanced: str = "x expanded",
    organ: Optional[str] = "thyroid",
    llm_enhanced: bool = False,
) -> EnhancedQuery:
    return EnhancedQuery(
        original=original,
        enhanced=enhanced,
        organ=organ,
        layer1_synonyms=["a"],
        layer2_hypernyms=["b"],
        layer3_related=["c"],
        llm_enhanced=llm_enhanced,
    )


def _build_service(
    *,
    rerank_error: Optional[Exception] = None,
    rerank_output: Optional[List[ScoredChunk]] = None,
    heuristic_output: Optional[List[ScoredChunk]] = None,
    retriever_output: Optional[RetrievalResult] = None,
    enhanced: Optional[EnhancedQuery] = None,
    bm25_should_trigger: bool = False,
    bm25_fallback_chunks: Optional[List[ScoredChunk]] = None,
    p99_warning_ms: float = 80.0,
    p99_critical_ms: float = 100.0,
    rerank_top_k: int = 5,
) -> RAGServiceImpl:
    """Construct a RAGServiceImpl with fully-mocked dependencies."""
    # QueryEnhancer mock
    enhancer = MagicMock(spec=QueryEnhancer, name="QueryEnhancer")
    enhancer.enhance = AsyncMock(
        return_value=enhanced if enhanced is not None else _mk_enhanced()
    )

    # Retriever mock
    retriever = MagicMock(spec=Retriever, name="Retriever")
    default_retrieval = RetrievalResult(
        chunks=[
            _mk_scored("c1", 0.9, RetrievalMethod.DENSE),
            _mk_scored("c2", 0.7, RetrievalMethod.DENSE),
            _mk_scored("c3", 0.5, RetrievalMethod.DENSE),
        ],
        retrieval_method=RetrievalMethod.HYBRID,
    )
    retriever.retrieve = AsyncMock(
        return_value=retriever_output if retriever_output is not None else default_retrieval
    )

    # BgeRerankerService mock
    reranker = MagicMock(spec=RerankService, name="BgeRerankerService")
    if rerank_error is not None:
        reranker.rerank = AsyncMock(side_effect=rerank_error)
    else:
        default_rerank = rerank_output if rerank_output is not None else [
            ScoredChunk(
                chunk=_mk_chunk("c2"),
                score=0.95,
                rerank_score=0.95,
                retrieval_source=RetrievalMethod.HYBRID,
            ),
            ScoredChunk(
                chunk=_mk_chunk("c1"),
                score=0.85,
                rerank_score=0.85,
                retrieval_source=RetrievalMethod.HYBRID,
            ),
        ]
        reranker.rerank = AsyncMock(return_value=default_rerank)
    reranker.health_check = AsyncMock(return_value=True)
    reranker.close = AsyncMock(return_value=None)

    # HeuristicReranker mock
    heuristic = MagicMock(spec=HeuristicReranker, name="HeuristicReranker")
    if heuristic_output is None:
        heuristic.rerank = AsyncMock(return_value=[
            ScoredChunk(
                chunk=_mk_chunk("c1"),
                score=0.8,
                rerank_score=0.8,
                retrieval_source=RetrievalMethod.HYBRID,
            ),
        ])
    else:
        heuristic.rerank = AsyncMock(return_value=heuristic_output)

    # BM25Fallback mock
    bm25_fb = MagicMock(spec=BM25Fallback, name="BM25Fallback")
    bm25_fb.should_trigger = MagicMock(return_value=bm25_should_trigger)
    bm25_fb.trigger_reason = MagicMock(return_value="ok" if not bm25_should_trigger else "no_results")
    bm25_fb.fallback_search = AsyncMock(
        return_value=bm25_fallback_chunks if bm25_fallback_chunks is not None else []
    )
    bm25_fb._index = MagicMock(name="BM25Index")
    bm25_fb._index.health_check = AsyncMock(return_value=True)
    bm25_fb._index.close = AsyncMock(return_value=None)

    return RAGServiceImpl(
        query_enhancer=enhancer,
        retriever=retriever,
        reranker=reranker,
        heuristic_reranker=heuristic,
        bm25_fallback=bm25_fb,
        rerank_output_top_k=rerank_top_k,
        p99_warning_ms=p99_warning_ms,
        p99_critical_ms=p99_critical_ms,
    )


# ----------------------------------------------------------------------
# RAGService interface
# ----------------------------------------------------------------------


class TestIsRAGService:
    def test_subclass(self):
        svc = _build_service()
        assert isinstance(svc, RAGService)


# ----------------------------------------------------------------------
# retrieve: happy path
# ----------------------------------------------------------------------


class TestHappyPath:
    async def test_returns_result_with_enhanced_and_reranked(self):
        svc = _build_service()
        req = RetrievalRequest(query="hypoechoic", organ="thyroid")
        result = await svc.retrieve(req)
        assert isinstance(result, RetrievalResult)
        assert result.enhanced_query == "x expanded"
        assert result.llm_enhanced is False
        assert len(result.chunks) > 0
        assert result.latency_ms >= 0.0

    async def test_calls_all_stages_in_order(self):
        svc = _build_service()
        call_order: List[str] = []

        async def track_enhance(*args, **kwargs):
            call_order.append("enhance")
            return _mk_enhanced()

        async def track_retrieve(*args, **kwargs):
            call_order.append("retrieve")
            return RetrievalResult(
                chunks=[_mk_scored("c1", 0.9)],
                retrieval_method=RetrievalMethod.HYBRID,
            )

        async def track_rerank(*args, **kwargs):
            call_order.append("rerank")
            return [ScoredChunk(chunk=_mk_chunk("c1"), score=0.9, rerank_score=0.9, retrieval_source=RetrievalMethod.HYBRID)]

        svc._enhancer.enhance = track_enhance
        svc._retriever.retrieve = track_retrieve
        svc._reranker.rerank = track_rerank

        await svc.retrieve(RetrievalRequest(query="x"))
        assert call_order == ["enhance", "retrieve", "rerank"]

    async def test_uses_enhanced_query_for_retrieval_and_rerank(self):
        svc = _build_service(enhanced=_mk_enhanced(enhanced="EXPANDED"))
        await svc.retrieve(RetrievalRequest(query="raw"))
        # retrieve() should have been called with the enhanced query
        retrieve_call = svc._retriever.retrieve.await_args
        assert retrieve_call.kwargs.get("enhanced_query") == "EXPANDED"
        rerank_call = svc._reranker.rerank.await_args
        assert rerank_call.kwargs.get("query") == "EXPANDED"

    async def test_rerank_output_top_k(self):
        # default rerank top_k=5 but we have only 3 chunks
        svc = _build_service()
        await svc.retrieve(RetrievalRequest(query="x"))
        kwargs = svc._reranker.rerank.await_args.kwargs
        assert kwargs["top_k"] == 5


# ----------------------------------------------------------------------
# retrieve: reranker failure
# ----------------------------------------------------------------------


class TestRerankFailure:
    async def test_rerank_error_falls_back_to_heuristic(self):
        svc = _build_service(
            rerank_error=RerankError("bge-reranker down", detail="timeout")
        )
        result = await svc.retrieve(RetrievalRequest(query="x"))
        # Heuristic was used
        svc._heuristic_reranker.rerank.assert_awaited_once()
        # rerank still got a result
        assert len(result.chunks) > 0

    async def test_unknown_exception_also_falls_back(self):
        svc = _build_service(rerank_error=RuntimeError("boom"))
        result = await svc.retrieve(RetrievalRequest(query="x"))
        svc._heuristic_reranker.rerank.assert_awaited_once()
        assert len(result.chunks) > 0


# ----------------------------------------------------------------------
# retrieve: BM25 fallback
# ----------------------------------------------------------------------


class TestBM25Fallback:
    async def test_fallback_triggered(self):
        fallback_chunks = [
            ScoredChunk(
                chunk=_mk_chunk("fc1"),
                score=0.8,
                rerank_score=None,
                retrieval_source=RetrievalMethod.BM25_FALLBACK,
            )
        ]
        svc = _build_service(
            bm25_should_trigger=True,
            bm25_fallback_chunks=fallback_chunks,
        )
        req = RetrievalRequest(query="x", use_bm25_fallback=True)
        result = await svc.retrieve(req)
        svc._bm25_fallback.should_trigger.assert_called_once()
        svc._bm25_fallback.fallback_search.assert_awaited_once()
        assert result.triggered_bm25_fallback is True
        assert result.retrieval_method == RetrievalMethod.BM25_FALLBACK
        assert result.chunks == fallback_chunks

    async def test_fallback_no_results_keeps_original(self):
        svc = _build_service(
            bm25_should_trigger=True,
            bm25_fallback_chunks=[],
        )
        result = await svc.retrieve(RetrievalRequest(query="x"))
        # Original retriever output is preserved
        assert result.chunks[0].chunk.chunk_id == "c2"  # from rerank default
        assert result.triggered_bm25_fallback is False
        assert result.retrieval_method == RetrievalMethod.HYBRID

    async def test_fallback_disabled_by_request(self):
        svc = _build_service(bm25_should_trigger=True)
        req = RetrievalRequest(query="x", use_bm25_fallback=False)
        await svc.retrieve(req)
        # should_trigger should not even be called
        svc._bm25_fallback.should_trigger.assert_not_called()


# ----------------------------------------------------------------------
# retrieve: query enhancement
# ----------------------------------------------------------------------


class TestQueryEnhancement:
    async def test_enhancement_disabled_uses_raw_query(self):
        svc = _build_service(enhanced=_mk_enhanced(enhanced="RAW_QUERY"))
        req = RetrievalRequest(query="RAW_QUERY", use_query_enhancement=False)
        await svc.retrieve(req)
        # enhancer.enhance not called
        svc._enhancer.enhance.assert_not_awaited()
        # retrieve called with raw query
        assert svc._retriever.retrieve.await_args.kwargs["enhanced_query"] == "RAW_QUERY"

    async def test_enhancement_exception_falls_back_to_raw(self):
        enhancer = MagicMock(spec=QueryEnhancer)
        enhancer.enhance = AsyncMock(side_effect=RuntimeError("udt crash"))
        retriever = MagicMock(spec=Retriever)
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(
                chunks=[_mk_scored("c1", 0.9)],
                retrieval_method=RetrievalMethod.HYBRID,
            )
        )
        reranker = MagicMock(spec=RerankService)
        reranker.rerank = AsyncMock(return_value=[
            ScoredChunk(chunk=_mk_chunk("c1"), score=0.9, rerank_score=0.9, retrieval_source=RetrievalMethod.HYBRID)
        ])
        reranker.health_check = AsyncMock(return_value=True)
        reranker.close = AsyncMock(return_value=None)
        heuristic = MagicMock(spec=HeuristicReranker)
        bm25_fb = MagicMock(spec=BM25Fallback)
        bm25_fb.should_trigger = MagicMock(return_value=False)

        svc = RAGServiceImpl(
            query_enhancer=enhancer,
            retriever=retriever,
            reranker=reranker,
            heuristic_reranker=heuristic,
            bm25_fallback=bm25_fb,
        )
        req = RetrievalRequest(query="raw_q")
        result = await svc.retrieve(req)
        # raw_q was used (enhancer raised → fallback)
        assert svc._retriever.retrieve.await_args.kwargs["enhanced_query"] == "raw_q"
        # enhanced_query NOT set
        assert result.enhanced_query is None

    async def test_llm_enhanced_propagates(self):
        svc = _build_service(enhanced=_mk_enhanced(llm_enhanced=True))
        result = await svc.retrieve(RetrievalRequest(query="x"))
        assert result.llm_enhanced is True


# ----------------------------------------------------------------------
# retrieve: retrieval failure
# ----------------------------------------------------------------------


class TestRetrievalFailure:
    async def test_retrieval_exception_raises_rag_error(self):
        svc = _build_service()
        svc._retriever.retrieve = AsyncMock(
            side_effect=RuntimeError("vector store down")
        )
        with pytest.raises(RAGError) as exc_info:
            await svc.retrieve(RetrievalRequest(query="x"))
        assert exc_info.value.error_code == RAGErrorCode.RETRIEVAL_FAILED
        assert "vector store down" in str(exc_info.value)


# ----------------------------------------------------------------------
# retrieve: min_score_threshold
# ----------------------------------------------------------------------


class TestMinScoreThreshold:
    async def test_filters_low_scores(self):
        # Rerank output: 0.9, 0.4, 0.2 — threshold 0.5 keeps only 0.9
        rerank_output = [
            ScoredChunk(chunk=_mk_chunk("c1"), score=0.9, rerank_score=0.9, retrieval_source=RetrievalMethod.HYBRID),
            ScoredChunk(chunk=_mk_chunk("c2"), score=0.4, rerank_score=0.4, retrieval_source=RetrievalMethod.HYBRID),
            ScoredChunk(chunk=_mk_chunk("c3"), score=0.2, rerank_score=0.2, retrieval_source=RetrievalMethod.HYBRID),
        ]
        svc = _build_service(rerank_output=rerank_output)
        req = RetrievalRequest(query="x", min_score_threshold=0.5)
        result = await svc.retrieve(req)
        ids = [c.chunk.chunk_id for c in result.chunks]
        assert "c1" in ids
        assert "c2" not in ids
        assert "c3" not in ids

    async def test_threshold_zero_no_filtering(self):
        svc = _build_service()
        req = RetrievalRequest(query="x", min_score_threshold=0.0)
        result = await svc.retrieve(req)
        assert len(result.chunks) == 2  # default rerank returns 2


# ----------------------------------------------------------------------
# retrieve: P99 measurement
# ----------------------------------------------------------------------


class TestP99:
    async def test_latency_recorded(self):
        svc = _build_service()
        result = await svc.retrieve(RetrievalRequest(query="x"))
        assert result.latency_ms > 0.0

    async def test_p99_violation_flag(self):
        # p99_critical=0 forces violation (latency always > 0)
        svc = _build_service(p99_critical_ms=0.0)
        result = await svc.retrieve(RetrievalRequest(query="x"))
        assert result.p99_violation is True

    async def test_p99_warning_threshold(self):
        # critical very high, warning very low → no violation, but warning fires
        svc = _build_service(
            p99_warning_ms=0.0,  # any latency > 0 triggers warning
            p99_critical_ms=1000.0,
        )
        result = await svc.retrieve(RetrievalRequest(query="x"))
        # Latency is positive but < critical → no violation
        assert result.p99_violation is False
        assert result.latency_ms >= 0.0


# ----------------------------------------------------------------------
# retrieve: use_reranking=False
# ----------------------------------------------------------------------


class TestUseRerankingFalse:
    async def test_skips_rerank(self):
        svc = _build_service()
        req = RetrievalRequest(query="x", use_reranking=False)
        result = await svc.retrieve(req)
        svc._reranker.rerank.assert_not_awaited()
        svc._heuristic_reranker.rerank.assert_not_awaited()
        # Top rerank_top_k chunks from retriever are kept
        assert len(result.chunks) <= 5


# ----------------------------------------------------------------------
# retrieve: retrieval_source preservation
# ----------------------------------------------------------------------


class TestRetrievalSourcePreservation:
    async def test_dense_source_preserved_through_rerank(self):
        # Retriever gives DENSE results; reranker should preserve via chunk_id lookup
        from app.rag.retriever import Retriever
        retriever_output = RetrievalResult(
            chunks=[
                _mk_scored("c1", 0.9, RetrievalMethod.DENSE),
                _mk_scored("c2", 0.8, RetrievalMethod.SPARSE),
            ],
            retrieval_method=RetrievalMethod.HYBRID,
        )
        rerank_output = [
            ScoredChunk(
                chunk=_mk_chunk("c1"),
                score=0.95,
                rerank_score=0.95,
                retrieval_source=RetrievalMethod.HYBRID,  # orchestrator will overwrite
            ),
            ScoredChunk(
                chunk=_mk_chunk("c2"),
                score=0.85,
                rerank_score=0.85,
                retrieval_source=RetrievalMethod.HYBRID,
            ),
        ]
        svc = _build_service(
            retriever_output=retriever_output,
            rerank_output=rerank_output,
        )
        result = await svc.retrieve(RetrievalRequest(query="x"))
        by_id = {c.chunk.chunk_id: c for c in result.chunks}
        assert by_id["c1"].retrieval_source == RetrievalMethod.DENSE
        assert by_id["c2"].retrieval_source == RetrievalMethod.SPARSE

    async def test_chunk_id_not_in_retriever_keeps_hybrid(self):
        retriever_output = RetrievalResult(
            chunks=[_mk_scored("c1", 0.9, RetrievalMethod.DENSE)],
            retrieval_method=RetrievalMethod.HYBRID,
        )
        # Reranker returns a chunk that was NOT in retriever
        rerank_output = [
            ScoredChunk(
                chunk=_mk_chunk("unknown"),
                score=0.95,
                rerank_score=0.95,
                retrieval_source=RetrievalMethod.HYBRID,
            ),
        ]
        svc = _build_service(
            retriever_output=retriever_output,
            rerank_output=rerank_output,
        )
        result = await svc.retrieve(RetrievalRequest(query="x"))
        # Unknown chunk keeps HYBRID (its own source from reranker output)
        assert result.chunks[0].retrieval_source == RetrievalMethod.HYBRID


# ----------------------------------------------------------------------
# retrieve: empty result paths
# ----------------------------------------------------------------------


class TestEmptyResult:
    async def test_retriever_no_results_no_rerank(self):
        svc = _build_service(
            retriever_output=RetrievalResult(
                chunks=[], retrieval_method=RetrievalMethod.HYBRID
            )
        )
        req = RetrievalRequest(query="x", use_bm25_fallback=False)
        result = await svc.retrieve(req)
        # Rerank not called (no candidates)
        svc._reranker.rerank.assert_not_awaited()
        assert result.chunks == []

    async def test_rerank_called_only_with_chunks(self):
        svc = _build_service(
            retriever_output=RetrievalResult(
                chunks=[], retrieval_method=RetrievalMethod.HYBRID
            )
        )
        await svc.retrieve(RetrievalRequest(query="x"))
        # Reranker.rerank must NOT be called with empty input
        svc._reranker.rerank.assert_not_awaited()


# ----------------------------------------------------------------------
# health_check
# ----------------------------------------------------------------------


class TestHealthCheck:
    async def test_all_healthy(self):
        svc = _build_service()
        svc._retriever.health_check = AsyncMock(
            return_value={"embedding": True, "vector_index": True}
        )
        svc._reranker.health_check = AsyncMock(return_value=True)
        svc._bm25_fallback._index.health_check = AsyncMock(return_value=True)
        assert await svc.health_check() is True

    async def test_embedding_unhealthy(self):
        svc = _build_service()
        svc._retriever.health_check = AsyncMock(
            return_value={"embedding": False, "vector_index": True}
        )
        svc._reranker.health_check = AsyncMock(return_value=True)
        svc._bm25_fallback._index.health_check = AsyncMock(return_value=True)
        assert await svc.health_check() is False

    async def test_one_raises_does_not_propagate(self):
        svc = _build_service()
        svc._retriever.health_check = AsyncMock(
            side_effect=RuntimeError("crash")
        )
        assert await svc.health_check() is False

    async def test_all_fail(self):
        svc = _build_service()
        svc._retriever.health_check = AsyncMock(
            side_effect=RuntimeError("x")
        )
        svc._reranker.health_check = AsyncMock(side_effect=RuntimeError("x"))
        svc._bm25_fallback._index.health_check = AsyncMock(
            side_effect=RuntimeError("x")
        )
        assert await svc.health_check() is False


# ----------------------------------------------------------------------
# close
# ----------------------------------------------------------------------


class TestClose:
    async def test_close_all_deps(self):
        svc = _build_service()
        svc._retriever.close = AsyncMock(return_value=None)
        svc._bm25_fallback._index.close = AsyncMock(return_value=None)
        await svc.close()
        svc._retriever.close.assert_awaited()
        svc._reranker.close.assert_awaited()
        svc._heuristic_reranker.close.assert_awaited()
        svc._bm25_fallback._index.close.assert_awaited()

    async def test_close_resilient_to_failures(self):
        svc = _build_service()
        svc._retriever.close = AsyncMock(
            side_effect=RuntimeError("retriever close fail")
        )
        svc._bm25_fallback._index.close = AsyncMock(return_value=None)
        await svc.close()
        svc._reranker.close.assert_awaited()
        svc._bm25_fallback._index.close.assert_awaited()

    async def test_close_with_sync_close(self):
        """Some close() methods are sync (return None)."""
        svc = _build_service()
        svc._retriever.close = MagicMock(return_value=None)
        svc._bm25_fallback._index.close = MagicMock(return_value=None)
        await svc.close()


# ----------------------------------------------------------------------
# chunk_id map: unknown chunk keeps its reranker source
# ----------------------------------------------------------------------


class TestEdgeCases:
    async def test_retrieve_with_request_id_passthrough(self):
        svc = _build_service()
        req = RetrievalRequest(query="x", request_id="req-1")
        result = await svc.retrieve(req)
        # The result is just a RetrievalResult; request_id is on the request
        assert isinstance(result, RetrievalResult)

    async def test_rerank_top_k_uses_setting_default(self):
        """If rerank_output_top_k is not passed, settings default is used."""
        enhancer = MagicMock(spec=QueryEnhancer)
        enhancer.enhance = AsyncMock(return_value=_mk_enhanced())
        retriever = MagicMock(spec=Retriever)
        retriever.retrieve = AsyncMock(
            return_value=RetrievalResult(
                chunks=[_mk_scored(f"c{i}", 0.9 - i * 0.1) for i in range(20)],
                retrieval_method=RetrievalMethod.HYBRID,
            )
        )
        reranker = MagicMock(spec=RerankService)
        reranker.rerank = AsyncMock(return_value=[
            ScoredChunk(chunk=_mk_chunk(f"c{i}"), score=0.5, rerank_score=0.5, retrieval_source=RetrievalMethod.HYBRID) for i in range(20)
        ])
        reranker.health_check = AsyncMock(return_value=True)
        reranker.close = AsyncMock(return_value=None)
        heuristic = MagicMock(spec=HeuristicReranker)
        bm25_fb = MagicMock(spec=BM25Fallback)
        bm25_fb.should_trigger = MagicMock(return_value=False)

        svc = RAGServiceImpl(
            query_enhancer=enhancer,
            retriever=retriever,
            reranker=reranker,
            heuristic_reranker=heuristic,
            bm25_fallback=bm25_fb,
        )
        await svc.retrieve(RetrievalRequest(query="x"))
        kwargs = reranker.rerank.await_args.kwargs
        # settings.rag_rerank_output_top_k default is 5
        assert kwargs["top_k"] == 5
