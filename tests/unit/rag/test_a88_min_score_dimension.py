"""A.88 A1 守卫：``min_score_threshold`` 必须按**分数实际所处的量纲**解释。

## 缺陷（量纲错配，不是"阈值调大了"）

``min_score_threshold`` 默认 0.5（``app/rag/protocols.py:119``）是按
**rerank**（cross-encoder，0~1 相似度）口径定的。而 standard 档
（``use_reranking=False``，见 ``app/agents/routing.py`` 的
``RetrievalProfile.standard``）与 BM25 兜底路径的 ``score`` 是 **RRF** 融合分：

    fused(d) = Σ 1/(60 + rank)   →   首条 1/61 ≈ 0.0164，量级 0.016~0.033

拿 0.5 去比**恒不成立** → Stage 5 把整批剔空 → ``RetrievalResult.chunks == []``
→ 一致性校验「与指南比」恒 ``no_guideline_chunks``
（``app/fusion/consistency.py:837-863``）。四层校验里的这一层因此**从来没跑过**。

## 修法（本文件逐条钉住）

* 有 ``rerank_score`` → 绝对判据（**语义不变**，这是回归腿）；
* 无 ``rerank_score`` → 相对判据 ``score >= threshold * max_score_of_batch``
  （max 取本批非图谱项最大值；max <= 0 时整批保留）；
* 图谱来源豁免不动（另有 ``test_service_graph_threshold.py`` 钉着）；
* 留痕新增 ``mode``（absolute/relative/mixed）与真实 ``dropped``/``kept``；
* 缓存键必须带语义版本（阈值数值相同、解释不同 → 必须换键）。

每条修复都配**反证腿**：把实现改回"一律绝对阈值"即红。
"""
from __future__ import annotations

import hashlib
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.knowledge.protocols import KnowledgeChunk
from app.rag import service as service_module
from app.rag.bm25_fallback import BM25Fallback
from app.rag.cache import MIN_SCORE_FILTER_SEMANTICS, RetrievalCache
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


def _rrf_hit(chunk_id: str, score: float) -> ScoredChunk:
    """RRF 命中：**没有** rerank_score（standard 档 / BM25 兜底的真实形态）。"""
    return ScoredChunk(
        chunk=_mk_chunk(chunk_id),
        score=score,
        rerank_score=None,
        retrieval_source=RetrievalMethod.HYBRID,
    )


def _reranked_hit(chunk_id: str, score: float) -> ScoredChunk:
    """rerank 命中：score 与 rerank_score 都是 0~1 相似度。"""
    return ScoredChunk(
        chunk=_mk_chunk(chunk_id),
        score=score,
        rerank_score=score,
        retrieval_source=RetrievalMethod.HYBRID,
    )


def _service(chunks: list[ScoredChunk], *, use_reranking: bool) -> RAGServiceImpl:
    """组装 service：``use_reranking=False`` 时让检索器直接吐 RRF 命中。

    注意 reranker 一律配置为返回"逐条转录"的替身 —— 若被测路径不该走
    rerank（``use_reranking=False``），service 根本不会调用它。
    """
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


#: 真实 RRF 量级（k=60）：首条 1/61，第 5 条 1/65。**全部远低于 0.5**。
RRF_SCORES = [1 / 61, 1 / 62, 1 / 63, 1 / 64, 1 / 65]


class TestRrfNotEmptied:
    """正腿：RRF 量纲下不再整批剔空（这是 A.88 的直接病象）。"""

    async def test_standard_tier_keeps_head_of_rrf_batch(self):
        """standard 档（use_reranking=False）在默认阈值 0.5 下必须留下头部。"""
        chunks = [_rrf_hit(f"c{i}", s) for i, s in enumerate(RRF_SCORES)]
        svc = _service(chunks, use_reranking=False)

        result = await svc.retrieve(
            RetrievalRequest(
                query="甲状腺结节指南", use_reranking=False, min_score_threshold=0.5
            )
        )

        assert result.chunks, (
            "RRF 分（0.016~0.033）被 0.5 的绝对阈值整批剔空 —— 这正是 A.88 A1 的缺陷；"
            "后果是一致性校验「与指南比」恒 no_guideline_chunks"
        )
        # 相对判据 = 0.5 * max(本批)，首条必过
        assert result.chunks[0].chunk.chunk_id == "c0"
        assert result.chunks[0].score == pytest.approx(RRF_SCORES[0])

    async def test_relative_judgement_still_trims_the_tail(self):
        """相对判据不是"全放行"：低于 threshold*max 的长尾仍要被剔除。"""
        # max=1.0，阈值 0.5 → floor=0.5；0.4/0.1 应被剔除
        chunks = [
            _rrf_hit("head", 1.0),
            _rrf_hit("mid", 0.5),
            _rrf_hit("low", 0.4),
            _rrf_hit("tail", 0.1),
        ]
        svc = _service(chunks, use_reranking=False)

        result = await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=False, min_score_threshold=0.5)
        )

        ids = [c.chunk.chunk_id for c in result.chunks]
        assert ids == ["head", "mid"]
        assert "low" not in ids and "tail" not in ids

    async def test_non_positive_max_keeps_whole_batch(self):
        """max<=0（例如全部 0 分）时整批保留 —— 除零/无意义判据的兜底。"""
        chunks = [_rrf_hit("z1", 0.0), _rrf_hit("z2", 0.0)]
        svc = _service(chunks, use_reranking=False)

        result = await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=False, min_score_threshold=0.5)
        )

        assert [c.chunk.chunk_id for c in result.chunks] == ["z1", "z2"]

    async def test_bm25_fallback_also_uses_relative_judgement(self):
        """BM25 兜底同样无 rerank_score → 同一条相对判据生效。"""
        chunks = [
            ScoredChunk(
                chunk=_mk_chunk("b1"),
                score=12.5,
                rerank_score=None,
                retrieval_source=RetrievalMethod.BM25_FALLBACK,
            ),
            ScoredChunk(
                chunk=_mk_chunk("b2"),
                score=3.0,
                rerank_score=None,
                retrieval_source=RetrievalMethod.BM25_FALLBACK,
            ),
        ]
        svc = _service(chunks, use_reranking=False)

        result = await svc.retrieve(
            RetrievalRequest(
                query="x",
                use_reranking=False,
                use_bm25_fallback=False,
                min_score_threshold=0.5,
            )
        )

        assert [c.chunk.chunk_id for c in result.chunks] == ["b1"]


class TestRerankSemanticsUnchanged:
    """反证腿：rerank 路径的绝对阈值语义**一个字没改**。"""

    async def test_absolute_threshold_still_drops_below_threshold(self):
        """有 rerank_score → 绝对判据；0.4/0.2 照样被剔除（不能因 A.88 变宽松）。"""
        chunks = [
            _reranked_hit("r1", 0.9),
            _reranked_hit("r2", 0.4),
            _reranked_hit("r3", 0.2),
        ]
        svc = _service(chunks, use_reranking=True)

        result = await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=True, min_score_threshold=0.5)
        )

        assert [c.chunk.chunk_id for c in result.chunks] == ["r1"]

    async def test_absolute_judgement_ignores_batch_max(self):
        """绝对判据不看本批最高分：即使全批偏低，0.5 以下一律剔除。"""
        chunks = [_reranked_hit("w1", 0.45), _reranked_hit("w2", 0.40)]
        svc = _service(chunks, use_reranking=True)

        result = await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=True, min_score_threshold=0.5)
        )

        assert result.chunks == []

    async def test_threshold_zero_keeps_everything(self):
        """阈值 0 → 不做任何过滤（与修复前一致）。"""
        chunks = [_rrf_hit("k1", 1 / 61), _reranked_hit("k2", 0.01)]
        svc = _service(chunks, use_reranking=False)

        result = await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=False, min_score_threshold=0.0)
        )

        assert len(result.chunks) == 2


class TestFilterTrace:
    """留痕：mode 与真实 kept/dropped 必须能从固定事件名读到。"""

    async def test_relative_mode_is_logged(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake_logger)
        chunks = [_rrf_hit("h", 1.0), _rrf_hit("t", 0.1)]
        svc = _service(chunks, use_reranking=False)

        await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=False, min_score_threshold=0.5)
        )

        payload = next(
            c.kwargs
            for c in fake_logger.info.call_args_list
            if c.args and c.args[0] == "min_score_filter_applied"
        )
        assert payload["mode"] == "relative"
        assert payload["kept"] == 1
        assert payload["dropped"] == 1
        assert payload["relative_floor"] == pytest.approx(0.5)

    async def test_absolute_mode_is_logged(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake_logger)
        chunks = [_reranked_hit("a", 0.9), _reranked_hit("b", 0.1)]
        svc = _service(chunks, use_reranking=True)

        await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=True, min_score_threshold=0.5)
        )

        payload = next(
            c.kwargs
            for c in fake_logger.info.call_args_list
            if c.args and c.args[0] == "min_score_filter_applied"
        )
        assert payload["mode"] == "absolute"
        assert payload["relative_floor"] is None
        assert payload["kept"] == 1
        assert payload["dropped"] == 1

    async def test_mixed_mode_when_both_dimensions_present(self, monkeypatch):
        """同一批里既有 rerank 又有 RRF（BM25 兜底与 rerank 混合）→ mixed。"""
        fake_logger = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake_logger)
        chunks = [
            _reranked_hit("m1", 0.9),
            _rrf_hit("m2", 1.0),
            _rrf_hit("m3", 0.1),
        ]
        svc = _service(chunks, use_reranking=True)

        await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=True, min_score_threshold=0.5)
        )

        payload = next(
            c.kwargs
            for c in fake_logger.info.call_args_list
            if c.args and c.args[0] == "min_score_filter_applied"
        )
        assert payload["mode"] == "mixed"
        # rerank 0.9 过；RRF max=1.0 → floor=0.5 → 1.0 过、0.1 剔
        assert payload["kept"] == 2
        assert payload["dropped"] == 1


class TestGraphExemptionUnchanged:
    """既有豁免不许被顺手改动（另一文件的钉子也读这些计数）。"""

    async def test_graph_neighbor_still_exempt_under_relative_mode(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(service_module, "logger", fake_logger)
        chunks = [
            _rrf_hit("head", 1.0),
            ScoredChunk(
                chunk=_mk_chunk("g1"),
                score=0.0,
                rerank_score=None,
                retrieval_source=RetrievalMethod.GRAPH,
            ),
        ]
        svc = _service(chunks, use_reranking=False)

        result = await svc.retrieve(
            RetrievalRequest(query="x", use_reranking=False, min_score_threshold=0.5)
        )

        assert "g1" in [c.chunk.chunk_id for c in result.chunks]
        payload = next(
            c.kwargs
            for c in fake_logger.info.call_args_list
            if c.args and c.args[0] == "min_score_filter_applied"
        )
        assert payload["graph_exempt"] == 1


class TestCacheKeyCarriesSemantics:
    """缓存键：阈值数值相同但解释不同，必须换键（否则空结果被继续复用）。"""

    def test_semantics_version_is_declared(self):
        assert MIN_SCORE_FILTER_SEMANTICS == "a88-relative-v1"

    def test_key_differs_from_pre_a88_key(self):
        """反证腿：键必须**不等于** A.88 之前那份 canonical 算出的键。

        A.88 之前缓存里可能存着"被绝对阈值剔空"的空结果，而阈值数值没变
        （仍是 0.5）→ 只有把语义版本嵌进 canonical，旧键才会失效。
        """
        request = RetrievalRequest(query="x", min_score_threshold=0.5)
        current = RetrievalCache.make_key(request, "kb_v1.0.0")

        legacy_canonical = {
            "query": request.query,
            "organ": request.organ,
            "top_k": request.top_k,
            "use_query_enhancement": request.use_query_enhancement,
            "use_reranking": request.use_reranking,
            "use_bm25_fallback": request.use_bm25_fallback,
            "use_multimodal": request.use_multimodal,
            "use_llm_enhancement": request.use_llm_enhancement,
            "min_score_threshold": request.min_score_threshold,
            "image_format": request.image_format,
            "extra": json.dumps(request.extra or {}, sort_keys=True, ensure_ascii=False),
        }
        legacy_digest = hashlib.sha1(
            json.dumps(
                legacy_canonical, sort_keys=True, ensure_ascii=False
            ).encode("utf-8")
        ).hexdigest()[:16]
        legacy_key = f"rag:cache:thyroid:vkb_v1.0.0:{legacy_digest}"

        assert current != legacy_key, (
            "缓存键与 A.88 之前完全相同 —— 会把旧判据剔空的空结果继续喂给一致性校验"
        )

    def test_semantics_version_bump_changes_key(self, monkeypatch):
        request = RetrievalRequest(query="x", min_score_threshold=0.5)
        before = RetrievalCache.make_key(request, "kb_v1.0.0")

        monkeypatch.setattr(
            "app.rag.cache.MIN_SCORE_FILTER_SEMANTICS", "a88-relative-v2"
        )
        after = RetrievalCache.make_key(request, "kb_v1.0.0")

        assert before != after

    def test_key_is_stable_for_same_request(self):
        request = RetrievalRequest(query="x", min_score_threshold=0.5)
        assert RetrievalCache.make_key(request, "kb_v1.0.0") == RetrievalCache.make_key(
            RetrievalRequest(query="x", min_score_threshold=0.5), "kb_v1.0.0"
        )

    def test_threshold_value_still_in_key(self):
        a = RetrievalCache.make_key(RetrievalRequest(query="x", min_score_threshold=0.5), "kb_v1.0.0")
        b = RetrievalCache.make_key(RetrievalRequest(query="x", min_score_threshold=0.7), "kb_v1.0.0")
        assert a != b
