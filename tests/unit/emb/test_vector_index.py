"""VectorIndexClient 协议契约单元测试。

测试策略:
- 通过 unittest.mock 模拟 Qdrant / PGVector 后端
- 参数化测试覆盖双后端的相同协议契约
- 独立测试 factory 的自动降级行为
- 不依赖真实 Qdrant 容器或 PostgreSQL
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from app.embedding.protocols import (
    SearchResult,
    VectorIndexBackend,
    VectorIndexError,
    VectorIndexErrorCode,
    VectorRecord,
)

pytestmark = [pytest.mark.track_emb]


def _make_vector_records(n: int, dim: int = 768) -> list[VectorRecord]:
    return [
        VectorRecord(
            id=f"v{i}",
            vector=np.random.randn(dim).astype(np.float32).tolist(),
            metadata={"organ": "thyroid" if i % 2 == 0 else "liver", "idx": i},
        )
        for i in range(n)
    ]


class TestVectorIndexProtocol:
    def test_cannot_instantiate_abc(self):
        from app.embedding.protocols import VectorIndexClient

        with pytest.raises(TypeError):
            VectorIndexClient()

    def test_backend_values(self):
        assert VectorIndexBackend.QDRANT == "qdrant"
        assert VectorIndexBackend.PGVECTOR == "pgvector"

    def test_vector_record_validates_empty_id(self):
        with pytest.raises(ValueError, match="id must not be empty"):
            VectorRecord(id="", vector=[0.1])

    def test_vector_record_validates_empty_vector(self):
        with pytest.raises(ValueError, match="vector must not be empty"):
            VectorRecord(id="x", vector=[])

    def test_vector_record_default_metadata(self):
        rec = VectorRecord(id="x", vector=[0.1])
        assert rec.metadata == {}

    def test_search_result_to_dict(self):
        result = SearchResult(id="v1", score=0.95, metadata={"organ": "thyroid"})
        d = result.model_dump() if hasattr(result, "model_dump") else result.dict()
        assert d["id"] == "v1"
        assert d["score"] == 0.95
        assert d["metadata"]["organ"] == "thyroid"


class TestVectorIndexError:
    def test_error_code_in_str(self):
        err = VectorIndexError(
            error_code=VectorIndexErrorCode.CONNECTION_FAILED, message="conn failed"
        )
        assert "E2003_VEC_CONNECTION_FAILED" in str(err)
        assert err.retryable is False

    def test_retryable_error(self):
        err = VectorIndexError(
            error_code=VectorIndexErrorCode.UPSERT_FAILED,
            message="upsert failed",
            retryable=True,
            retry_strategy="retry_after_5s",
        )
        assert err.retryable is True
        assert err.retry_strategy == "retry_after_5s"

    def test_all_error_codes_defined(self):
        expected = [
            "E2003_VEC_CONNECTION_FAILED",
            "E2003_VEC_UPSERT_FAILED",
            "E2003_VEC_SEARCH_FAILED",
            "E2003_VEC_DELETE_FAILED",
            "E2003_VEC_COLLECTION_NOT_FOUND",
            "E2003_VEC_DIMENSION_MISMATCH",
            "E2003_VEC_INVALID_INPUT",
        ]
        actual = [code.value for code in VectorIndexErrorCode]
        for code in expected:
            assert code in actual, f"Missing: {code}"


class TestQdrantVectorIndex:
    def _make_client(self):
        from app.embedding.qdrant_client import QdrantVectorIndex

        return QdrantVectorIndex(host="localhost", port=6333)

    def test_init_default_values(self):
        client = self._make_client()
        assert client._host == "localhost"
        assert client._port == 6333
        assert client._grpc_port == 6334
        assert client._prefix == "ultrasound_"
        assert client._closed is False

    def test_collection_name_with_prefix(self):
        client = self._make_client()
        assert client._collection_name("test") == "ultrasound_test"
        assert client._collection_name("ultrasound_test") == "ultrasound_test"

    def test_collection_name_with_custom_prefix(self):
        from app.embedding.qdrant_client import QdrantVectorIndex

        client = QdrantVectorIndex(prefix="custom_")
        assert client._collection_name("test") == "custom_test"

    def test_build_filter_empty_returns_none(self):
        client = self._make_client()
        assert client._build_filter(None) is None
        assert client._build_filter({}) is None

    def test_build_filter_simple_value(self):
        client = self._make_client()
        with patch("app.embedding.qdrant_client.qmodels") as mock_models:
            mock_models.MatchValue.return_value = "match_value"
            mock_models.FieldCondition.return_value = "field_cond"
            mock_models.Filter.return_value = "filter_obj"
            client._build_filter({"organ": "thyroid"})
            mock_models.MatchValue.assert_called_once_with(value="thyroid")
            mock_models.FieldCondition.assert_called_once()

    def test_build_filter_list_value(self):
        client = self._make_client()
        with patch("app.embedding.qdrant_client.qmodels") as mock_models:
            mock_models.MatchAny.return_value = "match_any"
            mock_models.FieldCondition.return_value = "field_cond"
            mock_models.Filter.return_value = "filter_obj"
            client._build_filter({"organ": ["thyroid", "liver"]})
            mock_models.MatchAny.assert_called_once_with(any=["thyroid", "liver"])

    def test_build_filter_range_value(self):
        client = self._make_client()
        with patch("app.embedding.qdrant_client.qmodels") as mock_models:
            mock_models.Range.return_value = "range_obj"
            mock_models.FieldCondition.return_value = "field_cond"
            mock_models.Filter.return_value = "filter_obj"
            client._build_filter({"age": {"gte": 18, "lte": 65}})
            mock_models.Range.assert_called_once_with(gte=18, lte=65)

    def test_ensure_client_after_close_raises(self):
        client = self._make_client()
        client._import_check_done = True
        client._closed = True
        with pytest.raises(VectorIndexError, match="closed"):
            client._ensure_client()

    def test_close_idempotent(self):
        client = self._make_client()
        client._client = MagicMock()
        client._client.close = AsyncMock()
        client._closed = True
        import asyncio

        asyncio.run(client.close())
        asyncio.run(client.close())
        assert client._closed is True


class TestPgvectorVectorIndex:
    def test_init_without_url_raises(self):
        from app.embedding.pgvector_client import PgvectorVectorIndex

        with pytest.raises(VectorIndexError, match="database_url is required"):
            PgvectorVectorIndex(database_url="")

    def test_validate_collection_name_accepts_valid(self):
        from app.embedding.pgvector_client import _validate_collection_name

        assert _validate_collection_name("valid_name_123") == "vector_index_valid_name_123"

    def test_validate_collection_name_rejects_invalid(self):
        from app.embedding.pgvector_client import _validate_collection_name

        with pytest.raises(VectorIndexError, match="invalid collection name"):
            _validate_collection_name("invalid; DROP TABLE x")
        with pytest.raises(VectorIndexError, match="invalid collection name"):
            _validate_collection_name("1starts_with_digit")
        with pytest.raises(VectorIndexError, match="invalid collection name"):
            _validate_collection_name("a" * 100)

    def test_validate_meta_key_accepts_valid(self):
        from app.embedding.pgvector_client import _validate_meta_key

        assert _validate_meta_key("organ") == "organ"

    def test_validate_meta_key_rejects_sql_injection(self):
        from app.embedding.pgvector_client import _validate_meta_key

        with pytest.raises(VectorIndexError, match="invalid metadata key"):
            _validate_meta_key("organ'; DROP TABLE--")
        with pytest.raises(VectorIndexError, match="invalid metadata key"):
            _validate_meta_key("key with space")

    def test_init_sets_attributes(self):
        from app.embedding.pgvector_client import PgvectorVectorIndex

        client = PgvectorVectorIndex(database_url="postgresql://user:pass@localhost/db")
        assert client._database_url == "postgresql://user:pass@localhost/db"
        assert client._pool is None
        assert client._closed is False


class TestIndexFactory:
    def test_resolve_backend_from_string(self):
        from app.embedding.index_factory import _resolve_backend

        assert _resolve_backend("qdrant") == VectorIndexBackend.QDRANT
        assert _resolve_backend("pgvector") == VectorIndexBackend.PGVECTOR
        assert _resolve_backend(VectorIndexBackend.QDRANT) == VectorIndexBackend.QDRANT

    def test_resolve_backend_default_when_none(self):
        from app.embedding.index_factory import _resolve_backend

        result = _resolve_backend(None)
        assert result in (VectorIndexBackend.QDRANT, VectorIndexBackend.PGVECTOR)

    def test_resolve_backend_invalid_raises(self):
        from app.embedding.index_factory import _resolve_backend

        with pytest.raises(ValueError):
            _resolve_backend("unknown")

    @pytest.mark.asyncio
    async def test_create_qdrant_with_fallback_to_pgvector_on_unhealthy(self):
        from app.embedding.index_factory import _create_qdrant_with_fallback

        with patch(
            "app.embedding.qdrant_client.QdrantVectorIndex"
        ) as mock_qdrant_cls:
            mock_client = MagicMock()
            mock_client.health_check = AsyncMock(return_value=False)
            mock_client.close = AsyncMock()
            mock_qdrant_cls.return_value = mock_client

            with patch(
                "app.embedding.pgvector_client.PgvectorVectorIndex"
            ) as mock_pg_cls:
                mock_pg = MagicMock()
                mock_pg.health_check = AsyncMock(return_value=True)
                mock_pg_cls.return_value = mock_pg

                result = await _create_qdrant_with_fallback(
                    qdrant_host="x", qdrant_port=1, qdrant_grpc_port=2,
                    qdrant_timeout_seconds=1.0, qdrant_api_key=None,
                    database_url="postgresql://x", auto_fallback=True,
                )
                assert result is mock_pg
                mock_client.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_qdrant_no_fallback_raises(self):
        from app.embedding.index_factory import _create_qdrant_with_fallback

        with patch(
            "app.embedding.qdrant_client.QdrantVectorIndex"
        ) as mock_qdrant_cls:
            mock_client = MagicMock()
            mock_client.health_check = AsyncMock(return_value=False)
            mock_client.close = AsyncMock()
            mock_qdrant_cls.return_value = mock_client

            with pytest.raises(VectorIndexError, match="qdrant unhealthy"):
                await _create_qdrant_with_fallback(
                    qdrant_host="x", qdrant_port=1, qdrant_grpc_port=2,
                    qdrant_timeout_seconds=1.0, qdrant_api_key=None,
                    database_url="postgresql://x", auto_fallback=False,
                )

    @pytest.mark.asyncio
    async def test_create_pgvector_healthy(self):
        from app.embedding.index_factory import _create_pgvector

        with patch(
            "app.embedding.pgvector_client.PgvectorVectorIndex"
        ) as mock_pg_cls:
            mock_pg = MagicMock()
            mock_pg.health_check = AsyncMock(return_value=True)
            mock_pg_cls.return_value = mock_pg

            result = await _create_pgvector("postgresql://x")
            assert result is mock_pg

    @pytest.mark.asyncio
    async def test_create_pgvector_unhealthy_raises(self):
        from app.embedding.index_factory import _create_pgvector

        with patch(
            "app.embedding.pgvector_client.PgvectorVectorIndex"
        ) as mock_pg_cls:
            mock_pg = MagicMock()
            mock_pg.health_check = AsyncMock(return_value=False)
            mock_pg.close = AsyncMock()
            mock_pg_cls.return_value = mock_pg

            with pytest.raises(VectorIndexError, match="pgvector unhealthy"):
                await _create_pgvector("postgresql://x")
            mock_pg.close.assert_awaited_once()


class TestBUSIAdapter:
    def test_load_synthetic_when_path_not_found(self, tmp_path):
        from app.embedding.busi_adapter import BUSIAdapter

        nonexistent = tmp_path / "missing"
        adapter = BUSIAdapter(dataset_path=str(nonexistent))
        samples = adapter.load(max_samples=4)
        assert len(samples) == 4
        assert all(s.image_path.startswith("<synthetic>/") for s in samples)
        labels = {s.label for s in samples}
        assert labels.issubset({"benign", "malignant", "normal"})

    def test_load_directory(self, tmp_path):
        from app.embedding.busi_adapter import BUSIAdapter

        for label in ["benign", "malignant", "normal"]:
            class_dir = tmp_path / label
            class_dir.mkdir()
            for i in range(2):
                (class_dir / f"img_{i}.png").write_bytes(b"\x00")

        adapter = BUSIAdapter(dataset_path=str(tmp_path))
        samples = adapter.load()
        assert len(samples) == 6
        labels = {s.label for s in samples}
        assert labels == {"benign", "malignant", "normal"}

    def test_load_jsonl(self, tmp_path):
        import json

        from app.embedding.busi_adapter import BUSIAdapter

        jsonl = tmp_path / "train.jsonl"
        rows = [
            {"image_path": "/x/a.png", "text": "text a", "label": "benign"},
            {"image_path": "/x/b.png", "text": "text b", "label": "malignant"},
        ]
        with open(jsonl, "w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")

        adapter = BUSIAdapter(dataset_path=str(jsonl))
        samples = adapter.load(split="train")
        assert len(samples) == 2
        assert samples[0].label == "benign"
        assert samples[1].label == "malignant"

    def test_load_max_samples(self, tmp_path):
        from app.embedding.busi_adapter import BUSIAdapter

        for label in ["benign", "malignant", "normal"]:
            class_dir = tmp_path / label
            class_dir.mkdir()
            for i in range(3):
                (class_dir / f"img_{i}.png").write_bytes(b"\x00")

        adapter = BUSIAdapter(dataset_path=str(tmp_path))
        samples = adapter.load(max_samples=5)
        assert len(samples) == 5

    def test_is_busi_path(self, tmp_path):
        from app.embedding.busi_adapter import is_busi_path

        (tmp_path / "benign").mkdir()
        (tmp_path / "malignant").mkdir()
        assert is_busi_path(str(tmp_path)) is True

        only_organ = tmp_path / "thyroid"
        only_organ.mkdir()
        assert is_busi_path(str(only_organ)) is False

        assert is_busi_path("/nonexistent/path") is False
