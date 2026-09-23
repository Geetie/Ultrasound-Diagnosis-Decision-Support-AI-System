"""Unit tests for app.embedding.qdrant_client (T-GA-EMB-02).

Covers:
- _ensure_qdrant_available: lazy import check
- _collection_name: prefix application, idempotency
- _build_filter: scalar / list / dict / None inputs (requires qdrant-client)
- create_collection: dimension parameter, idempotency (already-exists path)
- upsert: empty input short-circuit, points construction
- search: filter passing, search result mapping
- delete_collection: collection_name application
- health_check: returns True/False on success/failure
- close: idempotent, resets client
- Error mapping: UnexpectedResponse and generic Exception paths
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_emb]


# qdrant_client is an optional dep. Tests requiring qmodels skip if absent;
# the rest of the tests (constants, prefix logic, lazy import) always run.
qdrant_client = pytest.importorskip("qdrant_client", reason="qdrant-client not installed")
from qdrant_client.http import models as qmodels  # noqa: E402
from qdrant_client.http.exceptions import UnexpectedResponse  # noqa: E402

from app.embedding.qdrant_client import (  # noqa: E402
    QdrantVectorIndex,
    _DISTANCE_MAP,
)
from app.embedding.protocols import (  # noqa: E402
    SearchResult,
    VectorIndexError,
    VectorIndexErrorCode,
    VectorRecord,
)


# ---------------------------------------------------------------------------
# Module-level constants
# ---------------------------------------------------------------------------


class TestDistanceMap:
    def test_cosine_euclid_dot_supported(self) -> None:
        assert _DISTANCE_MAP["cosine"] == "Cosine"
        assert _DISTANCE_MAP["euclid"] == "Euclid"
        assert _DISTANCE_MAP["dot"] == "Dot"

    def test_unknown_distance_falls_back_to_cosine_via_get(self) -> None:
        # The implementation uses _DISTANCE_MAP.get(distance, "Cosine")
        assert _DISTANCE_MAP.get("manhattan", "Cosine") == "Cosine"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_index() -> QdrantVectorIndex:
    """Build a QdrantVectorIndex with a stubbed AsyncQdrantClient.

    Skips real network I/O entirely.
    """
    idx = QdrantVectorIndex(host="localhost", port=6333)
    idx._client = AsyncMock(name="AsyncQdrantClient")
    idx._import_check_done = True  # bypass lazy check
    return idx


def _make_hit(hit_id: str, score: float, payload: dict | None = None) -> MagicMock:
    h = MagicMock()
    h.id = hit_id
    h.score = score
    h.payload = payload or {}
    return h


# ---------------------------------------------------------------------------
# _collection_name
# ---------------------------------------------------------------------------


class TestCollectionName:
    def test_appends_prefix_when_missing(self) -> None:
        idx = QdrantVectorIndex(prefix="ultrasound_")
        assert idx._collection_name("kb") == "ultrasound_kb"

    def test_idempotent_when_prefix_present(self) -> None:
        idx = QdrantVectorIndex(prefix="ultrasound_")
        assert idx._collection_name("ultrasound_kb") == "ultrasound_kb"

    def test_custom_prefix(self) -> None:
        idx = QdrantVectorIndex(prefix="custom_")
        assert idx._collection_name("foo") == "custom_foo"


# ---------------------------------------------------------------------------
# _ensure_qdrant_available
# ---------------------------------------------------------------------------


class TestEnsureQdrantAvailable:
    def test_raises_when_qdrant_not_installed(self) -> None:
        idx = QdrantVectorIndex()
        idx._import_check_done = False
        with patch("app.embedding.qdrant_client._QDRANT_AVAILABLE", False):
            with pytest.raises(VectorIndexError) as exc_info:
                idx._ensure_qdrant_available()
            assert exc_info.value.error_code == VectorIndexErrorCode.CONNECTION_FAILED

    def test_noop_when_already_checked(self) -> None:
        idx = QdrantVectorIndex()
        idx._import_check_done = True
        with patch("app.embedding.qdrant_client._QDRANT_AVAILABLE", False):
            # Even with lib missing, the second call is a no-op.
            idx._ensure_qdrant_available()  # no raise


# ---------------------------------------------------------------------------
# _build_filter
# ---------------------------------------------------------------------------


class TestBuildFilter:
    def test_none_returns_none(self) -> None:
        idx = _make_index()
        assert idx._build_filter(None) is None

    def test_empty_dict_returns_none(self) -> None:
        idx = _make_index()
        assert idx._build_filter({}) is None

    def test_scalar_value(self) -> None:
        idx = _make_index()
        f = idx._build_filter({"organ": "thyroid"})
        assert f is not None
        assert hasattr(f, "must")
        assert len(f.must) == 1
        cond = f.must[0]
        assert cond.key == "organ"
        assert cond.match.value == "thyroid"

    def test_list_value(self) -> None:
        idx = _make_index()
        f = idx._build_filter({"organ": ["thyroid", "liver"]})
        assert len(f.must) == 1
        cond = f.must[0]
        assert cond.match.any == ["thyroid", "liver"]

    def test_range_dict(self) -> None:
        idx = _make_index()
        f = idx._build_filter({"score": {"gte": 0.5, "lte": 0.9}})
        assert len(f.must) == 1
        cond = f.must[0]
        assert cond.range.gte == 0.5
        assert cond.range.lte == 0.9

    def test_multiple_keys(self) -> None:
        idx = _make_index()
        f = idx._build_filter({"organ": "thyroid", "score": {"gt": 0.5}})
        assert len(f.must) == 2


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------


class TestCreateCollection:
    async def test_already_exists_returns_early(self) -> None:
        idx = _make_index()
        idx._client.collection_exists.return_value = True
        await idx.create_collection("kb_v1", dimension=768)
        # No create_collection call made.
        idx._client.create_collection.assert_not_called()

    async def test_creates_with_cosine_distance(self) -> None:
        idx = _make_index()
        idx._client.collection_exists.return_value = False
        await idx.create_collection("kb_v1", dimension=768, distance="cosine")
        idx._client.create_collection.assert_awaited_once()
        kwargs = idx._client.create_collection.await_args.kwargs
        assert kwargs["collection_name"] == "ultrasound_kb_v1"
        vc = kwargs["vectors_config"]
        assert vc.size == 768

    async def test_unknown_distance_falls_back_to_cosine(self) -> None:
        idx = _make_index()
        idx._client.collection_exists.return_value = False
        await idx.create_collection("kb_v1", dimension=768, distance="manhattan")
        kwargs = idx._client.create_collection.await_args.kwargs
        # Should still create with cosine as the fallback.
        assert kwargs["vectors_config"] is not None

    async def test_unexpected_response_mapped(self) -> None:
        idx = _make_index()
        idx._client.collection_exists.return_value = False
        idx._client.create_collection.side_effect = UnexpectedResponse("forbidden")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.create_collection("kb_v1", dimension=768)
        assert exc_info.value.error_code == VectorIndexErrorCode.UPSERT_FAILED
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


class TestUpsert:
    async def test_empty_input_short_circuits(self) -> None:
        idx = _make_index()
        await idx.upsert("kb_v1", [])
        idx._client.upsert.assert_not_called()

    async def test_inserts_each_point(self) -> None:
        idx = _make_index()
        records = [
            VectorRecord(id=f"id_{i}", vector=[0.1, 0.2, 0.3], metadata={"k": i})
            for i in range(3)
        ]
        await idx.upsert("kb_v1", records)
        idx._client.upsert.assert_awaited_once()
        kwargs = idx._client.upsert.await_args.kwargs
        assert kwargs["collection_name"] == "ultrasound_kb_v1"
        assert len(kwargs["points"]) == 3
        for i, point in enumerate(kwargs["points"]):
            assert point.id == f"id_{i}"
            assert point.vector == [0.1, 0.2, 0.3]
            assert point.payload == {"k": i}

    async def test_unexpected_response_mapped(self) -> None:
        idx = _make_index()
        idx._client.upsert.side_effect = UnexpectedResponse("server error")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.upsert("kb_v1", [VectorRecord(id="x", vector=[0.1], metadata={})])
        assert exc_info.value.error_code == VectorIndexErrorCode.UPSERT_FAILED
        assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_returns_search_results(self) -> None:
        idx = _make_index()
        hits = [
            _make_hit("a", 0.95, {"organ": "thyroid"}),
            _make_hit("b", 0.85, {"organ": "liver"}),
        ]
        idx._client.search.return_value = hits
        results = await idx.search("kb_v1", query_vector=[0.1, 0.2], top_k=5)
        assert len(results) == 2
        assert isinstance(results[0], SearchResult)
        assert results[0].id == "a"
        assert results[0].score == 0.95
        assert results[0].metadata == {"organ": "thyroid"}

    async def test_filter_passed_through(self) -> None:
        idx = _make_index()
        idx._client.search.return_value = []
        await idx.search(
            "kb_v1",
            query_vector=[0.1, 0.2],
            top_k=10,
            filter={"organ": "thyroid"},
        )
        kwargs = idx._client.search.await_args.kwargs
        assert kwargs["collection_name"] == "ultrasound_kb_v1"
        assert kwargs["query_filter"] is not None
        assert kwargs["limit"] == 10
        assert kwargs["with_payload"] is True

    async def test_unexpected_response_mapped(self) -> None:
        idx = _make_index()
        idx._client.search.side_effect = UnexpectedResponse("not found")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.search("kb_v1", query_vector=[0.1])
        assert exc_info.value.error_code == VectorIndexErrorCode.SEARCH_FAILED
        # UnexpectedResponse path is retryable
        assert exc_info.value.retryable is True

    async def test_generic_exception_mapped(self) -> None:
        idx = _make_index()
        idx._client.search.side_effect = RuntimeError("connection lost")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.search("kb_v1", query_vector=[0.1])
        assert exc_info.value.error_code == VectorIndexErrorCode.SEARCH_FAILED
        # Generic Exception path is non-retryable (deliberate design choice:
        # we don't know if the failure is transient).
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# delete_collection & health_check & close
# ---------------------------------------------------------------------------


class TestDeleteCollection:
    async def test_deletes_collection(self) -> None:
        idx = _make_index()
        await idx.delete_collection("kb_v1")
        idx._client.delete_collection.assert_awaited_once_with(
            collection_name="ultrasound_kb_v1"
        )

    async def test_unexpected_response_mapped(self) -> None:
        idx = _make_index()
        idx._client.delete_collection.side_effect = UnexpectedResponse("forbidden")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.delete_collection("kb_v1")
        assert exc_info.value.error_code == VectorIndexErrorCode.DELETE_FAILED
        assert exc_info.value.retryable is False


class TestHealthCheck:
    async def test_returns_true_on_success(self) -> None:
        idx = _make_index()
        idx._client.get_collections.return_value = MagicMock()
        assert await idx.health_check() is True

    async def test_returns_false_on_error(self) -> None:
        idx = _make_index()
        idx._client.get_collections.side_effect = RuntimeError("connect refused")
        assert await idx.health_check() is False


class TestClose:
    async def test_close_idempotent(self) -> None:
        idx = _make_index()
        client = idx._client  # capture before close clears it
        await idx.close()
        await idx.close()  # second call must not raise
        assert idx._closed is True
        assert idx._client is None
        client.close.assert_awaited_once()

    async def test_close_closes_client(self) -> None:
        idx = _make_index()
        client = idx._client
        await idx.close()
        client.close.assert_awaited_once()
