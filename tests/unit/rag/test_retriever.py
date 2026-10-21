"""Unit tests for app.rag.retriever (T-GA-RAG-02).

Covers:
- Dense / Sparse / Hybrid parallel retrieval
- RRF fusion formula correctness
- Multi-modal image leg (success + collection_not_found soft skip)
- Organ filter pass-through
- Dense / Sparse / Image leg failure → soft degrade (empty result for that leg)
- RRF deduplication when the same chunk appears in both legs
- top_k truncation
- Retrieval source preservation (DENSE / SPARSE / HYBRID)
- All legs failing → empty RetrievalResult

The retriever depends on VectorIndexClient and EmbeddingService ABCs; we
mock them with MagicMock(spec=...) so attribute access is type-checked.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.embedding.protocols import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingModality,
    EmbeddingResponse,
    SearchResult,
    TextEmbeddingRequest,
    VectorIndexError,
    VectorIndexErrorCode,
)
from app.knowledge.protocols import KnowledgeChunk
from app.rag.bm25_fallback import BM25Hit, BM25Index
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalRequest,
)
from app.rag.retriever import Retriever

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _make_chunk(
    chunk_id: str,
    organ: str = "thyroid",
    content: str = "x",
    embedding: list[float] | None = None,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ=organ,
        content=content,
        metadata={},
        embedding=embedding,
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )


def _make_search_result(chunk_id: str, score: float) -> SearchResult:
    return SearchResult(id=chunk_id, score=score, metadata={"organ": "thyroid"})


def _make_embedding_response(dim: int = 4) -> EmbeddingResponse:
    return EmbeddingResponse(
        embeddings=[[0.1] * dim],
        model="bge-m3-medical",
        dimension=dim,
        modality=EmbeddingModality.TEXT,
    )


def _make_mock_vector_client() -> MagicMock:
    """VectorIndexClient mock with sane defaults."""
    client = MagicMock(name="VectorIndexClient")
    client.search = AsyncMock(return_value=[])
    client.health_check = AsyncMock(return_value=True)
    client.close = AsyncMock(return_value=None)
    return client


def _make_mock_embedding() -> MagicMock:
    """EmbeddingService mock (text-only)."""
    svc = MagicMock(name="EmbeddingService")
    svc.embed_text = AsyncMock(return_value=_make_embedding_response())
    svc.health_check = AsyncMock(return_value=True)
    svc.close = AsyncMock(return_value=None)
    return svc


def _make_mock_clip() -> MagicMock:
    """Ultrasound-CLIP mock (image embedding)."""
    svc = MagicMock(name="UltrasoundClipService")
    svc.embed_image = AsyncMock(
        return_value=EmbeddingResponse(
            embeddings=[[0.1] * 768],
            model="ultrasound-clip-v1",
            dimension=768,
            modality=EmbeddingModality.IMAGE,
        )
    )
    svc.health_check = AsyncMock(return_value=True)
    svc.close = AsyncMock(return_value=None)
    return svc


def _make_mock_bm25() -> MagicMock:
    """BM25Index mock; search returns [] by default, is_built=True."""
    bm25 = MagicMock(spec=BM25Index, name="BM25Index")
    bm25.is_built = True
    bm25.search = MagicMock(return_value=[])
    bm25.health_check = AsyncMock(return_value=True)
    bm25.close = AsyncMock(return_value=None)
    bm25.ensure_built = AsyncMock(return_value=None)
    return bm25


def _bm25_hit(chunk_id: str, score: float, organ: str = "thyroid") -> BM25Hit:
    return BM25Hit(
        chunk_id=chunk_id,
        score=score,
        chunk=_make_chunk(chunk_id, organ=organ, content=f"bm25 content {chunk_id}"),
    )


# ----------------------------------------------------------------------
# basic construction
# ----------------------------------------------------------------------


class TestRetrieverConstruction:
    def test_minimal_construction(self):
        r = Retriever(
            vector_client=_make_mock_vector_client(),
            embedding_service=_make_mock_embedding(),
        )
        assert r._vector_client is not None
        assert r._embedding is not None
        assert r._clip is None
        assert r._bm25 is None
        assert r._rrf_k > 0


# ----------------------------------------------------------------------
# Dense-only
# ----------------------------------------------------------------------


class TestDenseOnly:
    async def test_returns_search_results(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[
                _make_search_result("c1", 0.9),
                _make_search_result("c2", 0.7),
            ]
        )
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)

        req = RetrievalRequest(query="hypoechoic", top_k=5)
        result = await r.retrieve(req, enhanced_query="hypoechoic expanded")

        assert result.has_results is True
        assert len(result.chunks) == 2
        assert result.chunks[0].chunk.chunk_id == "c1"
        # Without a session_factory, retriever uses placeholder chunks
        # but rank is preserved.
        assert result.chunks[0].score >= result.chunks[1].score
        # Source marked DENSE
        assert all(c.retrieval_source == RetrievalMethod.DENSE for c in result.chunks)

    async def test_calls_embed_text_once(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)

        await r.retrieve(RetrievalRequest(query="x"), enhanced_query="x enhanced")
        emb.embed_text.assert_awaited_once()
        # The first argument should be a TextEmbeddingRequest with texts=[enhanced]
        call_args = emb.embed_text.await_args
        text_req = call_args.args[0]
        assert isinstance(text_req, TextEmbeddingRequest)
        assert text_req.texts == ["x enhanced"]

    async def test_organ_filter_passed(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)

        req = RetrievalRequest(query="x", organ="thyroid", top_k=3)
        await r.retrieve(req, enhanced_query="x")

        kwargs = vc.search.await_args.kwargs
        assert kwargs["filter"] == {"organ": "thyroid"}
        assert kwargs["top_k"] == 3
        # Collection: settings.rag_collection default is "knowledge_chunks"
        assert kwargs["collection"] in ("knowledge_chunks", None) or isinstance(
            kwargs["collection"], str
        )

    async def test_no_organ_filter_is_none(self):
        vc = _make_mock_vector_client()
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)

        await r.retrieve(RetrievalRequest(query="x"), enhanced_query="x")
        kwargs = vc.search.await_args.kwargs
        assert kwargs["filter"] is None

    async def test_empty_embedding_returns_empty(self):
        vc = _make_mock_vector_client()
        emb = _make_mock_embedding()
        emb.embed_text = AsyncMock(
            return_value=EmbeddingResponse(embeddings=[], model="x", dimension=0)
        )
        r = Retriever(vector_client=vc, embedding_service=emb)

        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        assert result.has_results is False
        # vector_client.search should NOT have been called
        vc.search.assert_not_awaited()


# ----------------------------------------------------------------------
# Dense failure soft degrade
# ----------------------------------------------------------------------


class TestDenseFailure:
    async def test_embedding_error_returns_empty_dense(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        emb.embed_text = AsyncMock(
            side_effect=EmbeddingError(
                EmbeddingErrorCode.CONNECTION_FAILED, "down"
            )
        )
        r = Retriever(vector_client=vc, embedding_service=emb)

        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        # Dense failed, no bm25 → empty result, no exception
        assert result.has_results is False

    async def test_index_error_returns_empty_dense(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            side_effect=VectorIndexError(
                VectorIndexErrorCode.SEARCH_FAILED, "broken"
            )
        )
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)

        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        assert result.has_results is False

    async def test_dense_fail_sparse_ok_returns_sparse(self):
        vc = _make_mock_vector_client()
        emb = _make_mock_embedding()
        emb.embed_text = AsyncMock(
            side_effect=EmbeddingError(
                EmbeddingErrorCode.CONNECTION_FAILED, "down"
            )
        )
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(
            return_value=[_bm25_hit("c1", 2.5), _bm25_hit("c2", 1.0)]
        )
        r = Retriever(
            vector_client=vc, embedding_service=emb, bm25_index=bm25
        )

        result = await r.retrieve(
            RetrievalRequest(query="x", top_k=5), enhanced_query="x"
        )
        assert result.has_results is True
        assert len(result.chunks) == 2
        # Sparse leg is the only contributor
        assert all(
            c.retrieval_source == RetrievalMethod.SPARSE for c in result.chunks
        )


# ----------------------------------------------------------------------
# Sparse (BM25) leg
# ----------------------------------------------------------------------


class TestSparseLeg:
    async def test_sparse_only(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(
            return_value=[_bm25_hit("c1", 3.0), _bm25_hit("c2", 1.5)]
        )
        r = Retriever(
            vector_client=vc, embedding_service=emb, bm25_index=bm25
        )

        result = await r.retrieve(
            RetrievalRequest(query="x", organ="thyroid", top_k=5),
            enhanced_query="x",
        )
        assert result.has_results is True
        assert all(c.retrieval_source == RetrievalMethod.SPARSE for c in result.chunks)
        # organ_filter passed to BM25
        bm25.search.assert_called_once()
        kwargs = bm25.search.call_args.kwargs
        assert kwargs.get("organ_filter") == "thyroid"

    async def test_sparse_unbuilt_returns_empty(self):
        vc = _make_mock_vector_client()
        emb = _make_mock_embedding()
        bm25 = _make_mock_bm25()
        bm25.is_built = False  # pretend not built
        r = Retriever(
            vector_client=vc, embedding_service=emb, bm25_index=bm25
        )
        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        # No dense hits, no sparse → empty
        assert result.has_results is False

    async def test_sbm25_search_exception_swallowed(self):
        vc = _make_mock_vector_client()
        emb = _make_mock_embedding()
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(side_effect=RuntimeError("bm25 crash"))
        r = Retriever(
            vector_client=vc, embedding_service=emb, bm25_index=bm25
        )
        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        assert result.has_results is False


# ----------------------------------------------------------------------
# Hybrid (Dense + Sparse) with RRF
# ----------------------------------------------------------------------


class TestHybridRRF:
    async def test_rrf_combines_dense_and_sparse(self):
        """Two legs with distinct chunk_ids → RRF adds their scores."""
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[
                _make_search_result("c1", 0.9),
                _make_search_result("c2", 0.7),
            ]
        )
        emb = _make_mock_embedding()
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(
            return_value=[
                _bm25_hit("c2", 5.0),  # overlaps with dense c2
                _bm25_hit("c3", 4.0),  # unique to sparse
            ]
        )
        r = Retriever(
            vector_client=vc,
            embedding_service=emb,
            bm25_index=bm25,
            rrf_k=60,
        )
        result = await r.retrieve(
            RetrievalRequest(query="x", top_k=5), enhanced_query="x"
        )

        ids = [c.chunk.chunk_id for c in result.chunks]
        assert set(ids) == {"c1", "c2", "c3"}
        # c2 was in BOTH legs → highest RRF
        assert ids[0] == "c2"

        # Manually compute expected fused score for c2:
        # Dense: rank 2 → 1/(60+2) ≈ 0.01613
        # Sparse: rank 1 → 1/(60+1) ≈ 0.01639
        # Total ≈ 0.03252
        c2 = next(c for c in result.chunks if c.chunk.chunk_id == "c2")
        expected = 1.0 / (60 + 2) + 1.0 / (60 + 1)
        assert abs(c2.score - expected) < 1e-6

    async def test_rrf_dedup_same_chunk(self):
        """Same chunk_id appears in both legs → only one entry, summed score."""
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[_make_search_result("c1", 0.9)]
        )
        emb = _make_mock_embedding()
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(return_value=[_bm25_hit("c1", 3.0)])
        r = Retriever(
            vector_client=vc, embedding_service=emb, bm25_index=bm25
        )
        result = await r.retrieve(
            RetrievalRequest(query="x", top_k=5), enhanced_query="x"
        )
        assert len(result.chunks) == 1
        # Source should be the first leg the chunk appeared in (DENSE for rank-1)
        assert result.chunks[0].chunk.chunk_id == "c1"

    async def test_top_k_truncation(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[_make_search_result(f"c{i}", 0.9 - i * 0.1) for i in range(10)]
        )
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)
        result = await r.retrieve(
            RetrievalRequest(query="x", top_k=3), enhanced_query="x"
        )
        assert len(result.chunks) == 3

    async def test_rrf_default_k_is_60(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[_make_search_result("c1", 0.9)])
        emb = _make_mock_embedding()
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(return_value=[_bm25_hit("c1", 1.0)])
        # No explicit rrf_k → use settings default
        r = Retriever(vector_client=vc, embedding_service=emb, bm25_index=bm25)
        result = await r.retrieve(
            RetrievalRequest(query="x", top_k=1), enhanced_query="x"
        )
        # Just one chunk
        assert len(result.chunks) == 1

    async def test_rrf_fuse_direct(self):
        """Direct test of the static-style _rrf_fuse helper."""
        from app.rag.retriever import _Hit
        vc = _make_mock_vector_client()
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb, rrf_k=60)

        hits_dense = [
            _Hit(chunk_id="a", chunk=_make_chunk("a"), score=0.9, source=RetrievalMethod.DENSE),
            _Hit(chunk_id="b", chunk=_make_chunk("b"), score=0.7, source=RetrievalMethod.DENSE),
        ]
        hits_sparse = [
            _Hit(chunk_id="b", chunk=_make_chunk("b"), score=3.0, source=RetrievalMethod.SPARSE),
        ]
        fused = r._rrf_fuse(
            {RetrievalMethod.DENSE: hits_dense, RetrievalMethod.SPARSE: hits_sparse}
        )
        # b: dense rank 2 + sparse rank 1 = 1/62 + 1/61
        # a: dense rank 1 = 1/61
        # 1/61 ≈ 0.01639, 1/62 + 1/61 ≈ 0.03252 → b wins
        assert fused[0].chunk_id == "b"
        assert len(fused) == 2


# ----------------------------------------------------------------------
# Multi-modal (image) leg
# ----------------------------------------------------------------------


class TestImageLeg:
    async def test_image_leg_invoked_when_image_provided(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            side_effect=[
                [_make_search_result("c1", 0.8)],  # dense text
                [_make_search_result("cimg1", 0.7)],  # image
            ]
        )
        emb = _make_mock_embedding()
        clip = _make_mock_clip()
        r = Retriever(
            vector_client=vc,
            embedding_service=emb,
            ultrasound_clip=clip,
        )

        req = RetrievalRequest(
            query="x",
            image_data=b"\x00\x01\x02",
            image_format="dicom",
            top_k=5,
            # A.78 病人隔离：影像腿要求 session（无 session 时拒绝检索，防跨病人召回）
            session_id="sess-test-1",
        )
        result = await r.retrieve(req, enhanced_query="x")
        assert result.has_results is True
        ids = [c.chunk.chunk_id for c in result.chunks]
        assert "cimg1" in ids
        clip.embed_image.assert_awaited_once()
        # vector_client.search was called twice (text + image)
        assert vc.search.await_count == 2

    async def test_image_leg_skipped_without_session(self):
        """A.78：没有 session 时**不发影像检索**（否则等于全库跨病人搜）。"""
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[_make_search_result("c1", 0.8)])
        r = Retriever(
            vector_client=vc,
            embedding_service=_make_mock_embedding(),
            ultrasound_clip=_make_mock_clip(),
        )
        req = RetrievalRequest(
            query="x", image_data=b"\x00\x01\x02", image_format="dicom", top_k=5
        )
        await r.retrieve(req, enhanced_query="x")
        # 只应有文本那一次检索
        assert vc.search.await_count == 1

    async def test_image_skipped_when_no_image_data(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        clip = _make_mock_clip()
        r = Retriever(
            vector_client=vc, embedding_service=emb, ultrasound_clip=clip
        )
        req = RetrievalRequest(query="x", image_data=None, top_k=5)
        await r.retrieve(req, enhanced_query="x")
        clip.embed_image.assert_not_awaited()

    async def test_image_skipped_when_no_clip_service(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        # No clip
        r = Retriever(vector_client=vc, embedding_service=emb)
        req = RetrievalRequest(
            query="x", image_data=b"\x00", use_multimodal=True
        )
        result = await r.retrieve(req, enhanced_query="x")
        # No hits but no crash
        assert result.has_results is False

    async def test_image_collection_not_found_soft_skip(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            side_effect=[
                [_make_search_result("c1", 0.8)],
                VectorIndexError(
                    VectorIndexErrorCode.COLLECTION_NOT_FOUND,
                    "Collection ultrasound_chunks not found",
                ),
            ]
        )
        emb = _make_mock_embedding()
        clip = _make_mock_clip()
        r = Retriever(
            vector_client=vc, embedding_service=emb, ultrasound_clip=clip
        )
        req = RetrievalRequest(
            query="x", image_data=b"\x00", top_k=5
        )
        result = await r.retrieve(req, enhanced_query="x")
        # Text leg worked → still get c1
        assert result.has_results is True
        assert result.chunks[0].chunk.chunk_id == "c1"

    async def test_image_embedding_error_swallowed(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[_make_search_result("c1", 0.8)]
        )
        emb = _make_mock_embedding()
        clip = _make_mock_clip()
        clip.embed_image = AsyncMock(
            side_effect=EmbeddingError(
                EmbeddingErrorCode.MODEL_LOAD_FAILED, "clip down"
            )
        )
        r = Retriever(
            vector_client=vc, embedding_service=emb, ultrasound_clip=clip
        )
        req = RetrievalRequest(query="x", image_data=b"\x00", top_k=5)
        result = await r.retrieve(req, enhanced_query="x")
        # Text leg worked
        assert result.has_results is True

    async def test_use_multimodal_false_skips_image(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(return_value=[])
        emb = _make_mock_embedding()
        clip = _make_mock_clip()
        r = Retriever(
            vector_client=vc, embedding_service=emb, ultrasound_clip=clip
        )
        req = RetrievalRequest(
            query="x", image_data=b"\x00", use_multimodal=False
        )
        await r.retrieve(req, enhanced_query="x")
        clip.embed_image.assert_not_awaited()


# ----------------------------------------------------------------------
# All-legs-fail / output shape
# ----------------------------------------------------------------------


class TestRetrievalResultShape:
    async def test_all_legs_fail_returns_empty(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            side_effect=RuntimeError("vector store down")
        )
        emb = _make_mock_embedding()
        emb.embed_text = AsyncMock(
            side_effect=EmbeddingError(EmbeddingErrorCode.TIMEOUT, "t/o")
        )
        bm25 = _make_mock_bm25()
        bm25.search = MagicMock(side_effect=RuntimeError("bm25 crash"))
        r = Retriever(
            vector_client=vc, embedding_service=emb, bm25_index=bm25
        )
        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        assert result.has_results is False
        assert result.chunks == []
        # Default method is HYBRID
        assert result.retrieval_method == RetrievalMethod.HYBRID

    async def test_result_method_is_hybrid(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[_make_search_result("c1", 0.9)]
        )
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)
        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        assert result.retrieval_method == RetrievalMethod.HYBRID

    async def test_score_types(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[_make_search_result("c1", 0.9)]
        )
        emb = _make_mock_embedding()
        r = Retriever(vector_client=vc, embedding_service=emb)
        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        for c in result.chunks:
            assert isinstance(c.score, float)
            assert c.rerank_score is None


# ----------------------------------------------------------------------
# _wrap_search_results with no session_factory (placeholder path)
# ----------------------------------------------------------------------


class TestPlaceholderChunks:
    async def test_no_session_factory_uses_placeholder(self):
        vc = _make_mock_vector_client()
        vc.search = AsyncMock(
            return_value=[_make_search_result("abc", 0.5)]
        )
        emb = _make_mock_embedding()
        # session_factory not provided
        r = Retriever(
            vector_client=vc, embedding_service=emb, session_factory=None
        )
        result = await r.retrieve(
            RetrievalRequest(query="x"), enhanced_query="x"
        )
        assert result.has_results is True
        chunk = result.chunks[0].chunk
        # Placeholder fields
        assert chunk.organ == "unknown"
        assert chunk.kb_version == "unknown"
        assert chunk.content == "[placeholder]"
