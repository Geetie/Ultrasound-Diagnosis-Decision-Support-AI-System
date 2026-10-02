"""Unit tests for Retriever graph-neighbor expansion (P2-KB-08 消费方接线).

Covers:
- settings.graph_query_enabled=False（默认）→ 不调用 adapter，结果原样
- enabled → depth-1 邻居追加在 top-k 之后，retrieval_source=GRAPH
- 去重：邻居已在结果中不重复追加；neighbor_limit 截断生效
- adapter 抛异常 → 原样返回（主链路不受影响）
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.config.settings import settings
from app.knowledge.protocols import KnowledgeChunk
from app.rag.protocols import RetrievalMethod, RetrievalRequest, ScoredChunk
from app.rag.retriever import Retriever


def _make_chunk(chunk_id: str, content: str = "x") -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content=content,
        metadata={},
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )


def _scored(chunk_id: str, score: float = 1.0) -> ScoredChunk:
    return ScoredChunk(
        chunk=_make_chunk(chunk_id),
        score=score,
        rerank_score=None,
        retrieval_source=RetrievalMethod.HYBRID,
    )


def _make_adapter(neighbors: dict[str, list[KnowledgeChunk]]) -> MagicMock:
    adapter = MagicMock(name="GraphAdapter")

    async def _query(chunk_id, relation_type=None, max_depth=1):
        if chunk_id in neighbors:
            raise RuntimeError("boom")
        return neighbors.get(chunk_id, [])

    adapter.query_neighbors = AsyncMock(side_effect=_query)
    return adapter


def _make_retriever(adapter) -> Retriever:
    return Retriever(
        vector_client=MagicMock(),
        embedding_service=MagicMock(),
        graph_adapter=adapter,
    )


@pytest.mark.asyncio
async def test_disabled_by_default_no_expansion(monkeypatch):
    monkeypatch.setattr(settings, "graph_query_enabled", False)
    adapter = _make_adapter({})
    adapter.query_neighbors.reset_mock(side_effect=None)
    adapter.query_neighbors.return_value = [_make_chunk("kb_v1.0.0:d1#0009")]
    retriever = _make_retriever(adapter)
    chunks = [_scored("kb_v1.0.0:d1#0001")]
    result = await retriever._expand_graph_neighbors(chunks)
    assert result == chunks
    adapter.query_neighbors.assert_not_awaited()


@pytest.mark.asyncio
async def test_enabled_appends_neighbors_with_graph_source(monkeypatch):
    monkeypatch.setattr(settings, "graph_query_enabled", True)
    adapter = MagicMock(name="GraphAdapter")
    adapter.query_neighbors = AsyncMock(
        return_value=[_make_chunk("kb_v1.0.0:d1#0002", "neighbor")]
    )
    retriever = _make_retriever(adapter)
    chunks = [_scored("kb_v1.0.0:d1#0001"), _scored("kb_v1.0.0:d1#0003")]
    result = await retriever._expand_graph_neighbors(chunks)
    assert len(result) == 3
    assert result[-1].retrieval_source == RetrievalMethod.GRAPH
    assert result[-1].chunk.chunk_id == "kb_v1.0.0:d1#0002"
    # 种子按融合序取前 seed_limit（默认 3）个：这里 2 个种子都被查询，
    # 第二个种子返回的同一邻居被去重，不挤占原 top-k
    assert adapter.query_neighbors.await_count == 2
    first_call = adapter.query_neighbors.await_args_list[0]
    assert first_call.args[0] == "kb_v1.0.0:d1#0001"
    assert first_call.kwargs.get("max_depth") == 1


@pytest.mark.asyncio
async def test_dedup_and_neighbor_limit(monkeypatch):
    monkeypatch.setattr(settings, "graph_query_enabled", True)
    adapter = MagicMock(name="GraphAdapter")

    responses = {
        "kb_v1.0.0:d1#0001": [
            _make_chunk("kb_v1.0.0:d1#0003"),  # 已在 top-k → 去重
            _make_chunk("kb_v1.0.0:d1#0002"),
            _make_chunk("kb_v1.0.0:d1#0004"),
        ],
    }

    async def _query(chunk_id, relation_type=None, max_depth=1):
        return responses.get(chunk_id, [])

    adapter.query_neighbors = AsyncMock(side_effect=_query)
    retriever = Retriever(
        vector_client=MagicMock(),
        embedding_service=MagicMock(),
        graph_adapter=adapter,
        graph_neighbor_limit=1,
    )
    chunks = [_scored("kb_v1.0.0:d1#0001"), _scored("kb_v1.0.0:d1#0003")]
    result = await retriever._expand_graph_neighbors(chunks)
    assert len(result) == 3
    assert result[-1].chunk.chunk_id == "kb_v1.0.0:d1#0002"


@pytest.mark.asyncio
async def test_adapter_exception_degrades_to_original(monkeypatch):
    monkeypatch.setattr(settings, "graph_query_enabled", True)
    adapter = MagicMock(name="GraphAdapter")
    adapter.query_neighbors = AsyncMock(side_effect=RuntimeError("pg down"))
    retriever = _make_retriever(adapter)
    chunks = [_scored("kb_v1.0.0:d1#0001")]
    result = await retriever._expand_graph_neighbors(chunks)
    assert result == chunks
