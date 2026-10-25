"""A.88 T4-④ 守卫：权威性评分 + 分级下限**真的有实现且被生产路径调用**。

## 修的真问题（架构真相 §3.3）

「知识来源权威性评分 / 分级下限」两项**无实现**：检索把指南条文、教科书段落、
病例报告当同一类证据排序，医生看到的引用顺序里没有任何"这条是 ACR 指南、
那条是单个病例"的区别。

## 判据（"最小可用"但不许是壳）

1. **权威性有可解释权重并参与排序**：``RAGServiceImpl.retrieve`` 真的调了
   ``rank_indices`` 并可能改变 chunks 顺序；权重表可按来源类型逐条核对；
2. **未知来源不扣分**：missing metadata 是数据问题，不是"这条知识不可信"
   （同 ``decay`` 的"缺失数据不当证据"原则）；
3. **默认不改变临床输出**：``tie_break`` 只在分数完全相同时重排；
   ``weighted`` 必须显式开启；
4. **留痕**：``authority_ranking_applied`` 带 mode / 来源分布 / 变动条数。

反证腿：把 ``rank_indices`` 里的权重表全改成 1.0 → ``TestWeights`` 红；
把 service 里的排序调用删掉 → ``TestWiredIntoService`` 红。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.knowledge.authority import (  # noqa: E402
    DEFAULT_SOURCE_KIND,
    SOURCE_AUTHORITY,
    AuthorityRankingMode,
    SourceKind,
    authority_breakdown,
    authority_weight,
    rank_indices,
    source_kind_of,
)
from app.knowledge.protocols import KnowledgeChunk  # noqa: E402
from app.rag import service as service_module  # noqa: E402
from app.rag.protocols import (  # noqa: E402
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)


def _chunk(
    chunk_id: str,
    *,
    source_doc_id: str = "unknown_doc",
    metadata: dict | None = None,
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="x",
        metadata=metadata or {},
        embedding=None,
        source_doc_id=source_doc_id,
        section_path="",
        token_count=4,
    )


def _scored(chunk: KnowledgeChunk, score: float) -> ScoredChunk:
    return ScoredChunk(
        chunk=chunk, score=score, rerank_score=None, retrieval_source=RetrievalMethod.DENSE
    )


# ----------------------------------------------------------------------
# ① 权重表：可解释、可核对
# ----------------------------------------------------------------------


class TestWeights:
    def test_guideline_outranks_textbook_outranks_case_report(self):
        """指南 > 教科书 > 病例报告（模块文档那张表必须与常量一致）。"""
        assert SOURCE_AUTHORITY[SourceKind.GUIDELINE.value] == 1.00
        assert (
            SOURCE_AUTHORITY[SourceKind.GUIDELINE.value]
            > SOURCE_AUTHORITY[SourceKind.TEXTBOOK.value]
            > SOURCE_AUTHORITY[SourceKind.CASE_REPORTS.value]
        )

    def test_unknown_source_is_not_penalized(self):
        """**核心反证**：未知来源给 1.0（不拿缺失数据当证据）。

        反证：把 ``unknown`` 改成 0.5（"未知就当不可信"）→ 本用例红。
        """
        assert SOURCE_AUTHORITY[DEFAULT_SOURCE_KIND] == 1.0
        assert authority_weight(_chunk("c1")) == 1.0

    def test_metadata_source_kind_wins(self):
        c = _chunk("c1", metadata={"source_kind": "guideline"})
        assert source_kind_of(c) == SourceKind.GUIDELINE.value

    def test_doc_id_prefix_is_used_when_metadata_missing(self):
        assert source_kind_of(_chunk("c1", source_doc_id="guideline/thyroid")) == (
            SourceKind.GUIDELINE.value
        )
        # 前缀必须锚定在开头，不是"包含 guideline 就算"
        assert source_kind_of(
            _chunk("c2", source_doc_id="case_reports/guideline_misuse_2024")
        ) == SourceKind.CASE_REPORTS.value

    def test_dirty_metadata_does_not_raise(self):
        """脏 metadata 不许让检索崩掉（回退 unknown 并留痕）。"""
        c = _chunk("c1")
        c.metadata = {"source_kind": object()}  # 无法 str() 成已知类型也不会抛
        assert source_kind_of(c) in SOURCE_AUTHORITY

    def test_breakdown_counts_by_kind(self):
        chunks = [
            _chunk("a", metadata={"source_kind": "guideline"}),
            _chunk("b", metadata={"source_kind": "guideline"}),
            _chunk("c", metadata={"source_kind": "case_reports"}),
        ]
        assert authority_breakdown(chunks) == {"guideline": 2, "case_reports": 1}


# ----------------------------------------------------------------------
# ② 排序语义：三种模式各守一条
# ----------------------------------------------------------------------


class TestRankingModes:
    def test_off_keeps_score_order(self):
        chunks = [
            _chunk("case", metadata={"source_kind": "case_reports"}),
            _chunk("guide", metadata={"source_kind": "guideline"}),
        ]
        out = rank_indices(chunks, [0.5, 0.5], mode="off")
        assert out.order == [0, 1]
        assert out.mode == "off"

    def test_tie_break_only_touches_equal_scores(self):
        """默认档：分数不同的位置**不动**，只重排相等段。"""
        chunks = [
            _chunk("case", metadata={"source_kind": "case_reports"}),
            _chunk("guide", metadata={"source_kind": "guideline"}),
            _chunk("mid", metadata={"source_kind": "textbook"}),
        ]
        out = rank_indices(chunks, [0.9, 0.9, 0.1], mode="tie_break")
        # 前两条同分：指南排前
        assert out.order[:2] == [1, 0]
        # 第三条分低：仍在最后
        assert out.order[2] == 2

    def test_weighted_reorders_across_different_scores(self):
        """weighted 会跨分数重排（所以必须显式开启）。"""
        chunks = [
            _chunk("case", metadata={"source_kind": "case_reports"}),
            _chunk("guide", metadata={"source_kind": "guideline"}),
        ]
        # 0.51*0.70=0.357 < 0.5*1.00=0.5 → 指南反超
        out = rank_indices(chunks, [0.51, 0.50], mode="weighted")
        assert out.order == [1, 0]
        assert out.changed > 0

    def test_weighted_with_equal_weights_is_noop(self):
        chunks = [
            _chunk("a", metadata={"source_kind": "guideline"}),
            _chunk("b", metadata={"source_kind": "guideline"}),
        ]
        out = rank_indices(chunks, [0.9, 0.5], mode="weighted")
        assert out.order == [0, 1]
        assert out.changed == 0

    def test_unknown_mode_falls_back_to_tie_break(self):
        chunks = [_chunk("a"), _chunk("b")]
        out = rank_indices(chunks, [0.5, 0.4], mode="nonsense")
        assert out.mode == AuthorityRankingMode.TIE_BREAK.value


# ----------------------------------------------------------------------
# ③ 接线：真的被 RAG service 生产路径调用
# ----------------------------------------------------------------------


def _mk_rag_service(retrieval_result: RetrievalResult) -> service_module.RAGServiceImpl:
    enhancer = MagicMock()

    async def _enhance(query, organ_hint=None, use_llm=False):
        out = MagicMock()
        out.enhanced = query
        out.llm_enhanced = False
        return out

    enhancer.enhance = _enhance
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=retrieval_result)
    retriever.close = AsyncMock()
    retriever.health_check = AsyncMock(return_value={"embedding": True, "vector_index": True})
    reranker = MagicMock()
    reranker.close = AsyncMock()
    reranker.health_check = AsyncMock(return_value=True)
    heuristic = MagicMock()
    heuristic.close = AsyncMock()
    bm25 = MagicMock()
    bm25.should_trigger = MagicMock(return_value=False)
    bm25._index = MagicMock()
    bm25._index.close = AsyncMock()
    bm25._index.health_check = AsyncMock(return_value=True)
    return service_module.RAGServiceImpl(
        query_enhancer=enhancer,
        retriever=retriever,
        reranker=reranker,
        heuristic_reranker=heuristic,
        bm25_fallback=bm25,
        rerank_output_top_k=10,
    )


class TestWiredIntoService:
    async def test_service_calls_authority_ranking(self, monkeypatch):
        """**核心**：RAG 服务必须真的调用权威性排序（否则就是壳）。

        反证：删掉 service 里 ``authority_rank_indices(...)`` 那段 → 红。
        """
        calls: list[str] = []

        def _spy(chunks, scores, *, mode="tie_break"):
            calls.append(str(mode))
            return rank_indices(chunks, scores, mode=mode)

        monkeypatch.setattr(service_module, "authority_rank_indices", _spy)

        chunks = [
            _scored(_chunk("case", metadata={"source_kind": "case_reports"}), 0.5),
            _scored(_chunk("guide", metadata={"source_kind": "guideline"}), 0.5),
        ]
        svc = _mk_rag_service(RetrievalResult(chunks=chunks))
        request = RetrievalRequest(query="q", top_k=5, use_reranking=False,
                                   min_score_threshold=0.0)

        result = await svc.retrieve(request)

        assert calls == ["tie_break"], "权威性排序没有被调用"
        assert [c.chunk.chunk_id for c in result.chunks] == ["guide", "case"]

    async def test_off_mode_makes_no_change(self, monkeypatch):
        chunks = [
            _scored(_chunk("case", metadata={"source_kind": "case_reports"}), 0.5),
            _scored(_chunk("guide", metadata={"source_kind": "guideline"}), 0.5),
        ]
        svc = _mk_rag_service(RetrievalResult(chunks=chunks))
        request = RetrievalRequest(
            query="q", top_k=5, use_reranking=False, min_score_threshold=0.0,
            extra={"authority_ranking": "off"},
        )
        result = await svc.retrieve(request)
        assert [c.chunk.chunk_id for c in result.chunks] == ["case", "guide"]

    async def test_weighted_mode_is_opt_in(self, monkeypatch):
        chunks = [
            _scored(_chunk("case", metadata={"source_kind": "case_reports"}), 0.51),
            _scored(_chunk("guide", metadata={"source_kind": "guideline"}), 0.50),
        ]
        svc = _mk_rag_service(RetrievalResult(chunks=chunks))
        request = RetrievalRequest(
            query="q", top_k=5, use_reranking=False, min_score_threshold=0.0,
            extra={"authority_ranking": "weighted"},
        )
        result = await svc.retrieve(request)
        assert [c.chunk.chunk_id for c in result.chunks] == ["guide", "case"]

    async def test_ranking_failure_keeps_original_order(self, monkeypatch):
        """排序增强翻车 → 保持原顺序且**留痕**（不阻断检索）。

        反证：把 service 里的 try/except 去掉并让它抛 → 检索直接失败 → 红。
        """

        def _boom(*args, **kwargs):
            raise RuntimeError("ranking exploded")

        monkeypatch.setattr(service_module, "authority_rank_indices", _boom)
        chunks = [
            _scored(_chunk("a"), 0.9),
            _scored(_chunk("b"), 0.8),
        ]
        svc = _mk_rag_service(RetrievalResult(chunks=chunks))
        result = await svc.retrieve(
            RetrievalRequest(query="q", top_k=5, use_reranking=False,
                             min_score_threshold=0.0)
        )
        assert [c.chunk.chunk_id for c in result.chunks] == ["a", "b"]

    async def test_ranking_leaves_trace(self, monkeypatch):
        events: list[tuple[str, dict]] = []

        class _Logger:
            def info(self, event, **fields):
                events.append((event, fields))

            def warning(self, event, **fields):
                events.append((event, fields))

            def error(self, event, **fields):
                events.append((event, fields))

            def debug(self, event, **fields):
                pass

        monkeypatch.setattr(service_module, "logger", _Logger())
        chunks = [
            _scored(_chunk("case", metadata={"source_kind": "case_reports"}), 0.5),
            _scored(_chunk("guide", metadata={"source_kind": "guideline"}), 0.5),
        ]
        svc = _mk_rag_service(RetrievalResult(chunks=chunks))
        await svc.retrieve(
            RetrievalRequest(query="q", top_k=5, use_reranking=False,
                             min_score_threshold=0.0)
        )
        payloads = [f for e, f in events if e == "authority_ranking_applied"]
        assert payloads, "权威性排序没有留痕"
        assert payloads[0]["mode"] == "tie_break"
        assert payloads[0]["changed"] >= 1
        assert payloads[0]["source_kinds"] == {"case_reports": 1, "guideline": 1}
