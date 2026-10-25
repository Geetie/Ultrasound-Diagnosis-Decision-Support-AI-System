"""A.88 T4-⑥ 守卫：``dropped_missing_row`` 不再静默（§4.3「零静默失败要加定语」）。

## 修的真问题（架构真相 §4.3）

原文点名了一条具体缺陷：*Qdrant 命中但 PG 无该 ``chunk_id`` 时直接 ``continue``、
无日志无计数*（``app/rag/retriever.py`` 的 ``_wrap_search_results``）。所以
「后端 428→0 裸 ``except: pass`` 归零」**不等于**"零静默失败"。

## 三条不变量

1. **每条丢弃都有 ``logger.warning``**（带 chunk_id / 来源 / 分数）；
2. **结果里暴露计数** ``RetrievalResult.dropped_missing_row``；
3. **计数是"本次检索"的，不是实例累加的**（长跑实例累加会让这个字段说谎）。

反证腿：
* 把 ``if row is None:`` 分支换回裸 ``continue``（去掉 warning 与计数）→
  ``TestNoLongerSilent`` 全组红；
* 把计数改成实例属性累加 → ``TestPerRequestTally`` 红。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.embedding.protocols import (  # noqa: E402
    EmbeddingModality,
    EmbeddingResponse,
    SearchResult,
)
from app.rag import retriever as retriever_module  # noqa: E402
from app.rag.protocols import RetrievalRequest  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402


class _Row:
    """模拟 ``KnowledgeChunkModel`` 行（只用到被读的字段）。"""

    def __init__(self, chunk_id: str):
        self.chunk_id = chunk_id
        self.kb_version = "kb_v1.0.0"
        self.organ = "thyroid"
        self.content = "content"
        self.meta = {}
        self.embedding = None
        self.source_doc_id = "d1"
        self.section_path = ""
        self.token_count = 4


class _FakeScalars:
    def __init__(self, rows: Sequence[_Row]):
        self._rows = list(rows)

    def all(self):
        return self._rows


class _FakeResult:
    def __init__(self, rows: Sequence[_Row]):
        self._rows = list(rows)

    def scalars(self):
        return _FakeScalars(self._rows)


class _FakeSession:
    """按 ``chunk_id`` 白名单返回行（模拟 PG 里只有一部分命中）。"""

    def __init__(self, existing: set[str]):
        self._existing = existing
        self.statement_count = 0

    async def execute(self, stmt):
        self.statement_count += 1
        # 简单粗暴：从 where 子句拿不到参数，改用收集到的 ids（由 factory 注入）
        return _FakeResult([_Row(cid) for cid in sorted(self._existing)])

    async def close(self):
        return None


def _mk_embedding() -> MagicMock:
    emb = MagicMock()
    emb.embed_text = AsyncMock(
        return_value=EmbeddingResponse(
            embeddings=[[0.1] * 8],
            model="bge-m3-medical",
            dimension=8,
            modality=EmbeddingModality.TEXT,
        )
    )
    return emb


def _mk_vector_client(results: list[SearchResult]) -> MagicMock:
    vc = MagicMock()
    vc.search = AsyncMock(return_value=results)
    return vc


def _session_factory(existing: set[str]):
    return lambda: _FakeSession(existing)


@pytest.fixture(autouse=True)
def _silence_side_effects(monkeypatch):
    """隔离引用记录与衰减调度（本组只关心丢弃计数）。"""

    async def _no_refs(chunk_ids):
        return 0

    async def _no_decay():
        return None

    monkeypatch.setattr(Retriever, "_record_references", staticmethod(_no_refs))
    monkeypatch.setattr(Retriever, "_maybe_run_decay", staticmethod(_no_decay))


class TestNoLongerSilent:
    async def test_missing_pg_row_is_counted_and_exposed(self):
        """**核心**：PG 缺行 → 结果里 ``dropped_missing_row`` 必须 > 0。

        反证：把 ``if row is None:`` 改回裸 ``continue`` → 计数恒 0 → 红。
        """
        vc = _mk_vector_client(
            [
                SearchResult(id="c1", score=0.9, metadata={}),
                SearchResult(id="gone-1", score=0.8, metadata={}),
                SearchResult(id="gone-2", score=0.7, metadata={}),
            ]
        )
        r = Retriever(
            vector_client=vc,
            embedding_service=_mk_embedding(),
            session_factory=_session_factory({"c1"}),
        )

        result = await r.retrieve(
            RetrievalRequest(query="q", top_k=5), enhanced_query="q"
        )

        assert result.dropped_missing_row == 2
        assert [c.chunk.chunk_id for c in result.chunks] == ["c1"]

    async def test_each_drop_logs_a_warning(self, monkeypatch):
        """每条丢弃都要有自己的 warning（不只一条汇总）。"""
        warnings: list[tuple[str, dict]] = []

        class _Logger:
            def warning(self, event, **fields):
                warnings.append((event, fields))

            def info(self, event, **fields):
                pass

            def debug(self, event, **fields):
                pass

        monkeypatch.setattr(retriever_module, "logger", _Logger())
        vc = _mk_vector_client(
            [
                SearchResult(id="gone-1", score=0.8, metadata={}),
                SearchResult(id="gone-2", score=0.7, metadata={}),
            ]
        )
        r = Retriever(
            vector_client=vc,
            embedding_service=_mk_embedding(),
            session_factory=_session_factory(set()),
        )

        await r.retrieve(RetrievalRequest(query="q", top_k=5), enhanced_query="q")

        detail = [f for e, f in warnings if e == "retrieval_hit_dropped_missing_in_pg"]
        assert len(detail) == 2, "逐条丢弃必须逐条留痕"
        assert {f["chunk_id"] for f in detail} == {"gone-1", "gone-2"}
        summary = [f for e, f in warnings if e == "retrieval_rows_dropped_missing_in_pg"]
        assert summary, "缺汇总日志"
        assert summary[0]["dropped"] == 2

    async def test_no_drop_means_zero_and_no_warning(self, monkeypatch):
        """没有丢弃时不该有噪音日志（否则 warning 会被淹没）。"""
        warnings: list[str] = []

        class _Logger:
            def warning(self, event, **fields):
                warnings.append(event)

            def info(self, event, **fields):
                pass

            def debug(self, event, **fields):
                pass

        monkeypatch.setattr(retriever_module, "logger", _Logger())
        vc = _mk_vector_client([SearchResult(id="c1", score=0.9, metadata={})])
        r = Retriever(
            vector_client=vc,
            embedding_service=_mk_embedding(),
            session_factory=_session_factory({"c1"}),
        )

        result = await r.retrieve(
            RetrievalRequest(query="q", top_k=5), enhanced_query="q"
        )

        assert result.dropped_missing_row == 0
        assert "retrieval_rows_dropped_missing_in_pg" not in warnings

    async def test_placeholder_path_has_no_drops(self):
        """没有 session_factory 的占位路径不查 PG → 不该报丢弃。"""
        vc = _mk_vector_client([SearchResult(id="c1", score=0.9, metadata={})])
        r = Retriever(
            vector_client=vc, embedding_service=_mk_embedding(), session_factory=None
        )
        result = await r.retrieve(
            RetrievalRequest(query="q", top_k=5), enhanced_query="q"
        )
        assert result.dropped_missing_row == 0
        assert result.has_results is True


class TestPerRequestTally:
    async def test_tally_does_not_accumulate_across_requests(self):
        """**反证腿核心**：计数必须是"本次"的，不能跨请求累加。

        反证：把 ``drop_tally`` 改成 ``self._drop_tally``（实例属性）→ 第二次
        请求会读到上一次的 2 → 本用例红（结果里的数字在说谎）。
        """
        vc = _mk_vector_client([SearchResult(id="gone", score=0.9, metadata={})])
        r = Retriever(
            vector_client=vc,
            embedding_service=_mk_embedding(),
            session_factory=_session_factory(set()),
        )

        first = await r.retrieve(RetrievalRequest(query="q", top_k=5), enhanced_query="q")
        second = await r.retrieve(RetrievalRequest(query="q", top_k=5), enhanced_query="q")

        assert first.dropped_missing_row == 1
        assert second.dropped_missing_row == 1, "计数跨请求累加了"


def test_protocol_field_exists_with_safe_default():
    """字段默认值必须是 0（老调用方构造 ``RetrievalResult()`` 不受影响）。"""
    from app.rag.protocols import RetrievalResult

    assert RetrievalResult().dropped_missing_row == 0
    assert RetrievalResult().image_leg_skip_reason == ""


def test_cache_roundtrip_keeps_drop_count(tmp_path):
    """缓存命中也要保留这个字段（否则第二次请求会读成 0）。"""
    from app.knowledge.protocols import KnowledgeChunk
    from app.rag.cache import RetrievalCache
    from app.rag.protocols import RetrievalResult, ScoredChunk

    cache = RetrievalCache(redis_factory=None)
    chunk = KnowledgeChunk(
        chunk_id="c1",
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="x",
        metadata={},
        embedding=None,
        source_doc_id="d",
        section_path="",
        token_count=4,
    )
    result = RetrievalResult(
        chunks=[
            ScoredChunk(chunk=chunk, score=0.5, retrieval_source=retriever_module.RetrievalMethod.DENSE)
        ],
        dropped_missing_row=3,
        image_leg_skip_reason="capability_collection_missing",
    )

    async def _roundtrip():
        key = "rag:cache:test"
        await cache.set(key, result)
        return await cache.get(key)

    import asyncio

    decoded = asyncio.run(_roundtrip())
    assert decoded is not None
    assert decoded.dropped_missing_row == 3
    assert decoded.image_leg_skip_reason == "capability_collection_missing"


def _unused(*args: Any, **kwargs: Any) -> None:  # pragma: no cover - 保留导入
    return None
