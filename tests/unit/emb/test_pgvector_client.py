"""Unit tests for app.embedding.pgvector_client (T-GA-EMB-02).

Covers:
- Collection name validation (SQL-injection prevention)
- Metadata key validation
- Constructor invariants (database_url required, asyncpg/pgvector required)
- create_collection: dimension parameter, table_name construction
- upsert: empty input short-circuit, transaction wrapping, INSERT SQL
- search: SQL construction (with and without filter), dimension param
- search: filter handling for scalars, lists, dicts
- health_check: pool acquire, returns True/False on success/failure
- close: idempotent, resets pool
- UndefinedTableError → COLLECTION_NOT_FOUND
- PostgresError → SEARCH_FAILED / UPSERT_FAILED / DELETE_FAILED
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_emb]

# asyncpg is required for these tests; the codebase ships it, but we guard
# the test module so it cleanly skips in CI without it.
asyncpg = pytest.importorskip("asyncpg")
from asyncpg.exceptions import (  # noqa: E402
    PostgresError,
    UndefinedTableError,
)

from app.embedding.pgvector_client import (  # noqa: E402
    PgvectorVectorIndex,
    _validate_collection_name,
    _validate_meta_key,
)
from app.embedding.protocols import (  # noqa: E402
    VectorIndexError,
    VectorIndexErrorCode,
    VectorRecord,
)

# ---------------------------------------------------------------------------
# Collection / metadata key validation
# ---------------------------------------------------------------------------


class TestCollectionNameValidation:
    @pytest.mark.parametrize(
        "name",
        ["knowledge_chunks", "ultrasound_768", "a", "A1_b2_C3"],
    )
    def test_valid_collection_names_accepted(self, name: str) -> None:
        result = _validate_collection_name(name)
        assert result == f"vector_index_{name}"

    @pytest.mark.parametrize(
        "name",
        [
            "1leading_digit",  # starts with digit
            "has space",       # contains space
            "has-dash",        # contains dash
            "drop;table",      # SQL-injection attempt
            "' OR 1=1--",
            "",                # empty
            "a" * 64,          # too long (>62 chars)
        ],
    )
    def test_invalid_collection_names_rejected(self, name: str) -> None:
        with pytest.raises(VectorIndexError) as exc_info:
            _validate_collection_name(name)
        assert exc_info.value.error_code == VectorIndexErrorCode.INVALID_INPUT


class TestMetaKeyValidation:
    @pytest.mark.parametrize(
        "key",
        ["organ", "kb_version", "_internal", "a1b2c3"],
    )
    def test_valid_keys_accepted(self, key: str) -> None:
        assert _validate_meta_key(key) == key

    @pytest.mark.parametrize(
        "key",
        ["1leading", "key with space", "key-with-dash", "x" * 64, ""],
    )
    def test_invalid_keys_rejected(self, key: str) -> None:
        with pytest.raises(VectorIndexError) as exc_info:
            _validate_meta_key(key)
        assert exc_info.value.error_code == VectorIndexErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# Constructor invariants
# ---------------------------------------------------------------------------


class TestConstructor:
    def test_missing_asyncpg_or_pgvector_raises(self) -> None:
        """When asyncpg or pgvector is not installed, the constructor must
        raise with a clear message and a CONNECTION_FAILED code."""
        with patch("app.embedding.pgvector_client._PGVECTOR_AVAILABLE", False):
            with pytest.raises(VectorIndexError) as exc_info:
                PgvectorVectorIndex(database_url="postgresql://x")
            assert exc_info.value.error_code == VectorIndexErrorCode.CONNECTION_FAILED
            assert "asyncpg" in exc_info.value.detail

    def test_empty_database_url_raises(self) -> None:
        with pytest.raises(VectorIndexError) as exc_info:
            PgvectorVectorIndex(database_url="")
        assert exc_info.value.error_code == VectorIndexErrorCode.INVALID_INPUT


# ---------------------------------------------------------------------------
# Test scaffolding: build an index with a stubbed asyncpg pool
# ---------------------------------------------------------------------------


def _build_index() -> PgvectorVectorIndex:
    """Build a PgvectorVectorIndex with a stubbed pool, bypassing the
    asyncpg/pgvector import check."""
    with patch("app.embedding.pgvector_client._PGVECTOR_AVAILABLE", True):
        idx = PgvectorVectorIndex(database_url="postgresql://stub@localhost/db")
    pool = MagicMock(name="asyncpg.Pool")
    conn = AsyncMock(name="asyncpg.Connection")
    # asyncpg.Pool.acquire() returns an async context manager
    acm = AsyncMock(name="acm")
    acm.__aenter__ = AsyncMock(return_value=conn)
    acm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire.return_value = acm
    # asyncpg.Connection.transaction() is itself a regular call returning an
    # async context manager. Use a plain MagicMock so .transaction() returns
    # `tx` directly (NOT a coroutine wrapper).
    tx = MagicMock(name="tx")  # not AsyncMock — this is the context manager
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    # conn.close() is async in real life; mock it as a coroutine
    pool.close = AsyncMock(return_value=None)
    idx._pool = pool
    return idx, conn


# ---------------------------------------------------------------------------
# create_collection
# ---------------------------------------------------------------------------


class TestCreateCollection:
    async def test_creates_table_with_dimension(self) -> None:
        idx, conn = _build_index()
        await idx.create_collection("kb_v1", dimension=768)
        execute_calls = [c.args[0] for c in conn.execute.await_args_list]
        assert any("CREATE EXTENSION" in c for c in execute_calls)
        assert any(
            "vector_index_kb_v1" in c and "vector(768)" in c for c in execute_calls
        )
        assert any(
            "vector_index_kb_v1_embedding_idx" in c for c in execute_calls
        )

    async def test_invalid_collection_name_in_create(self) -> None:
        idx, _ = _build_index()
        with pytest.raises(VectorIndexError):
            await idx.create_collection("drop;table", dimension=768)

    async def test_postgres_error_mapped_to_upsert_failed(self) -> None:
        idx, conn = _build_index()
        conn.execute.side_effect = PostgresError("pg boom")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.create_collection("kb_v1", dimension=768)
        assert exc_info.value.error_code == VectorIndexErrorCode.UPSERT_FAILED
        assert exc_info.value.retryable is False

    async def test_ivfflat_creation_failure_is_logged_not_raised(self) -> None:
        """CREATE INDEX failing must be tolerated (logged, not raised)."""
        idx, conn = _build_index()
        # First two execute() calls succeed; third (CREATE INDEX) raises.
        conn.execute.side_effect = [None, None, RuntimeError("ivfflat not supported")]
        await idx.create_collection("kb_v1", dimension=768)
        # No exception raised.


# ---------------------------------------------------------------------------
# upsert
# ---------------------------------------------------------------------------


class TestUpsert:
    async def test_empty_input_short_circuits(self) -> None:
        idx, _ = _build_index()
        await idx.upsert("kb_v1", [])
        # No execute calls; the empty-list branch returns before acquiring.
        idx._pool.acquire.assert_not_called()

    async def test_inserts_each_record(self) -> None:
        idx, conn = _build_index()
        records = [
            VectorRecord(id=f"id_{i}", vector=[0.1, 0.2, 0.3], metadata={"k": i})
            for i in range(3)
        ]
        await idx.upsert("kb_v1", records)
        # conn.transaction() was used as the async context manager
        # (`async with conn.transaction():`). tx.__aenter__/__aexit__ are
        # the awaits that should fire, not conn.transaction() itself.
        conn.transaction.assert_called_once()
        # 3 INSERT statements.
        assert conn.execute.await_count == 3
        for call in conn.execute.await_args_list:
            sql = call.args[0]
            assert "INSERT INTO" in sql
            assert "vector_index_kb_v1" in sql
            assert "ON CONFLICT" in sql

    async def test_postgres_error_mapped(self) -> None:
        idx, conn = _build_index()
        records = [VectorRecord(id="x", vector=[0.1], metadata={})]
        # Fail at the first execute() inside the transaction.
        conn.execute.side_effect = PostgresError("connection lost")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.upsert("kb_v1", records)
        assert exc_info.value.error_code == VectorIndexErrorCode.UPSERT_FAILED
        assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    async def test_search_without_filter(self) -> None:
        idx, conn = _build_index()
        row_a = {"id": "a", "score": 0.95, "metadata": json.dumps({"organ": "thyroid"})}
        row_b = {"id": "b", "score": 0.85, "metadata": json.dumps({"organ": "liver"})}
        conn.fetch.return_value = [row_a, row_b]
        results = await idx.search("kb_v1", query_vector=[0.1, 0.2, 0.3], top_k=5)
        assert len(results) == 2
        assert results[0].id == "a"
        assert results[0].score == 0.95
        assert results[0].metadata == {"organ": "thyroid"}
        sql = conn.fetch.call_args.args[0]
        assert "WHERE" not in sql
        assert "ORDER BY embedding" in sql
        assert "LIMIT" in sql

    async def test_search_with_scalar_filter(self) -> None:
        idx, conn = _build_index()
        conn.fetch.return_value = []
        await idx.search(
            "kb_v1",
            query_vector=[0.1, 0.2],
            top_k=10,
            filter={"organ": "thyroid"},
        )
        sql = conn.fetch.call_args.args[0]
        assert "WHERE metadata->>organ" in sql
        assert "$1::text" in sql
        # conn.fetch was called as fetch(sql, "thyroid", [0.1, 0.2], 10)
        # (the *query_params in the source unpacks the param list).
        args = conn.fetch.call_args.args
        assert args[1] == "thyroid"
        assert args[2] == [0.1, 0.2]
        assert args[3] == 10

    async def test_search_with_list_filter_serializes_as_json(self) -> None:
        idx, conn = _build_index()
        conn.fetch.return_value = []
        await idx.search(
            "kb_v1",
            query_vector=[0.1, 0.2],
            filter={"organ": ["thyroid", "liver"]},
        )
        sql = conn.fetch.call_args.args[0]
        assert "WHERE" in sql
        # The list is JSON-dumped into the first param (a string).
        first_param = conn.fetch.call_args.args[1]
        assert json.loads(first_param) == ["thyroid", "liver"]
        assert conn.fetch.call_args.args[2] == [0.1, 0.2]
        # top_k defaults to 10.
        assert conn.fetch.call_args.args[3] == 10

    async def test_search_with_dict_filter_serializes_as_json(self) -> None:
        idx, conn = _build_index()
        conn.fetch.return_value = []
        await idx.search(
            "kb_v1",
            query_vector=[0.1, 0.2],
            filter={"confidence": {"gte": 0.5, "lte": 0.9}},
        )
        sql = conn.fetch.call_args.args[0]
        assert "WHERE" in sql
        first_param = conn.fetch.call_args.args[1]
        assert json.loads(first_param) == {"gte": 0.5, "lte": 0.9}

    async def test_search_invalid_meta_key_raises(self) -> None:
        idx, _ = _build_index()
        with pytest.raises(VectorIndexError):
            await idx.search("kb_v1", query_vector=[0.1], filter={"1bad_key": "x"})

    async def test_search_undefined_table_raises_collection_not_found(self) -> None:
        idx, conn = _build_index()
        conn.fetch.side_effect = UndefinedTableError("table not found")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.search("kb_v1", query_vector=[0.1])
        assert exc_info.value.error_code == VectorIndexErrorCode.COLLECTION_NOT_FOUND
        assert exc_info.value.retryable is False

    async def test_search_postgres_error_raises_search_failed(self) -> None:
        idx, conn = _build_index()
        conn.fetch.side_effect = PostgresError("connection lost")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.search("kb_v1", query_vector=[0.1])
        assert exc_info.value.error_code == VectorIndexErrorCode.SEARCH_FAILED
        assert exc_info.value.retryable is True


# ---------------------------------------------------------------------------
# delete_collection
# ---------------------------------------------------------------------------


class TestDeleteCollection:
    async def test_drops_table(self) -> None:
        idx, conn = _build_index()
        await idx.delete_collection("kb_v1")
        sql = conn.execute.call_args.args[0]
        assert "DROP TABLE IF EXISTS vector_index_kb_v1" in sql

    async def test_invalid_collection_name_raises(self) -> None:
        idx, _ = _build_index()
        with pytest.raises(VectorIndexError):
            await idx.delete_collection("drop;table")

    async def test_postgres_error_mapped(self) -> None:
        idx, conn = _build_index()
        conn.execute.side_effect = PostgresError("pg down")
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.delete_collection("kb_v1")
        assert exc_info.value.error_code == VectorIndexErrorCode.DELETE_FAILED
        assert exc_info.value.retryable is False


# ---------------------------------------------------------------------------
# health_check & close
# ---------------------------------------------------------------------------


class TestHealthCheck:
    async def test_returns_true_on_select_1(self) -> None:
        idx, conn = _build_index()
        conn.fetchval.return_value = 1
        assert await idx.health_check() is True

    async def test_returns_false_on_error(self) -> None:
        idx, conn = _build_index()
        conn.fetchval.side_effect = RuntimeError("pool exhausted")
        assert await idx.health_check() is False


class TestClose:
    async def test_close_idempotent(self) -> None:
        idx, _ = _build_index()
        # Capture the pool reference before close() (which sets _pool=None).
        pool = idx._pool
        await idx.close()
        await idx.close()  # second call must not raise
        assert idx._closed is True
        # Pool is closed once (only the first close() does work).
        pool.close.assert_awaited_once()

    async def test_close_resets_pool(self) -> None:
        idx, _ = _build_index()
        await idx.close()
        assert idx._pool is None

    async def test_close_raises_after_close_when_used(self) -> None:
        """Once closed, operations must raise with a clear error."""
        idx, _ = _build_index()
        await idx.close()
        with pytest.raises(VectorIndexError) as exc_info:
            await idx.upsert("kb_v1", [VectorRecord(id="x", vector=[0.1], metadata={})])
        assert exc_info.value.error_code == VectorIndexErrorCode.CONNECTION_FAILED
