"""Tests for app.knowledge.graph_query — GraphQueryAdapter Protocol + PgVectorGraphAdapter."""
from __future__ import annotations

import pytest

from app.knowledge.graph_query import GraphQueryAdapter, PgVectorGraphAdapter
from app.knowledge.protocols import KnowledgeChunk


# ------------------------------------------------------------------
# PgVectorGraphAdapter skeleton tests
# ------------------------------------------------------------------

class TestPgVectorGraphAdapter:
    """Test the one-period skeleton implementation."""

    @pytest.fixture
    def adapter(self) -> PgVectorGraphAdapter:
        return PgVectorGraphAdapter()

    @pytest.mark.asyncio
    async def test_query_neighbors_returns_empty(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.query_neighbors("chunk_001")
        assert result == []

    @pytest.mark.asyncio
    async def test_query_neighbors_with_relation_type(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.query_neighbors("chunk_001", relation_type="implies")
        assert result == []

    @pytest.mark.asyncio
    async def test_query_neighbors_with_max_depth(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.query_neighbors("chunk_001", max_depth=3)
        assert result == []

    @pytest.mark.asyncio
    async def test_traverse_path_returns_empty(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.traverse_path("chunk_001", "chunk_002")
        assert result == []

    @pytest.mark.asyncio
    async def test_traverse_path_with_max_hops(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.traverse_path("chunk_001", "chunk_002", max_hops=10)
        assert result == []

    @pytest.mark.asyncio
    async def test_check_contradictions_returns_empty(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.check_contradictions(["chunk_001", "chunk_002"])
        assert result == []

    @pytest.mark.asyncio
    async def test_check_contradictions_single_chunk(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.check_contradictions(["chunk_001"])
        assert result == []

    @pytest.mark.asyncio
    async def test_check_contradictions_empty_list(self, adapter: PgVectorGraphAdapter) -> None:
        result = await adapter.check_contradictions([])
        assert result == []


# ------------------------------------------------------------------
# Protocol structural compliance test
# ------------------------------------------------------------------

class TestGraphQueryAdapterProtocol:
    """Verify PgVectorGraphAdapter satisfies the GraphQueryAdapter Protocol."""

    def test_adapter_has_query_neighbors(self) -> None:
        adapter = PgVectorGraphAdapter()
        assert hasattr(adapter, "query_neighbors")
        assert callable(adapter.query_neighbors)

    def test_adapter_has_traverse_path(self) -> None:
        adapter = PgVectorGraphAdapter()
        assert hasattr(adapter, "traverse_path")
        assert callable(adapter.traverse_path)

    def test_adapter_has_check_contradictions(self) -> None:
        adapter = PgVectorGraphAdapter()
        assert hasattr(adapter, "check_contradictions")
        assert callable(adapter.check_contradictions)

    def test_session_factory_stored(self) -> None:
        sentinel = object()
        adapter = PgVectorGraphAdapter(session_factory=sentinel)
        assert adapter._session_factory is sentinel
