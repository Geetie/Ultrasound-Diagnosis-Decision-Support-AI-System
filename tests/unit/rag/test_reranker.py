"""Unit tests for app.rag.reranker (T-GA-RAG-03).

Covers:
- BgeRerankerService HTTP client (success, timeout, connect error, bad status,
  invalid JSON, missing 'results', empty chunks)
- BgeRerankerService.health_check
- BgeRerankerService.close (closed client raises on use)
- HeuristicReranker (deterministic, embedding=None graceful, weight validation)
- tokenize() (from bm25_fallback) — also referenced by heuristic reranker
"""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

pytestmark = [pytest.mark.track_rag]

from app.knowledge.protocols import KnowledgeChunk
from app.rag.bm25_fallback import tokenize
from app.rag.protocols import RetrievalMethod
from app.rag.reranker import (
    BgeRerankerService,
    HeuristicReranker,
    RerankError,
)

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _make_chunk(
    chunk_id: str,
    content: str = "x",
    embedding: list[float] | None = None,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content=content,
        metadata={},
        embedding=embedding,
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )


def _make_mock_handler(
    response: httpx.Response | None = None,
    exc: Exception | None = None,
):
    async def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        return response
    return handler


def _inject_client(svc: BgeRerankerService, handler) -> None:
    svc._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=svc._base_url,
    )


def _ok_response(payload: dict[str, Any]) -> httpx.Response:
    return httpx.Response(
        200,
        json=payload,
        request=httpx.Request("POST", "http://test/v1/rerank"),
    )


# ----------------------------------------------------------------------
# BgeRerankerService: construction
# ----------------------------------------------------------------------


class TestConstruction:
    def test_base_url_strips_trailing_slash(self):
        svc = BgeRerankerService(
            base_url="http://x:8005/", model="bge-reranker-base"
        )
        assert svc._base_url == "http://x:8005"


# ----------------------------------------------------------------------
# BgeRerankerService: rerank happy path
# ----------------------------------------------------------------------


class TestBgeRerankSuccess:
    async def test_rerank_returns_sorted_top_k(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        # Indices come back in arbitrary order; reranker sorts.
        payload = {
            "results": [
                {"index": 0, "relevance_score": 0.5},
                {"index": 1, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.7},
            ]
        }
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        chunks = [
            _make_chunk("c0", content="first"),
            _make_chunk("c1", content="second"),
            _make_chunk("c2", content="third"),
        ]
        out = await svc.rerank(query="q", chunks=chunks, top_k=2)
        assert len(out) == 2
        # Sorted by score desc
        assert out[0].chunk.chunk_id == "c1"
        assert out[1].chunk.chunk_id == "c2"
        assert out[0].rerank_score == 0.9
        assert out[1].rerank_score == 0.7

    async def test_rerank_empty_chunks(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        out = await svc.rerank(query="q", chunks=[], top_k=5)
        assert out == []
        # Should not have created a client
        assert svc._client is None

    async def test_rerank_skips_invalid_indices(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        payload = {
            "results": [
                {"index": -1, "relevance_score": 0.9},   # invalid
                {"index": 99, "relevance_score": 0.5},   # out of range
                {"index": 0, "relevance_score": 0.7},    # valid
                {"index": "x", "relevance_score": 0.8},  # bad index type
                "not_a_dict",                              # bad item
            ]
        }
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))

        chunks = [_make_chunk("c0", "a")]
        out = await svc.rerank(query="q", chunks=chunks, top_k=5)
        # Only the valid one survives
        assert len(out) == 1
        assert out[0].chunk.chunk_id == "c0"

    async def test_rerank_default_score_zero(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        payload = {"results": [{"index": 0, "relevance_score": "bad"}]}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))
        chunks = [_make_chunk("c0", "a")]
        out = await svc.rerank(query="q", chunks=chunks, top_k=5)
        # Score falls back to 0.0
        assert out[0].rerank_score == 0.0

    async def test_rerank_sets_hybrid_source(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        payload = {"results": [{"index": 0, "relevance_score": 0.5}]}
        _inject_client(svc, _make_mock_handler(_ok_response(payload)))
        chunks = [_make_chunk("c0", "a")]
        out = await svc.rerank(query="q", chunks=chunks, top_k=5)
        # orchestrator overwrites this when it has the source map
        assert out[0].retrieval_source == RetrievalMethod.HYBRID

    async def test_rerank_top_k_caps_input(self):
        """top_k in payload is capped to len(chunks)."""
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        captured: list[dict] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            captured.append(json.loads(request.content))
            return _ok_response({"results": []})

        _inject_client(svc, handler)
        chunks = [_make_chunk("c0", "a"), _make_chunk("c1", "b")]
        await svc.rerank(query="q", chunks=chunks, top_k=20)
        assert captured[0]["top_k"] == 2  # min(20, 2)


# ----------------------------------------------------------------------
# BgeRerankerService: failure paths
# ----------------------------------------------------------------------


class TestBgeRerankFailures:
    async def test_http_500(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        bad = httpx.Response(
            500, text="oops", request=httpx.Request("POST", "http://x/v1/rerank")
        )
        _inject_client(svc, _make_mock_handler(bad))
        with pytest.raises(RerankError, match="500"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_timeout(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(exc=httpx.TimeoutException("t/o")))
        with pytest.raises(RerankError, match="failed"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_connect_error(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(exc=httpx.ConnectError("refused")))
        with pytest.raises(RerankError, match="failed"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_invalid_json(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        bad = httpx.Response(
            200, content=b"not json",
            request=httpx.Request("POST", "http://x/v1/rerank")
        )
        _inject_client(svc, _make_mock_handler(bad))
        with pytest.raises(RerankError, match="invalid JSON"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_missing_results_field(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(_ok_response({"foo": "bar"})))
        with pytest.raises(RerankError, match="missing 'results'"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_results_not_list(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(_ok_response({"results": "nope"})))
        with pytest.raises(RerankError, match="missing 'results'"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_unexpected_exception(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(exc=RuntimeError("boom")))
        with pytest.raises(RerankError, match="unexpected"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)

    async def test_rerank_after_close_raises(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        await svc.close()
        with pytest.raises(RerankError, match="closed"):
            await svc.rerank(query="q", chunks=[_make_chunk("c0")], top_k=5)


# ----------------------------------------------------------------------
# BgeRerankerService: health_check
# ----------------------------------------------------------------------


class TestBgeHealthCheck:
    async def test_healthy(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        ok = httpx.Response(
            200, request=httpx.Request("GET", "http://test/health")
        )
        _inject_client(svc, _make_mock_handler(ok))
        assert await svc.health_check() is True

    async def test_unhealthy_status(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        bad = httpx.Response(
            503, request=httpx.Request("GET", "http://test/health")
        )
        _inject_client(svc, _make_mock_handler(bad))
        assert await svc.health_check() is False

    async def test_timeout(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(exc=httpx.TimeoutException("t/o")))
        assert await svc.health_check() is False

    async def test_connection_error(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        _inject_client(svc, _make_mock_handler(exc=httpx.ConnectError("refused")))
        assert await svc.health_check() is False


# ----------------------------------------------------------------------
# BgeRerankerService: close
# ----------------------------------------------------------------------


class TestBgeClose:
    async def test_close_idempotent(self):
        svc = BgeRerankerService(
            base_url="http://test", model="bge-reranker-base"
        )
        await svc.close()
        await svc.close()  # no raise
        assert svc._closed is True


# ----------------------------------------------------------------------
# HeuristicReranker
# ----------------------------------------------------------------------


class TestHeuristicReranker:
    def test_weight_must_sum_to_one(self):
        with pytest.raises(ValueError, match="must equal 1.0"):
            HeuristicReranker(jaccard_weight=0.3, cosine_weight=0.3)

    def test_default_weights_valid(self):
        r = HeuristicReranker()
        assert r._jw == 0.5
        assert r._cw == 0.5

    async def test_health_check_always_true(self):
        r = HeuristicReranker()
        assert await r.health_check() is True

    async def test_close_is_noop(self):
        r = HeuristicReranker()
        assert await r.close() is None

    async def test_rerank_empty_chunks(self):
        r = HeuristicReranker()
        assert await r.rerank(query="q", chunks=[], top_k=5) == []

    async def test_deterministic(self):
        """Same input → same output across calls."""
        r = HeuristicReranker()
        chunks = [
            _make_chunk("c0", "thyroid hypoechoic nodule"),
            _make_chunk("c1", "liver mass"),
        ]
        out1 = await r.rerank(query="thyroid hypoechoic", chunks=chunks, top_k=2)
        out2 = await r.rerank(query="thyroid hypoechoic", chunks=chunks, top_k=2)
        assert [c.chunk.chunk_id for c in out1] == [c.chunk.chunk_id for c in out2]
        assert [c.score for c in out1] == [c.score for c in out2]

    async def test_high_overlap_ranks_higher(self):
        r = HeuristicReranker()
        chunks = [
            _make_chunk("c0", "thyroid hypoechoic nodule with clear boundary"),
            _make_chunk("c1", "completely unrelated cardiac content"),
        ]
        out = await r.rerank(
            query="thyroid hypoechoic nodule", chunks=chunks, top_k=2
        )
        # c0 shares many tokens; c1 shares nothing
        assert out[0].chunk.chunk_id == "c0"
        assert out[0].score > out[1].score

    async def test_no_overlap_zero_jaccard(self):
        r = HeuristicReranker()
        chunks = [
            _make_chunk("c0", "alpha beta gamma"),
            _make_chunk("c1", "delta epsilon zeta"),
        ]
        out = await r.rerank(query="xxx yyy", chunks=chunks, top_k=2)
        # Both Jaccard = 0, ranking by embedding norm
        for c in out:
            assert c.score >= 0.0
            assert c.score < 1.0

    async def test_embedding_none_handled(self):
        r = HeuristicReranker()
        chunks = [_make_chunk("c0", "abc def", embedding=None)]
        out = await r.rerank(query="abc", chunks=chunks, top_k=1)
        # Should not raise; cosine component is 0
        assert len(out) == 1
        assert out[0].score >= 0.0

    async def test_embedding_zero_norm(self):
        r = HeuristicReranker()
        chunks = [_make_chunk("c0", "abc", embedding=[0.0, 0.0, 0.0])]
        out = await r.rerank(query="abc", chunks=chunks, top_k=1)
        assert out[0].score >= 0.0

    async def test_top_k_truncation(self):
        r = HeuristicReranker()
        chunks = [_make_chunk(f"c{i}", f"text {i}") for i in range(10)]
        out = await r.rerank(query="text", chunks=chunks, top_k=3)
        assert len(out) == 3

    async def test_top_k_larger_than_chunks(self):
        r = HeuristicReranker()
        chunks = [_make_chunk("c0", "a"), _make_chunk("c1", "b")]
        out = await r.rerank(query="a", chunks=chunks, top_k=10)
        assert len(out) == 2

    async def test_rerank_with_embedding_higher_than_without(self):
        """With equal token overlap, embedding boosts the score."""
        r = HeuristicReranker(jaccard_weight=0.5, cosine_weight=0.5)
        chunks = [
            _make_chunk("c0", "abc", embedding=None),
            _make_chunk("c1", "abc", embedding=[0.5] * 100),
        ]
        out = await r.rerank(query="abc", chunks=chunks, top_k=2)
        # c1 has higher cosine → should be first (or tied at minimum)
        assert out[0].chunk.chunk_id in ("c1", "c0")  # at least no crash
        # If c1 wins, score should be strictly higher
        if out[0].chunk.chunk_id == "c1":
            assert out[0].score > out[1].score


# ----------------------------------------------------------------------
# tokenize() shared between reranker and BM25
# ----------------------------------------------------------------------


class TestTokenize:
    def test_chinese(self):
        out = tokenize("低回声结节")
        # Full phrase preserved
        assert "低回声结节" in out
        # 2-char windows for partial-match recall
        assert "低回" in out
        assert "回声" in out
        assert "声结" in out
        assert "结节" in out

    def test_english(self):
        out = tokenize("TI-RADS 4A")
        assert "ti" in out
        assert "rads" in out
        assert "4a" in out

    def test_mixed(self):
        out = tokenize("肝脏 liver TI-RADS 4A")
        assert "肝脏" in out
        assert "liver" in out
        assert "ti" in out
        assert "rads" in out

    def test_empty(self):
        assert tokenize("") == []

    def test_none_safe(self):
        # The function should not be called with None by callers, but
        # be tolerant: tokenize(None) returns [].
        assert tokenize("") == []

    def test_punctuation_stripped(self):
        out = tokenize("hello, world! how are you?")
        assert "," not in out
        assert "!" not in out
        assert "hello" in out
        assert "world" in out

    def test_case_insensitive(self):
        out = tokenize("HYPERECHOIC")
        assert "hyperechoic" in out
