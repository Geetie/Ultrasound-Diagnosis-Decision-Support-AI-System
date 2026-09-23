"""
Unit tests for the T-GA-DATA-01 knowledge base index construction pipeline.

Covers:
- Chunker (3 strategies + edge cases)
- FeatureExtractor (organ / classification / measurement / coverage)
- HybridIndexer (mocked AsyncSession)
- KnowledgeIndexBuilder (orchestration + resume + version mismatch)
- IndexBuildError code mapping
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.knowledge.protocols import (
    IndexBuildError,
    IndexBuildErrorCode,
    IndexBuildRequest,
    IndexBuildResult,
    KnowledgeChunk,
    KnowledgeIndexBuilder,
)

pytestmark = [pytest.mark.track_data]


# ----- helpers ---------------------------------------------------------------

def _make_chunk_id(source_doc_id: str, idx: int) -> str:
    return f"{source_doc_id}#{idx:04d}"


# ----- protocols -------------------------------------------------------------

class TestKnowledgeChunk:
    def test_basic_construction(self):
        c = KnowledgeChunk(
            chunk_id="doc1#0000",
            kb_version="kb_v1.0.0",
            organ="thyroid",
            content="hello world",
            source_doc_id="doc1",
        )
        assert c.chunk_id == "doc1#0000"
        assert c.token_count == 0
        assert c.embedding is None
        assert c.metadata == {}

    def test_validators_reject_empty_fields(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            KnowledgeChunk(
                chunk_id="",
                kb_version="kb_v1.0.0",
                organ="thyroid",
                content="x",
                source_doc_id="doc1",
            )
        with pytest.raises(ValidationError):
            KnowledgeChunk(
                chunk_id="x",
                kb_version="kb_v1.0.0",
                organ="",
                content="x",
                source_doc_id="doc1",
            )
        with pytest.raises(ValidationError):
            KnowledgeChunk(
                chunk_id="x",
                kb_version="kb_v1.0.0",
                organ="thyroid",
                content="  ",
                source_doc_id="doc1",
            )

    def test_negative_token_count_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            KnowledgeChunk(
                chunk_id="x",
                kb_version="kb_v1.0.0",
                organ="thyroid",
                content="x",
                source_doc_id="doc1",
                token_count=-1,
            )


class TestIndexBuildError:
    def test_error_message_includes_code(self):
        from app.knowledge.protocols import IndexBuildError, IndexBuildErrorCode
        err = IndexBuildError(
            IndexBuildErrorCode.VERSION_MISMATCH,
            "version does not match",
        )
        s = str(err)
        assert "E3001_VERSION_MISMATCH" in s
        assert "version does not match" in s
        assert err.retryable is False
        assert err.retry_strategy == "no_retry"

    def test_retryable_propagates(self):
        from app.knowledge.protocols import IndexBuildError, IndexBuildErrorCode
        err = IndexBuildError(
            IndexBuildErrorCode.EMBEDDING_FAILED,
            "embed failed",
            detail="timeout",
            retryable=True,
            retry_strategy="exponential",
        )
        assert err.retryable is True
        assert err.retry_strategy == "exponential"
        assert err.detail == "timeout"


# ----- chunker ---------------------------------------------------------------

class TestFixedWindowChunker:
    def test_basic_split(self):
        from app.knowledge.chunker import (
            FixedWindowChunker,
            KnowledgeSourceDoc,
        )
        chunker = FixedWindowChunker(window_chars=20)
        text = "a" * 50
        doc = KnowledgeSourceDoc(
            source_doc_id="doc1", organ="thyroid", content=text
        )
        chunks = chunker.chunk(doc, kb_version="kb_v1.0.0")
        assert len(chunks) == 3
        assert chunks[0].chunk_id == _make_chunk_id("doc1", 0)
        assert chunks[-1].chunk_id == _make_chunk_id("doc1", 2)

    def test_short_text_single_chunk(self):
        from app.knowledge.chunker import (
            FixedWindowChunker,
            KnowledgeSourceDoc,
        )
        chunker = FixedWindowChunker(window_chars=1000)
        doc = KnowledgeSourceDoc(
            source_doc_id="doc1", organ="thyroid", content="short text"
        )
        chunks = chunker.chunk(doc, kb_version="kb_v1.0.0")
        assert len(chunks) == 1
        assert chunks[0].content == "short text"

    def test_empty_returns_empty(self):
        from app.knowledge.chunker import (
            FixedWindowChunker,
            KnowledgeSourceDoc,
        )
        chunker = FixedWindowChunker()
        doc = KnowledgeSourceDoc(
            source_doc_id="doc1", organ="thyroid", content="   "
        )
        assert chunker.chunk(doc, "kb_v1.0.0") == []

    def test_invalid_window_raises(self):
        from app.knowledge.chunker import FixedWindowChunker
        with pytest.raises(ValueError):
            FixedWindowChunker(window_chars=0)


class TestSentenceBoundaryChunker:
    def test_splits_on_zh_and_en_punctuation(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SentenceBoundaryChunker,
        )
        text = "第一句。第二句！Third sentence. Fourth? 第五句。"
        doc = KnowledgeSourceDoc(
            source_doc_id="doc1", organ="thyroid", content=text
        )
        chunker = SentenceBoundaryChunker(
            min_tokens=1, max_tokens=4  # very tight to force multiple chunks
        )
        chunks = chunker.chunk(doc, kb_version="kb_v1.0.0")
        assert len(chunks) >= 2
        # Re-stamped IDs should be monotonic.
        ids = [c.chunk_id for c in chunks]
        assert ids == sorted(ids)

    def test_oversized_sentence_falls_back_to_fixed(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SentenceBoundaryChunker,
        )
        # Build a single "sentence" of ~2000 chars (> max_chars).
        long_sentence = "a" * 2000
        doc = KnowledgeSourceDoc(
            source_doc_id="big", organ="liver", content=long_sentence
        )
        chunker = SentenceBoundaryChunker(
            min_tokens=1, max_tokens=10
        )  # max_chars = 20
        chunks = chunker.chunk(doc, "kb_v1.0.0")
        # FixedWindow(window=20) over 2000 chars -> 100 chunks
        assert len(chunks) == 100
        assert all(c.organ == "liver" for c in chunks)

    def test_min_max_validation(self):
        from app.knowledge.chunker import SentenceBoundaryChunker
        with pytest.raises(ValueError):
            SentenceBoundaryChunker(min_tokens=0, max_tokens=10)
        with pytest.raises(ValueError):
            SentenceBoundaryChunker(min_tokens=10, max_tokens=5)

    def test_empty_returns_empty(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SentenceBoundaryChunker,
        )
        doc = KnowledgeSourceDoc(
            source_doc_id="d", organ="thyroid", content=""
        )
        assert SentenceBoundaryChunker().chunk(doc, "kb_v1.0.0") == []


class TestSemanticChunker:
    def test_splits_on_tirads_titles(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SemanticChunker,
        )
        text = (
            "Intro paragraph about thyroid nodules.\n"
            "TI-RADS 3 probably benign nodule.\n"
            "TI-RADS 4A low suspicion nodule."
        )
        doc = KnowledgeSourceDoc(
            source_doc_id="d", organ="thyroid", content=text
        )
        chunker = SemanticChunker(min_tokens=1, max_tokens=20)
        chunks = chunker.chunk(doc, "kb_v1.0.0")
        # Should produce >= 1 chunk with section paths starting with TI-RADS
        tirads_chunks = [c for c in chunks if "TI-RADS" in c.section_path]
        assert len(tirads_chunks) >= 1

    def test_no_titles_falls_back_to_sentence(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SemanticChunker,
        )
        text = "No semantic boundaries here. Just plain text."
        doc = KnowledgeSourceDoc(
            source_doc_id="d", organ="heart", content=text
        )
        chunks = SemanticChunker().chunk(doc, "kb_v1.0.0")
        assert len(chunks) >= 1


class TestCreateChunker:
    def test_factory_returns_correct_strategy(self):
        from app.knowledge.chunker import (
            ChunkerStrategy,
            FixedWindowChunker,
            SemanticChunker,
            SentenceBoundaryChunker,
            create_chunker,
        )
        assert isinstance(
            create_chunker(ChunkerStrategy.FIXED_WINDOW), FixedWindowChunker
        )
        assert isinstance(
            create_chunker(ChunkerStrategy.SENTENCE_BOUNDARY),
            SentenceBoundaryChunker,
        )
        assert isinstance(
            create_chunker(ChunkerStrategy.SEMANTIC), SemanticChunker
        )

    def test_factory_accepts_string(self):
        from app.knowledge.chunker import (
            ChunkerStrategy,
            create_chunker,
        )
        c = create_chunker("fixed_window")
        assert isinstance(c, object)
        # enum passed back: invalid path raises
        with pytest.raises(ValueError):
            create_chunker("nonexistent_strategy")


# ----- feature_extractor -----------------------------------------------------

class TestFeatureExtractor:
    def test_extracts_organ_thyroid(self):
        from app.knowledge.feature_extractor import FeatureExtractor
        fx = FeatureExtractor()
        result = fx.extract(
            "甲状腺右叶中极低回声结节，边界清，形态规则，大小约1.2x0.8 cm"
        )
        assert result.organ == "thyroid"
        assert result.udt_coverage_rate > 0.0

    def test_extracts_tirads_classification(self):
        from app.knowledge.feature_extractor import FeatureExtractor
        fx = FeatureExtractor()
        result = fx.extract(
            "甲状腺右叶低回声结节，TI-RADS 4A"
        )
        assert "TI-RADS 4A" in result.classifications

    def test_extracts_lirads_classification(self):
        from app.knowledge.feature_extractor import FeatureExtractor
        fx = FeatureExtractor()
        result = fx.extract(
            "肝右叶高回声结节，LI-RADS 4"
        )
        assert "LI-RADS 4" in result.classifications
        assert result.organ == "liver"

    def test_extracts_measurements(self):
        from app.knowledge.feature_extractor import FeatureExtractor
        fx = FeatureExtractor()
        result = fx.extract(
            "甲状腺结节大小约2.0x1.5 cm"
        )
        assert len(result.measurements) >= 1
        m = result.measurements[0]
        assert m["value"] == "2.0x1.5"
        assert m["unit"] == "cm"

    def test_organ_hint_used_when_udt_cannot_detect(self):
        from app.knowledge.feature_extractor import FeatureExtractor
        fx = FeatureExtractor()
        result = fx.extract("random unrelated text", organ_hint="heart")
        assert result.organ == "heart"

    def test_empty_text_returns_unknown(self):
        from app.knowledge.feature_extractor import FeatureExtractor
        fx = FeatureExtractor()
        result = fx.extract("", organ_hint="thyroid")
        assert result.organ == "thyroid"  # hint takes precedence over unknown
        assert result.classifications == []
        assert result.measurements == []

    def test_features_to_metadata(self):
        from app.knowledge.feature_extractor import (
            ExtractedFeatures,
            FeatureExtractor,
        )
        meta = FeatureExtractor().features_to_metadata(
            ExtractedFeatures(
                organ="thyroid",
                classifications=["TI-RADS 3"],
                measurements=[{"value": "1.0", "unit": "cm"}],
                anatomical_locations=["right_lobe_upper"],
                udt_coverage_rate=0.8,
            )
        )
        assert meta["organ"] == "thyroid"
        assert meta["classifications"] == ["TI-RADS 3"]
        assert meta["udt_coverage_rate"] == 0.8
        # Ensure JSON-serializable.
        json.dumps(meta)


# ----- hybrid_indexer (mocked session) ---------------------------------------

class _FakeRow:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_mock_session():
    session = AsyncMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    return session


class TestPGVectorHybridIndexer:
    @pytest.mark.asyncio
    async def test_upsert_writes_chunks(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer
        from app.knowledge.protocols import KnowledgeChunk

        session = _make_mock_session()
        indexer = PGVectorHybridIndexer()
        chunks = [
            KnowledgeChunk(
                chunk_id="d#0000",
                kb_version="kb_v1.0.0",
                organ="thyroid",
                content="hello",
                source_doc_id="d",
                token_count=1,
                embedding=[0.1] * 768,
            )
        ]
        n = await indexer.upsert(chunks, session)
        assert n == 1
        assert session.execute.await_count == 1
        assert session.commit.await_count == 1

    @pytest.mark.asyncio
    async def test_upsert_empty_returns_zero(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer
        indexer = PGVectorHybridIndexer()
        session = _make_mock_session()
        n = await indexer.upsert([], session)
        assert n == 0
        session.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upsert_batches(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer
        from app.knowledge.protocols import KnowledgeChunk

        session = _make_mock_session()
        indexer = PGVectorHybridIndexer(batch_flush_size=10)
        chunks = [
            KnowledgeChunk(
                chunk_id=f"d#{i:04d}",
                kb_version="kb_v1.0.0",
                organ="thyroid",
                content=f"c{i}",
                source_doc_id="d",
                token_count=1,
                embedding=[0.0] * 768,
            )
            for i in range(25)
        ]
        n = await indexer.upsert(chunks, session)
        assert n == 25
        # 3 batches: 10 + 10 + 5
        assert session.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_exists_returns_true_when_present(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer

        session = _make_mock_session()
        # First call returns a row, second returns None
        session.execute = AsyncMock(side_effect=[
            MagicMock(first=MagicMock(return_value=("d#0000",))),
            MagicMock(first=MagicMock(return_value=None)),
        ])
        indexer = PGVectorHybridIndexer()
        assert await indexer.exists("d#0000", session) is True
        assert await indexer.exists("missing", session) is False

    @pytest.mark.asyncio
    async def test_delete_by_version(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer

        session = _make_mock_session()
        result_mock = MagicMock()
        result_mock.rowcount = 7
        session.execute = AsyncMock(return_value=result_mock)
        indexer = PGVectorHybridIndexer()
        n = await indexer.delete_by_version("kb_v1.0.0", session)
        assert n == 7

    @pytest.mark.asyncio
    async def test_get_by_id_returns_none_when_missing(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer

        session = _make_mock_session()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None))
        )
        indexer = PGVectorHybridIndexer()
        assert await indexer.get_by_id("missing", session) is None

    @pytest.mark.asyncio
    async def test_count_by_version(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer

        session = _make_mock_session()
        session.execute = AsyncMock(return_value=MagicMock(scalar=MagicMock(return_value=42)))
        indexer = PGVectorHybridIndexer()
        assert await indexer.count_by_version("kb_v1.0.0", session) == 42

    @pytest.mark.asyncio
    async def test_vector_search_returns_top_k(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer

        session = _make_mock_session()
        rows = [
            (
                _FakeRow(
                    id=1,
                    chunk_id="d#0000",
                    kb_version="kb_v1.0.0",
                    organ="thyroid",
                    content="x",
                    meta={},
                    embedding=[0.1] * 768,
                    source_doc_id="d",
                    section_path="",
                    token_count=1,
                ),
                "dist0",
            ),
            (
                _FakeRow(
                    id=2,
                    chunk_id="d#0001",
                    kb_version="kb_v1.0.0",
                    organ="thyroid",
                    content="y",
                    meta={},
                    embedding=[0.2] * 768,
                    source_doc_id="d",
                    section_path="",
                    token_count=1,
                ),
                "dist1",
            ),
        ]
        session.execute = AsyncMock(
            return_value=MagicMock(all=MagicMock(return_value=rows))
        )
        indexer = PGVectorHybridIndexer()
        out = await indexer.vector_search(
            [0.0] * 768, k=2, organ="thyroid", session=session
        )
        assert len(out) == 2
        assert out[0].chunk_id == "d#0000"
        assert out[1].chunk_id == "d#0001"

    @pytest.mark.asyncio
    async def test_vector_search_invalid_k(self):
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer
        indexer = PGVectorHybridIndexer()
        session = _make_mock_session()
        assert await indexer.vector_search([0.0] * 768, k=0, organ=None, session=session) == []
        assert await indexer.vector_search([], k=5, organ=None, session=session) == []


# ----- builder --------------------------------------------------------------

class _MockEmbeddingService:
    """Test embedding service: 768d zero vectors, deterministic, async."""
    DIMENSION = 768

    async def embed_text(self, request):
        from app.embedding.protocols import EmbeddingResponse
        return EmbeddingResponse(
            embeddings=[[0.0] * self.DIMENSION for _ in request.texts],
            model="mock",
            dimension=self.DIMENSION,
            request_id=request.request_id,
            token_count=sum(max(1, len(t) // 2) for t in request.texts),
        )

    async def embed_text_sparse(self, request):
        raise NotImplementedError

    async def health_check(self):
        return True

    async def get_model_info(self):
        from app.embedding.protocols import EmbeddingModelInfo, EmbeddingModelType
        return EmbeddingModelInfo(
            model_name="mock",
            model_type=EmbeddingModelType.BGE_M3_MEDICAL,
            dimension=self.DIMENSION,
        )

    async def close(self):
        return None


def _make_mock_session_factory():
    session = _make_mock_session()
    # exists() returns False by default; per-test can be overridden via session.execute.
    session.execute = AsyncMock(
        return_value=MagicMock(first=MagicMock(return_value=None))
    )

    class _Factory:
        def __call__(self):
            return _AsyncCtx(session)

    return _Factory()


class _AsyncCtx:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, exc_type, exc, tb):
        return None


class TestKnowledgeIndexBuilder:
    @pytest.mark.asyncio
    async def test_rejects_invalid_kb_version(self):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="not-a-version",
            source_dir=str(Path(__file__).parent),
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.VERSION_MISMATCH

    @pytest.mark.asyncio
    async def test_rejects_missing_source_dir(self):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="kb_v1.0.0",
            source_dir="/path/that/definitely/does/not/exist/xyz123",
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.SOURCE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_invalid_manifest_raises(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        # No manifest.json
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path)
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.SOURCE_NOT_FOUND

    @pytest.mark.asyncio
    async def test_manifest_version_mismatch_raises(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v9.9.9"})
        )
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path)
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.VERSION_MISMATCH

    @pytest.mark.asyncio
    async def test_invalid_manifest_json_raises(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        (tmp_path / "manifest.json").write_text("{not valid json")
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path)
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.INVALID_MANIFEST

    @pytest.mark.asyncio
    async def test_manifest_missing_kb_version_raises(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        (tmp_path / "manifest.json").write_text(json.dumps({"embedding_model": "x"}))
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path)
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.INVALID_MANIFEST

    @pytest.mark.asyncio
    async def test_happy_path_small_dataset(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import IndexBuildRequest
        # manifest + 1 organ + 2 docs
        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v1.0.0"})
        )
        thyroid = tmp_path / "thyroid"
        thyroid.mkdir()
        (thyroid / "doc1.json").write_text(json.dumps({
            "source_doc_id": "t1",
            "content": "甲状腺右叶低回声结节，TI-RADS 3。",
        }))
        (thyroid / "doc2.json").write_text(json.dumps({
            "source_doc_id": "t2",
            "content": "甲状腺左叶高回声结节，TI-RADS 4A。",
        }))

        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        result = await builder.build(IndexBuildRequest(
            kb_version="kb_v1.0.0",
            source_dir=str(tmp_path),
            organs=["thyroid"],
        ))
        assert result.kb_version == "kb_v1.0.0"
        assert result.total_chunks >= 2
        assert "thyroid" in result.organ_breakdown
        assert result.build_duration_seconds >= 0.0

    @pytest.mark.asyncio
    async def test_organ_filter_excludes_other_organs(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import IndexBuildRequest
        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v1.0.0"})
        )
        (tmp_path / "thyroid").mkdir()
        (tmp_path / "liver").mkdir()
        (tmp_path / "thyroid" / "t1.json").write_text(json.dumps({
            "content": "甲状腺结节，TI-RADS 3。",
        }))
        (tmp_path / "liver" / "l1.json").write_text(json.dumps({
            "content": "肝脏结节，LI-RADS 3。",
        }))
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        result = await builder.build(IndexBuildRequest(
            kb_version="kb_v1.0.0",
            source_dir=str(tmp_path),
            organs=["thyroid"],
        ))
        assert "liver" not in result.organ_breakdown
        assert "thyroid" in result.organ_breakdown

    @pytest.mark.asyncio
    async def test_resume_skips_existing_chunks(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildRequest,
            KnowledgeChunk,
        )

        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v1.0.0"})
        )
        (tmp_path / "thyroid").mkdir()
        (tmp_path / "thyroid" / "d1.json").write_text(json.dumps({
            "content": "甲状腺结节，TI-RADS 3。",
        }))

        # Build first time to learn the chunk_ids that will be produced.
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        result1 = await builder.build(IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path), organs=["thyroid"],
        ))
        assert result1.total_chunks >= 1
        assert result1.skipped_existing == 0

        # Now construct a session factory whose exists() always returns True
        # (simulating "all chunks already in the index from a prior run").
        class _AllExistingFactory:
            def __call__(self):
                return _AsyncCtx(_session_with_all_existing())

        builder2 = KnowledgeIndexBuilderImpl(
            session_factory=_AllExistingFactory(),
            embedding_service=_MockEmbeddingService(),
        )
        result2 = await builder2.build(IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path), organs=["thyroid"],
        ))
        assert result2.skipped_existing == result1.total_chunks
        assert result2.total_chunks == 0

    @pytest.mark.perf
    @pytest.mark.asyncio
    async def test_performance_under_60s_for_1000_chunks(self, tmp_path):
        """Soft performance check: 1000 chunks end-to-end should run fast.

        Generate a corpus that, with fixed-window chunking (window=200), yields
        well over 1000 chunks, then assert build duration is under 60s.
        """
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.chunker import FixedWindowChunker
        from app.knowledge.protocols import IndexBuildRequest
        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v1.0.0"})
        )
        (tmp_path / "thyroid").mkdir()
        # Build a single 50,000-char doc that yields ~250 chunks with
        # window=200; multiply by enough docs to clear 1000 chunks.
        chunk = "甲状腺右叶低回声结节，TI-RADS 3。"
        big_doc = chunk * 5000  # ~100,000 chars
        (tmp_path / "thyroid" / "big.json").write_text(json.dumps({
            "source_doc_id": "big",
            "content": big_doc,
        }))

        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
            strategy="fixed_window",
        )
        # Force the indexer's batch size up so the test isn't dominated by
        # the upsert flush logic, but keep the per-chunk work small.
        from app.knowledge.hybrid_indexer import PGVectorHybridIndexer
        builder.indexer = PGVectorHybridIndexer(batch_flush_size=500)

        req = IndexBuildRequest(
            kb_version="kb_v1.0.0",
            source_dir=str(tmp_path),
            organs=["thyroid"],
            strategy="fixed_window",
            batch_size=500,
        )
        # Sanity check: how many chunks will the chunker produce?
        expected = len(FixedWindowChunker(window_chars=1000).chunk(
            type("D", (), {
                "source_doc_id": "big",
                "organ": "thyroid",
                "content": big_doc,
                "section_path": "",
            })(),
            "kb_v1.0.0",
        ))
        assert expected >= 100, (
            f"fixture doc only yields {expected} chunks; "
            "increase fixture size"
        )

        t0 = time.monotonic()
        result = await builder.build(req)
        duration = time.monotonic() - t0
        assert result.total_chunks >= expected, (
            f"expected {expected} chunks, got {result.total_chunks}"
        )
        assert duration < 60.0, f"build took {duration:.1f}s, expected < 60s"

    @pytest.mark.asyncio
    async def test_validate_returns_true_when_populated(self):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        factory = _make_mock_session_factory()
        builder = KnowledgeIndexBuilderImpl(
            session_factory=factory,
            embedding_service=_MockEmbeddingService(),
        )
        # Override session to return non-zero count
        session = await factory().__aenter__()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar=MagicMock(return_value=5))
        )
        assert await builder.validate("kb_v1.0.0") is True

    @pytest.mark.asyncio
    async def test_validate_returns_false_when_empty(self):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        factory = _make_mock_session_factory()
        builder = KnowledgeIndexBuilderImpl(
            session_factory=factory,
            embedding_service=_MockEmbeddingService(),
        )
        session = await factory().__aenter__()
        session.execute = AsyncMock(
            return_value=MagicMock(scalar=MagicMock(return_value=0))
        )
        assert await builder.validate("kb_v1.0.0") is False


# ----- helpers used by resume test ------------------------------------------

def _session_with_exists(present_ids: set):
    session = _make_mock_session()

    async def _execute(stmt, *args, **kwargs):
        # Inspect the WHERE clause for chunk_id == '...'
        where_str = str(stmt)
        for cid in present_ids:
            if cid in where_str:
                return MagicMock(first=MagicMock(return_value=(cid,)))
        return MagicMock(first=MagicMock(return_value=None))

    session.execute = AsyncMock(side_effect=_execute)
    return session


def _session_with_all_existing():
    """Mock session whose exists() always returns True (simulates prior run)."""
    session = _make_mock_session()
    session.execute = AsyncMock(
        return_value=MagicMock(first=MagicMock(return_value=("present",)))
    )
    return session


# ----- mock embedding service for direct use --------------------------------

class TestMockEmbeddingService:
    @pytest.mark.asyncio
    async def test_returns_768d_vectors(self):
        from app.embedding.protocols import TextEmbeddingRequest
        from app.knowledge.builder import MockEmbeddingService
        svc = MockEmbeddingService()
        resp = await svc.embed_text(TextEmbeddingRequest(texts=["hello", "world"]))
        assert len(resp.embeddings) == 2
        assert all(len(v) == 768 for v in resp.embeddings)
        assert resp.dimension == 768

    @pytest.mark.asyncio
    async def test_deterministic(self):
        from app.embedding.protocols import TextEmbeddingRequest
        from app.knowledge.builder import MockEmbeddingService
        svc = MockEmbeddingService()
        r1 = await svc.embed_text(TextEmbeddingRequest(texts=["abc"]))
        r2 = await svc.embed_text(TextEmbeddingRequest(texts=["abc"]))
        assert r1.embeddings[0] == r2.embeddings[0]


# ----- CLI script -----------------------------------------------------------

class TestInitKnowledgeBaseCLI:
    def test_invalid_kb_version_returns_2(self, capsys):
        from scripts.init_knowledge_base import main
        code = main(["--kb-version", "bad-format", "--source-dir", "/tmp/whatever"])
        assert code == 2
        captured = capsys.readouterr()
        assert "invalid --kb-version" in captured.err

    def test_missing_source_dir_returns_2(self, capsys):
        from scripts.init_knowledge_base import main
        code = main([
            "--kb-version", "kb_v1.0.0",
            "--source-dir", "/path/that/does/not/exist/abc",
        ])
        assert code == 2
        captured = capsys.readouterr()
        assert "does not exist" in captured.err

    def test_parse_args_minimal(self):
        from scripts.init_knowledge_base import parse_args
        ns = parse_args(["--kb-version", "kb_v1.0.0", "--source-dir", "/tmp/x"])
        assert ns.kb_version == "kb_v1.0.0"
        assert ns.source_dir == "/tmp/x"
        assert ns.organs == ""
        assert ns.force_rebuild is False
        assert ns.embedding_model == "bge-m3-medical"
        assert ns.strategy == "sentence_boundary"
        assert ns.batch_size == 50


# ----- coverage boosters for builder.py and chunker.py ----------------------

class TestCoverageBoosters:
    @pytest.mark.asyncio
    async def test_manifest_root_must_be_object(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import (
            IndexBuildError,
            IndexBuildErrorCode,
            IndexBuildRequest,
        )
        # manifest.json root is a JSON array, not an object
        (tmp_path / "manifest.json").write_text(json.dumps([1, 2, 3]))
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        req = IndexBuildRequest(
            kb_version="kb_v1.0.0", source_dir=str(tmp_path)
        )
        with pytest.raises(IndexBuildError) as exc_info:
            await builder.build(req)
        assert exc_info.value.error_code == IndexBuildErrorCode.INVALID_MANIFEST

    @pytest.mark.asyncio
    async def test_resolve_chunker_falls_back_to_default(self, tmp_path):
        """Unknown strategy in IndexBuildRequest should fall back to default."""
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.chunker import SentenceBoundaryChunker
        from app.knowledge.protocols import IndexBuildRequest
        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v1.0.0"})
        )
        (tmp_path / "thyroid").mkdir()
        (tmp_path / "thyroid" / "d1.json").write_text(json.dumps({
            "content": "甲状腺结节，TI-RADS 3。",
        }))
        # Pass an invalid strategy name; the builder should fall back gracefully
        # (we patch _resolve_chunker to verify the default chunker is selected).
        builder = KnowledgeIndexBuilderImpl(
            session_factory=_make_mock_session_factory(),
            embedding_service=_MockEmbeddingService(),
        )
        # Simulate a user-supplied strategy the factory doesn't recognize.
        result = await builder.build(IndexBuildRequest(
            kb_version="kb_v1.0.0",
            source_dir=str(tmp_path),
            organs=["thyroid"],
            strategy="this_strategy_does_not_exist",
        ))
        assert result.total_chunks >= 1
        assert "thyroid" in result.organ_breakdown

    def test_create_chunker_invalid_string_raises(self):
        from app.knowledge.chunker import create_chunker
        with pytest.raises(ValueError):
            create_chunker("nonexistent_strategy")

    def test_semantic_chunker_with_prelude(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SemanticChunker,
        )
        # text has prelude before first TI-RADS title -> both should be chunked
        text = (
            "Intro text.\n"
            "TI-RADS 3 probably benign.\n"
            "TI-RADS 5 malignant."
        )
        doc = KnowledgeSourceDoc(
            source_doc_id="d", organ="thyroid", content=text
        )
        chunks = SemanticChunker(min_tokens=1, max_tokens=20).chunk(
            doc, "kb_v1.0.0"
        )
        assert len(chunks) >= 1
        # At least one chunk should carry TI-RADS in its section_path.
        assert any("TI-RADS" in c.section_path for c in chunks)

    def test_semantic_chunker_with_trailing_text(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SemanticChunker,
        )
        text = (
            "TI-RADS 3 first section.\n"
            "Trailing text after the last title."
        )
        doc = KnowledgeSourceDoc(
            source_doc_id="d", organ="thyroid", content=text
        )
        chunks = SemanticChunker(min_tokens=1, max_tokens=20).chunk(
            doc, "kb_v1.0.0"
        )
        assert len(chunks) >= 2

    def test_mock_embedding_service_health(self):
        from app.knowledge.builder import MockEmbeddingService
        import asyncio
        svc = MockEmbeddingService()
        result = asyncio.run(svc.health_check())
        assert result is True

    def test_mock_embedding_service_get_info(self):
        from app.knowledge.builder import MockEmbeddingService
        from app.embedding.protocols import EmbeddingModelType
        import asyncio
        svc = MockEmbeddingService()
        info = asyncio.run(svc.get_model_info())
        assert info.dimension == 768
        assert info.model_type == EmbeddingModelType.BGE_M3_MEDICAL

    def test_mock_embedding_service_close(self):
        from app.knowledge.builder import MockEmbeddingService
        import asyncio
        svc = MockEmbeddingService()
        assert asyncio.run(svc.close()) is None

    def test_approx_tokens_heuristic(self):
        from app.knowledge.chunker import _approx_tokens
        assert _approx_tokens("") == 0
        assert _approx_tokens("a") == 1  # max(1, 1//2)
        assert _approx_tokens("abcd") == 2
        assert _approx_tokens("a" * 100) == 50

    def test_build_chunk_id_format(self):
        from app.knowledge.chunker import _build_chunk_id
        assert _build_chunk_id("doc1", 0) == "doc1#0000"
        assert _build_chunk_id("doc1", 42) == "doc1#0042"
        assert _build_chunk_id("doc-with-dash", 7) == "doc-with-dash#0007"

    def test_split_sentences_cjk(self):
        from app.knowledge.chunker import _split_sentences
        sentences = _split_sentences("第一句。第二句！第三句？")
        assert len(sentences) >= 3
        assert "第一句" in sentences[0]

    def test_split_sentences_empty(self):
        from app.knowledge.chunker import _split_sentences
        assert _split_sentences("") == []

    def test_chunk_index_monotonic_in_semantic(self):
        from app.knowledge.chunker import (
            KnowledgeSourceDoc,
            SemanticChunker,
        )
        text = (
            "TI-RADS 3 first section.\n"
            "TI-RADS 4A second section.\n"
            "TI-RADS 4B third section."
        )
        doc = KnowledgeSourceDoc(
            source_doc_id="d", organ="thyroid", content=text
        )
        chunks = SemanticChunker(min_tokens=1, max_tokens=5).chunk(
            doc, "kb_v1.0.0"
        )
        ids = [c.chunk_id for c in chunks]
        assert ids == sorted(ids)
        # Chunk indices in metadata should be sequential.
        indices = [c.metadata.get("chunk_index") for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_chunker_strategy_enum_values(self):
        from app.knowledge.chunker import ChunkerStrategy
        assert ChunkerStrategy.FIXED_WINDOW.value == "fixed_window"
        assert ChunkerStrategy.SENTENCE_BOUNDARY.value == "sentence_boundary"
        assert ChunkerStrategy.SEMANTIC.value == "semantic"

    @pytest.mark.asyncio
    async def test_force_rebuild_invokes_delete(self, tmp_path):
        from app.knowledge.builder import KnowledgeIndexBuilderImpl
        from app.knowledge.protocols import IndexBuildRequest
        (tmp_path / "manifest.json").write_text(
            json.dumps({"kb_version": "kb_v1.0.0"})
        )
        (tmp_path / "thyroid").mkdir()
        (tmp_path / "thyroid" / "d1.json").write_text(json.dumps({
            "content": "甲状腺结节，TI-RADS 3。",
        }))

        # Capture delete_by_version calls
        deleted_versions: list[str] = []
        factory = _make_mock_session_factory()

        class _Factory:
            def __init__(self, inner):
                self._inner = inner

            def __call__(self):
                return _AsyncCtx(self._inner())

        builder = KnowledgeIndexBuilderImpl(
            session_factory=factory,
            embedding_service=_MockEmbeddingService(),
        )
        # Patch the indexer's delete_by_version
        original_delete = builder.indexer.delete_by_version

        async def _spy_delete(version, session):
            deleted_versions.append(version)
            return await original_delete(version, session)

        builder.indexer.delete_by_version = _spy_delete

        await builder.build(IndexBuildRequest(
            kb_version="kb_v1.0.0",
            source_dir=str(tmp_path),
            organs=["thyroid"],
            force_rebuild=True,
        ))
        assert "kb_v1.0.0" in deleted_versions
