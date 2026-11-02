"""A.109 / N5 守卫：影像腿不能被 dense 阈值整批丢弃。

背景（实测，非推断）
--------------------
影像块在 `retriever._image_hits` 里 **embedding=None**（只有 6 个元数据键的文本），
分数来自启发式 `0.5 * jaccard`（`reranker.py:250-257`），量纲与 dense/相对阈值
完全不同，且恒 < 0.5 —— 一旦 `min_score_threshold=0.5`（生产默认），打开影像腿
后**所有**影像块都在最后一层被剔除。这与"影像块文本没特征"（G2）是两个独立原因，
只修 G2 不会有任何效果。

三条不变量：
1. **正腿**：`retrieval_source=IMAGE` 的块即使低分也必须活到最终结果；
2. **反腿**：普通来源低分块**仍然**被剔除（阈值语义未被放宽）；
3. **留痕**：豁免条数写进 `min_score_filter_applied.image_exempt`（真机探针读这个字段）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.knowledge.protocols import KnowledgeChunk
from app.rag import service as service_module
from app.rag.bm25_fallback import BM25Fallback
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)
from app.rag.query_enhancer import QueryEnhancer
from app.rag.reranker import HeuristicReranker, RerankService
from app.rag.retriever import Retriever
from app.rag.service import RAGServiceImpl

pytestmark = [pytest.mark.track_rag]


def _chunk(chunk_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="[影像参考] img_a；模态=US",
        metadata={},
        embedding=None,
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )


def _scored(chunk_id: str, score: float, source: RetrievalMethod) -> ScoredChunk:
    return ScoredChunk(
        chunk=_chunk(chunk_id), score=score, rerank_score=score, retrieval_source=source
    )


def _service(chunks: list[ScoredChunk]) -> RAGServiceImpl:
    retriever = MagicMock(spec=Retriever)
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResult(chunks=list(chunks), retrieval_method=RetrievalMethod.HYBRID)
    )
    reranker = MagicMock(spec=RerankService)
    reranker.rerank = AsyncMock(return_value=list(chunks))
    heuristic = MagicMock(spec=HeuristicReranker)
    bm25 = MagicMock(spec=BM25Fallback)
    bm25.should_trigger = MagicMock(return_value=False)
    enhancer = MagicMock(spec=QueryEnhancer)
    enhancer.enhance = AsyncMock(return_value=None)
    return RAGServiceImpl(
        query_enhancer=enhancer,
        retriever=retriever,
        reranker=reranker,
        heuristic_reranker=heuristic,
        bm25_fallback=bm25,
    )


class TestImageExemption:
    async def test_image_hit_survives_dense_threshold(self):
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("img_a", 0.30, RetrievalMethod.IMAGE),  # 启发式分，恒 <0.5
        ]
        result = await _service(chunks).retrieve(
            RetrievalRequest(query="x", min_score_threshold=0.5)
        )
        ids = [c.chunk.chunk_id for c in result.chunks]
        assert "img_a" in ids, "影像块被 dense 阈值剔除 —— 打开影像腿等于白开（N5）"

    async def test_normal_low_score_still_dropped(self):
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("kb_v1.0.0:d1#0002", 0.2, RetrievalMethod.DENSE),
        ]
        result = await _service(chunks).retrieve(
            RetrievalRequest(query="x", min_score_threshold=0.5)
        )
        assert [c.chunk.chunk_id for c in result.chunks] == ["kb_v1.0.0:d1#0001"]

    async def test_exemption_is_traced(self, monkeypatch):
        fake = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake)
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("img_a", 0.30, RetrievalMethod.IMAGE),
        ]
        await _service(chunks).retrieve(RetrievalRequest(query="x", min_score_threshold=0.5))
        events = [c.args[0] for c in fake.info.call_args_list if c.args]
        assert "min_score_filter_applied" in events
        payload = next(
            c.kwargs for c in fake.info.call_args_list
            if c.args and c.args[0] == "min_score_filter_applied"
        )
        assert payload["image_exempt"] == 1
