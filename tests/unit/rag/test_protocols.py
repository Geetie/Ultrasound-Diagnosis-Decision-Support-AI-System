"""Unit tests for app.rag.protocols.

Validates Pydantic validation rules, error handling, and enum completeness.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_rag]

from app.knowledge.protocols import KnowledgeChunk
from app.rag.protocols import (
    EnhancedQuery,
    RAGError,
    RAGErrorCode,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)


def _mk_chunk(chunk_id: str = "c1", score: float = 0.9) -> ScoredChunk:
    chunk = KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="some content",
        metadata={},
        embedding=None,
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )
    return ScoredChunk(
        chunk=chunk,
        score=score,
        rerank_score=None,
        retrieval_source=RetrievalMethod.HYBRID,
    )


class TestRetrievalRequest:
    def test_minimal_valid(self):
        req = RetrievalRequest(query="甲状腺结节")
        assert req.query == "甲状腺结节"
        assert req.top_k == 20
        assert req.use_query_enhancement is True
        assert req.use_reranking is True
        assert req.use_bm25_fallback is True
        assert req.use_multimodal is True
        assert req.use_llm_enhancement is False
        assert req.min_score_threshold == 0.5
        assert req.organ is None
        assert req.image_data is None
        assert req.kb_version is None
        assert req.extra == {}

    def test_empty_query_rejected(self):
        with pytest.raises(ValueError):
            RetrievalRequest(query="")

    def test_whitespace_query_rejected(self):
        with pytest.raises(ValueError):
            RetrievalRequest(query="   \n\t  ")

    def test_zero_top_k_rejected(self):
        with pytest.raises(ValueError):
            RetrievalRequest(query="x", top_k=0)

    def test_negative_top_k_rejected(self):
        with pytest.raises(ValueError):
            RetrievalRequest(query="x", top_k=-1)

    def test_full_request(self):
        req = RetrievalRequest(
            query="liver nodule",
            organ="liver",
            image_data=b"\x00\x01",
            image_format="png",
            top_k=10,
            use_query_enhancement=False,
            use_reranking=True,
            use_bm25_fallback=True,
            use_multimodal=True,
            use_llm_enhancement=True,
            min_score_threshold=0.7,
            kb_version="kb_v1.2.3",
            request_id="r-1",
            session_id="s-1",
            extra={"trace_id": "abc"},
        )
        assert req.image_data == b"\x00\x01"
        assert req.kb_version == "kb_v1.2.3"
        assert req.extra == {"trace_id": "abc"}


class TestRetrievalResult:
    def test_empty_result_top_score_zero(self):
        r = RetrievalResult()
        assert r.top_score == 0.0
        assert r.has_results is False
        assert r.chunks == []
        assert r.latency_ms == 0.0
        assert r.p99_violation is False

    def test_with_chunks(self):
        chunks = [_mk_chunk("c1", 0.9), _mk_chunk("c2", 0.7)]
        r = RetrievalResult(chunks=chunks)
        assert r.has_results is True
        assert r.top_score == 0.9

    def test_default_method(self):
        r = RetrievalResult()
        assert r.retrieval_method == RetrievalMethod.HYBRID


class TestEnhancedQuery:
    def test_defaults(self):
        eq = EnhancedQuery(original="q", enhanced="q")
        assert eq.layer1_synonyms == []
        assert eq.layer2_hypernyms == []
        assert eq.layer3_related == []
        assert eq.llm_enhanced is False
        assert eq.organ is None

    def test_with_layers(self):
        eq = EnhancedQuery(
            original="q",
            enhanced="q expanded",
            organ="thyroid",
            layer1_synonyms=["a"],
            layer2_hypernyms=["b"],
            layer3_related=["c"],
            llm_enhanced=True,
        )
        assert eq.organ == "thyroid"
        assert eq.llm_enhanced is True
        assert eq.layer1_synonyms == ["a"]


class TestEnums:
    def test_rag_error_codes_unique(self):
        values = [c.value for c in RAGErrorCode]
        assert len(values) == len(set(values))

    def test_retrieval_methods_unique(self):
        values = [m.value for m in RetrievalMethod]
        assert len(values) == len(set(values))
        assert "dense" in values
        assert "sparse" in values
        assert "hybrid" in values
        assert "bm25_fallback" in values


class TestRAGError:
    def test_format(self):
        e = RAGError(
            error_code=RAGErrorCode.RETRIEVAL_FAILED,
            message="x failed",
            detail="connection refused",
            retryable=True,
            retry_strategy="retry_after_5s",
        )
        s = str(e)
        assert "E5001_RAG_RETRIEVAL_FAILED" in s
        assert "x failed" in s
        assert e.detail == "connection refused"
        assert e.retryable is True
        assert e.retry_strategy == "retry_after_5s"
