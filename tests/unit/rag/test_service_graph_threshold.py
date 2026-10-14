"""A.63 批 WP1 守卫：图谱邻居不被 min_score_threshold 整批丢掉。

背景（真机发现，不是猜的）
--------------------------
图谱扩展（`app/rag/retriever.py:247-306` `_expand_graph_neighbors`）以
``score=0.0`` + ``retrieval_source=GRAPH`` 把邻居 **append 在 top-k 之后**；
而 `app/rag/service.py` 的 ``min_score_threshold`` 过滤按分数筛 →
**开启图谱后邻居会在最后一层被整批剔除**（表现为"开关开了等于没开"，
且日志里看不出任何痕迹）。

三条不变量（本文件逐条钉住）
----------------------------
1. **正腿**：图谱来源即使 ``score=0.0`` 也必须活到最终结果；
2. **反腿**：普通来源的低分块**仍然**被剔除 —— 阈值语义没有被放宽；
3. **留痕**：豁免条数与剔除条数写进固定事件名 ``min_score_filter_applied``
   （真机探针与守卫都读这个事件名）。

另有一条与"开了但空转"相关的不变量放在
`tests/unit/rag/test_retriever_graph_expansion.py`
（门控开着却一条邻居都没有 → ``graph_neighbors_empty`` 留痕）。
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


def _mk_chunk(chunk_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="x",
        metadata={},
        embedding=None,
        source_doc_id="d1",
        section_path="",
        token_count=4,
    )


def _scored(
    chunk_id: str,
    score: float,
    source: RetrievalMethod,
) -> ScoredChunk:
    """构造检索/重排输出（score 与 rerank_score 同值，便于阈值判定）。"""
    return ScoredChunk(
        chunk=_mk_chunk(chunk_id),
        score=score,
        rerank_score=score,
        retrieval_source=source,
    )


def _service(chunks: list[ScoredChunk]) -> RAGServiceImpl:
    retriever = MagicMock(spec=Retriever)
    retriever.retrieve = AsyncMock(
        return_value=RetrievalResult(
            chunks=list(chunks),
            retrieval_method=RetrievalMethod.HYBRID,
        )
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


class TestGraphExemption:
    """阈值过滤只豁免图谱来源；其余语义一律不变。"""

    async def test_graph_neighbor_survives_threshold(self):
        """正腿：score=0.0 的图谱邻居必须活下来（否则开启图谱是空转）。"""
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("kb_v1.0.0:d1#0009", 0.0, RetrievalMethod.GRAPH),
        ]
        svc = _service(chunks)
        result = await svc.retrieve(
            RetrievalRequest(query="x", min_score_threshold=0.5)
        )
        ids = [c.chunk.chunk_id for c in result.chunks]
        assert "kb_v1.0.0:d1#0009" in ids, (
            "图谱邻居被 min_score_threshold 丢掉了 —— 这正是 A.63 WP1 修的接线缺陷"
        )
        assert "kb_v1.0.0:d1#0001" in ids

    async def test_normal_low_score_is_still_dropped(self):
        """反腿：阈值语义没被放宽 —— 普通来源的低分块照样被剔除。"""
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("kb_v1.0.0:d1#0002", 0.4, RetrievalMethod.DENSE),
            _scored("kb_v1.0.0:d1#0003", 0.2, RetrievalMethod.SPARSE),
        ]
        svc = _service(chunks)
        result = await svc.retrieve(
            RetrievalRequest(query="x", min_score_threshold=0.5)
        )
        ids = [c.chunk.chunk_id for c in result.chunks]
        assert ids == ["kb_v1.0.0:d1#0001"]

    async def test_threshold_zero_keeps_everything(self):
        """反腿：阈值 0 时不做任何过滤（行为与修改前一致）。"""
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("kb_v1.0.0:d1#0009", 0.0, RetrievalMethod.GRAPH),
        ]
        svc = _service(chunks)
        result = await svc.retrieve(
            RetrievalRequest(query="x", min_score_threshold=0.0)
        )
        assert len(result.chunks) == 2


class TestFilterIsTraced:
    """留痕：豁免/剔除条数必须可从固定事件名读到（探针与守卫共用）。"""

    async def test_filter_logs_counts(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake_logger)
        chunks = [
            _scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID),
            _scored("kb_v1.0.0:d1#0002", 0.4, RetrievalMethod.DENSE),
            _scored("kb_v1.0.0:d1#0009", 0.0, RetrievalMethod.GRAPH),
        ]
        svc = _service(chunks)
        await svc.retrieve(RetrievalRequest(query="x", min_score_threshold=0.5))

        events = [c.args[0] for c in fake_logger.info.call_args_list if c.args]
        assert "min_score_filter_applied" in events, (
            "过滤必须留痕，否则'图谱被豁免/被剔除几条'在真机上不可见"
        )
        payload = next(
            c.kwargs
            for c in fake_logger.info.call_args_list
            if c.args and c.args[0] == "min_score_filter_applied"
        )
        assert payload["graph_exempt"] == 1
        assert payload["dropped"] == 1
        assert payload["kept"] == 2
        assert payload["threshold"] == 0.5

    async def test_no_filter_no_log(self, monkeypatch):
        """反腿：阈值 0 时不该产生过滤留痕（避免噪声/误导探针）。"""
        fake_logger = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake_logger)
        svc = _service([_scored("kb_v1.0.0:d1#0001", 0.9, RetrievalMethod.HYBRID)])
        await svc.retrieve(RetrievalRequest(query="x", min_score_threshold=0.0))
        events = [c.args[0] for c in fake_logger.info.call_args_list if c.args]
        assert "min_score_filter_applied" not in events
