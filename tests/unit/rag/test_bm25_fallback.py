"""Unit tests for app.rag.bm25_fallback (T-GA-RAG-03).

Covers:
- tokenize() basic behavior (already covered in test_reranker; here for completeness)
- BM25Index ensure_built idempotency
- BM25Index kb_version triggers rebuild
- BM25Index.search with built index (organ filter, empty query, negative scores)
- BM25Index properties: is_built, num_chunks, close
- BM25Fallback.should_trigger policy
- BM25Fallback.trigger_reason strings
- BM25Fallback.fallback_search wraps hits as ScoredChunk

Strategy: inject a fake _BuiltIndex directly via the private _built attribute
to avoid spinning up a real DB session. The DB path is exercised by
tests/unit/data/test_knowledge_builder.py and integration tests.
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from rank_bm25 import BM25Okapi

pytestmark = [pytest.mark.track_rag]

from app.knowledge.protocols import KnowledgeChunk
from app.rag.bm25_fallback import (
    BM25Fallback,
    BM25Index,
    _BuiltIndex,
    tokenize,
)
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalResult,
    ScoredChunk,
)

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _mk_chunk(
    chunk_id: str,
    organ: str,
    content: str,
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
        token_count=10,
    )


def _inject_index(
    bm25: BM25Index,
    docs: list[tuple[str, str, str]],   # (chunk_id, organ, content)
    kb_version: str | None = "kb_v1.0.0",
) -> None:
    """Build a small BM25Okapi from in-memory docs and inject into BM25Index."""
    if not docs:
        bm25._built = _BuiltIndex(
            bm25=None, chunk_ids=[], chunks=[], organs=[],
            kb_version=kb_version, built_at=time.monotonic(),
        )
        return
    corpus = [tokenize(content) for _, _, content in docs]
    corpus = [c if c else ["__empty__"] for c in corpus]
    bm25._built = _BuiltIndex(
        bm25=BM25Okapi(corpus),
        chunk_ids=[d[0] for d in docs],
        chunks=[_mk_chunk(d[0], d[1], d[2]) for d in docs],
        organs=[d[1] for d in docs],
        kb_version=kb_version,
        built_at=time.monotonic(),
    )


def _fake_session_factory() -> MagicMock:
    """Return a session_factory that produces a minimally-usable AsyncSession mock."""
    session = AsyncMock(name="AsyncSession")
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session.execute = AsyncMock(return_value=result)
    session.close = AsyncMock(return_value=None)
    factory = MagicMock(return_value=session)
    return factory


# ----------------------------------------------------------------------
# tokenize
# ----------------------------------------------------------------------


class TestTokenize:
    def test_chinese(self):
        out = tokenize("低回声结节")
        # Full phrase preserved
        assert "低回声结节" in out
        # 2-char windows produced for partial-match recall
        assert "低回" in out
        assert "回声" in out
        assert "声结" in out
        assert "结节" in out

    def test_english(self):
        out = tokenize("thyroid nodule")
        assert "thyroid" in out
        assert "nodule" in out

    def test_empty(self):
        assert tokenize("") == []

    def test_mixed(self):
        out = tokenize("肝脏 liver")
        assert "肝脏" in out
        assert "liver" in out

    def test_lowercases(self):
        out = tokenize("HYPERECHOIC")
        assert "hyperechoic" in out

    def test_punctuation_removed(self):
        out = tokenize("a, b. c! d?")
        assert "," not in out
        assert "." not in out
        assert "!" not in out
        assert "?" not in out
        assert "a" in out
        assert "b" in out


# ----------------------------------------------------------------------
# BM25Index.ensure_built
# ----------------------------------------------------------------------


class TestEnsureBuilt:
    async def test_first_build_then_cached(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        # No _built yet
        assert bm25.is_built is False
        await bm25.ensure_built(kb_version="kb_v1.0.0")
        # Still not built because fake factory returns no rows
        # (BM25 built is None when corpus is empty, see _build_locked)
        # We expect: the function ran without error, and the index remains "not built"
        # if no rows were returned.
        # For the "cached" path, manually inject:
        _inject_index(bm25, [("c1", "thyroid", "hypoechoic nodule")])
        # Calling again with the same kb_version should be a no-op (cached)
        prev_built = bm25._built
        await bm25.ensure_built(kb_version="kb_v1.0.0")
        assert bm25._built is prev_built

    async def test_kb_version_change_triggers_rebuild(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "old")], kb_version="kb_v1")
        old_built = bm25._built
        # Same version → no rebuild
        await bm25.ensure_built(kb_version="kb_v1")
        assert bm25._built is old_built
        # Different version → triggers rebuild (but factory returns no rows,
        # so the rebuilt index is empty)
        await bm25.ensure_built(kb_version="kb_v2")
        # New _BuiltIndex instance (because rebuild path)
        assert bm25._built is not old_built
        # But empty, because our fake factory has no data
        assert bm25.is_built is False

    async def test_kb_version_none_uses_cache(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "x")], kb_version="kb_v1")
        prev = bm25._built
        await bm25.ensure_built(kb_version=None)
        # None is treated as "no version constraint" → cached
        assert bm25._built is prev


# ----------------------------------------------------------------------
# BM25Index.search
# ----------------------------------------------------------------------


class TestSearch:
    def test_not_built_returns_empty(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        # Unbuilt → no results
        assert bm25.search("x") == []

    def test_empty_query_returns_empty(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "hypoechoic")])
        # tokenize("") returns []
        assert bm25.search("") == []

    def test_basic_search(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(
            bm25,
            [
                ("c1", "thyroid", "hypoechoic nodule"),
                ("c2", "thyroid", "hyperechoic nodule"),
                ("c3", "liver", "liver mass"),
            ],
        )
        hits = bm25.search("hypoechoic", top_k=5)
        assert len(hits) >= 1
        # c1 should be top hit
        assert hits[0].chunk_id == "c1"
        # All hits have non-negative scores
        for h in hits:
            assert h.score >= 0.0

    def test_organ_filter(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(
            bm25,
            [
                ("c1", "thyroid", "nodule"),
                ("c2", "liver", "nodule"),
                ("c3", "liver", "nodule"),
            ],
        )
        hits = bm25.search("nodule", top_k=10, organ_filter="liver")
        organs = {h.chunk.organ for h in hits}
        assert organs == {"liver"}
        assert all(h.chunk_id in {"c2", "c3"} for h in hits)

    def test_top_k_truncation(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(
            bm25,
            [(f"c{i}", "thyroid", "nodule") for i in range(10)],
        )
        hits = bm25.search("nodule", top_k=3)
        assert len(hits) == 3

    def test_top_k_zero(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "nodule")])
        assert bm25.search("nodule", top_k=0) == []

    def test_negative_score_clamped(self):
        """BM25Okapi can produce negative scores; clamp to 0."""
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        # Manually inject an index with a stub that returns negative scores
        from rank_bm25 import BM25Okapi
        bm25._built = _BuiltIndex(
            bm25=BM25Okapi([["foo"], ["bar"]]),
            chunk_ids=["c1", "c2"],
            chunks=[_mk_chunk("c1", "thyroid", "foo"), _mk_chunk("c2", "thyroid", "bar")],
            organs=["thyroid", "thyroid"],
            kb_version="kb_v1",
            built_at=time.monotonic(),
        )
        # Query with a term that has no overlap → scores may be negative
        hits = bm25.search("nonexistent_token_xyz", top_k=5)
        # All returned scores are non-negative
        for h in hits:
            assert h.score >= 0.0


# ----------------------------------------------------------------------
# BM25Index properties
# ----------------------------------------------------------------------


class TestProperties:
    def test_is_built_initially_false(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        assert bm25.is_built is False
        assert bm25.num_chunks == 0

    def test_is_built_true_after_inject(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "x"), ("c2", "liver", "y")])
        assert bm25.is_built is True
        assert bm25.num_chunks == 2

    async def test_close_clears_built(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "x")])
        assert bm25.is_built is True
        await bm25.close()
        assert bm25._built is None
        assert bm25.is_built is False

    async def test_health_check_unbuilt(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        assert await bm25.health_check() is False

    async def test_health_check_built(self):
        bm25 = BM25Index(
            session_factory=_fake_session_factory(), max_chunks=100
        )
        _inject_index(bm25, [("c1", "thyroid", "x")])
        assert await bm25.health_check() is True


# ----------------------------------------------------------------------
# BM25Fallback.should_trigger / trigger_reason
# ----------------------------------------------------------------------


class TestBM25FallbackPolicy:
    def _mk_result(self, scores: list[float], chunks: int = 0) -> RetrievalResult:
        scored = []
        for i, s in enumerate(scores):
            chunk = _mk_chunk(f"c{i}", "thyroid", "x")
            scored.append(
                ScoredChunk(
                    chunk=chunk,
                    score=s,
                    rerank_score=None,
                    retrieval_source=RetrievalMethod.HYBRID,
                )
            )
        return RetrievalResult(
            chunks=scored,
            retrieval_method=RetrievalMethod.HYBRID,
        )

    def test_no_results_triggers(self):
        fb = BM25Fallback(BM25Index(_fake_session_factory()))
        result = RetrievalResult()
        assert fb.should_trigger(result) is True
        assert fb.trigger_reason(result) == "no_results"

    def test_low_top1_triggers(self):
        fb = BM25Fallback(BM25Index(_fake_session_factory()))
        result = self._mk_result([0.1, 0.05, 0.04])
        assert fb.should_trigger(result) is True
        assert fb.trigger_reason(result) == "low_top1_score"

    def test_too_few_results_triggers(self):
        fb = BM25Fallback(
            BM25Index(_fake_session_factory()),
            min_top1_score=0.3,
            min_results=3,
        )
        # 2 chunks, both above threshold → still triggers because too few
        result = self._mk_result([0.9, 0.8])
        assert fb.should_trigger(result) is True
        assert fb.trigger_reason(result) == "too_few_results"

    def test_sufficient_results_does_not_trigger(self):
        fb = BM25Fallback(
            BM25Index(_fake_session_factory()),
            min_top1_score=0.3,
            min_results=3,
        )
        result = self._mk_result([0.9, 0.8, 0.5])
        assert fb.should_trigger(result) is False
        assert fb.trigger_reason(result) == "ok"

    def test_custom_thresholds(self):
        fb = BM25Fallback(
            BM25Index(_fake_session_factory()),
            min_top1_score=0.5,
            min_results=2,
        )
        # top1=0.4 < 0.5 → triggers with "low_top1_score"
        result = self._mk_result([0.4, 0.3, 0.2])
        assert fb.should_trigger(result) is True
        assert fb.trigger_reason(result) == "low_top1_score"


# ----------------------------------------------------------------------
# BM25Fallback.fallback_search
# ----------------------------------------------------------------------


class TestFallbackSearch:
    async def test_unbuilt_returns_empty(self):
        index = BM25Index(_fake_session_factory())
        fb = BM25Fallback(index)
        out = await fb.fallback_search(query="x", top_k=5, organ=None)
        assert out == []

    async def test_built_wraps_as_scored_chunks(self):
        index = BM25Index(_fake_session_factory())
        _inject_index(
            index,
            [
                ("c1", "thyroid", "hypoechoic nodule"),
                ("c2", "thyroid", "hyperechoic mass"),
            ],
        )
        fb = BM25Fallback(index)
        out = await fb.fallback_search(
            query="hypoechoic", top_k=5, organ="thyroid"
        )
        assert len(out) >= 1
        for sc in out:
            assert sc.retrieval_source == RetrievalMethod.BM25_FALLBACK
            assert sc.rerank_score is None
            assert sc.score >= 0.0
            # The wrapped chunk is the BM25 chunk
            assert sc.chunk.chunk_id in {"c1", "c2"}

    async def test_fallback_search_organ_filter(self):
        index = BM25Index(_fake_session_factory())
        _inject_index(
            index,
            [
                ("c1", "thyroid", "nodule"),
                ("c2", "liver", "nodule"),
            ],
        )
        fb = BM25Fallback(index)
        out = await fb.fallback_search(
            query="nodule", top_k=10, organ="liver"
        )
        for sc in out:
            assert sc.chunk.organ == "liver"
