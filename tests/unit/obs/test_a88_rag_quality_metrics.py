"""A.88 T4-⑤ 守卫：RAG 三指标**有真实生产者**，且"没测到"不等于 0。

## 修的真问题（架构真相 §3.3 / §3.5）

``retrieval_precision`` / ``recall`` / ``ndcg`` 在规格里存在，但仓库里既无指标
定义也无写入点 —— 看板上永远读不到这三个数。

## 诚实口径（本文件最核心的一组断言）

这三项都**必须有 ground truth** 才算得出来。用"检索到的结果"当自己的真值是
循环证明（会恒为 1.0）。所以：

1. **有真值 → 真算真写**（数值可逐位核对，不是"写了个常数"）；
2. **没真值 → 写 ``not_measured_<原因>`` 计数，绝不 ``set(0.0)``**
   （否则"召回率 0"与"从没测过"在看板上分不开）；
3. **生产路径真的调了生产者**（``RAGServiceImpl.retrieve``），且失败不影响检索。

反证腿：
* 把"没真值"分支改成 ``set_retrieval_quality(metric, 0.0)`` →
  ``TestNoGroundTruthIsNotZero`` 红；
* 把 service 里的调用删掉 → ``TestWiredIntoService`` 红；
* 把 nDCG 的 log2 折损去掉（改成一刀切）→ ``TestMetricMath`` 红。
"""

from __future__ import annotations

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_obs]

from app.knowledge.protocols import KnowledgeChunk  # noqa: E402
from app.observability.metrics_collector import rag_quality as quality_module  # noqa: E402
from app.observability.metrics_collector.rag_quality import (  # noqa: E402
    REASON_NO_GROUND_TRUTH,
    REASON_NO_RESULTS,
    RetrievalQuality,
    evaluate,
    evaluate_and_publish,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    report_unavailable,
)
from app.rag import service as service_module  # noqa: E402
from app.rag.protocols import (  # noqa: E402
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)


class _FakeCollector:
    """记录写入调用的假收集器（不碰真 Prometheus 单例）。"""

    def __init__(self):
        self.gauges: list[tuple[str, float]] = []
        self.not_measured: list[tuple[str, str]] = []
        self.measured: list[str] = []

    def set_retrieval_quality(self, metric: str, value: float) -> None:
        self.gauges.append((metric, value))

    def record_retrieval_quality_not_measured(self, metric: str, reason: str) -> None:
        self.not_measured.append((metric, reason))

    def record_retrieval_quality_measured(self, metric: str) -> None:
        self.measured.append(metric)


@pytest.fixture
def fake_collector(monkeypatch):
    collector = _FakeCollector()
    monkeypatch.setattr(quality_module, "_collector", lambda: collector)
    return collector


# ----------------------------------------------------------------------
# ① 指标数学：可逐位核对
# ----------------------------------------------------------------------


class TestMetricMath:
    def test_precision_at_k(self):
        ranked = ["a", "b", "c", "d"]
        relevant = {"a", "c"}
        assert precision_at_k(ranked, relevant, 2) == 0.5
        assert precision_at_k(ranked, relevant, 4) == 0.5

    def test_recall_at_k(self):
        ranked = ["a", "b", "c", "d"]
        relevant = {"a", "c", "z"}
        assert recall_at_k(ranked, relevant, 2) == pytest.approx(1 / 3)
        assert recall_at_k(ranked, relevant, 4) == pytest.approx(2 / 3)

    def test_ndcg_penalizes_late_hits(self):
        """命中越靠后 nDCG 越低（**反证：去掉 log2 折损就相等了**）。"""
        relevant = {"x"}
        early = ndcg_at_k(["x", "a", "b"], relevant, 3)
        late = ndcg_at_k(["a", "b", "x"], relevant, 3)
        assert early == pytest.approx(1.0)
        assert late < early
        assert late == pytest.approx((1 / math.log2(4)) / 1.0)

    def test_ndcg_is_capped_at_one(self):
        relevant = {"a", "b"}
        assert ndcg_at_k(["a", "b"], relevant, 5) == pytest.approx(1.0)

    def test_k_must_be_positive(self):
        for fn in (precision_at_k, recall_at_k, ndcg_at_k):
            with pytest.raises(ValueError):
                fn(["a"], {"a"}, 0)


# ----------------------------------------------------------------------
# ② "没测到" ≠ 0（本文件最重要的一组）
# ----------------------------------------------------------------------


class TestNoGroundTruthIsNotZero:
    def test_missing_ground_truth_yields_none_not_zero(self):
        """**核心反证**：无真值 → 三个指标都是 ``None``。

        反证：把 ``skip_reason`` 分支改成返回 0.0 → 本用例红。
        """
        quality = evaluate(["a", "b"], None)

        assert quality.precision is None
        assert quality.recall is None
        assert quality.ndcg is None
        assert quality.skip_reason == REASON_NO_GROUND_TRUTH
        assert quality.measured is False

    def test_empty_ground_truth_is_also_not_measured(self):
        quality = evaluate(["a"], [])
        assert quality.skip_reason == REASON_NO_GROUND_TRUTH
        assert quality.as_dict() == {"precision": None, "recall": None, "ndcg": None}

    def test_no_results_is_its_own_reason(self):
        quality = evaluate([], ["a"])
        assert quality.skip_reason == REASON_NO_RESULTS

    def test_publish_writes_not_measured_counters_not_zero_gauges(self, fake_collector):
        """写的是"没测到"计数，**一个 gauge 都不许写 0**。"""
        evaluate_and_publish(["a"], None)

        assert fake_collector.gauges == [], (
            "无 ground truth 时写了 gauge 值（会把「没测过」伪装成「测出 0」）"
        )
        reasons = {reason for _metric, reason in fake_collector.not_measured}
        assert reasons == {REASON_NO_GROUND_TRUTH}
        assert len(fake_collector.not_measured) == 3  # precision / recall / ndcg

    def test_report_unavailable_is_callable_without_ground_truth(self, fake_collector):
        report_unavailable()

        assert fake_collector.gauges == []
        assert len(fake_collector.not_measured) == 3

    def test_zero_recall_is_a_real_measurement(self, fake_collector):
        """真值一条都没命中 → **召回率 0 是真实测量结果**，必须如实写。"""
        evaluate_and_publish(["a", "b"], ["zzz"], k=2)

        written = dict(fake_collector.gauges)
        assert written["recall"] == 0.0
        assert written["precision"] == 0.0
        assert len(fake_collector.measured) == 3


# ----------------------------------------------------------------------
# ③ 有真值 → 真算真写
# ----------------------------------------------------------------------


class TestMeasuredPublishesValues:
    def test_values_are_published(self, fake_collector):
        values = evaluate_and_publish(["a", "b", "c"], ["a", "c"], k=3)

        assert values["precision"] == pytest.approx(2 / 3)
        assert values["recall"] == pytest.approx(1.0)
        assert values["ndcg"] == pytest.approx(
            (1.0 + 1 / math.log2(4)) / (1.0 + 1 / math.log2(3))
        )
        written = dict(fake_collector.gauges)
        assert set(written) == {"precision", "recall", "ndcg"}
        assert fake_collector.not_measured == []

    def test_evaluate_returns_measured_flag(self):
        quality = evaluate(["a"], ["a"])
        assert isinstance(quality, RetrievalQuality)
        assert quality.measured is True
        assert quality.skip_reason == ""

    def test_publish_failure_does_not_raise(self, monkeypatch):
        """观测链路故障不得影响调用方（评测脚本不因此崩）。"""

        def _boom():
            raise RuntimeError("collector down")

        monkeypatch.setattr(quality_module, "_collector", _boom)
        # 不抛异常，仍返回算出来的值
        values = evaluate_and_publish(["a"], ["a"])
        assert values["precision"] == 1.0


# ----------------------------------------------------------------------
# ④ 生产路径真的调了生产者
# ----------------------------------------------------------------------


def _mk_chunk(chunk_id: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="x",
        metadata={},
        embedding=None,
        source_doc_id="d",
        section_path="",
        token_count=4,
    )


def _mk_service(chunks) -> service_module.RAGServiceImpl:
    enhancer = MagicMock()

    async def _enhance(query, organ_hint=None, use_llm=False):
        out = MagicMock()
        out.enhanced = query
        out.llm_enhanced = False
        return out

    enhancer.enhance = _enhance
    retriever = MagicMock()
    retriever.retrieve = AsyncMock(return_value=RetrievalResult(chunks=chunks))
    retriever.close = AsyncMock()
    retriever.health_check = AsyncMock(return_value={})
    reranker = MagicMock()
    reranker.close = AsyncMock()
    heuristic = MagicMock()
    heuristic.close = AsyncMock()
    bm25 = MagicMock()
    bm25.should_trigger = MagicMock(return_value=False)
    bm25._index = MagicMock()
    bm25._index.close = AsyncMock()
    return service_module.RAGServiceImpl(
        query_enhancer=enhancer,
        retriever=retriever,
        reranker=reranker,
        heuristic_reranker=heuristic,
        bm25_fallback=bm25,
        rerank_output_top_k=5,
    )


class TestWiredIntoService:
    async def test_service_publishes_not_measured_without_ground_truth(
        self, fake_collector
    ):
        """**核心**：生产检索必须调用生产者（否则就是壳）。

        反证：删掉 service 里 ``report_unavailable(...)`` / ``evaluate_and_publish``
        那段 → 本用例红（计数器一条都没有）。
        """
        chunks = [
            ScoredChunk(
                chunk=_mk_chunk("c1"), score=0.9, retrieval_source=RetrievalMethod.DENSE
            )
        ]
        svc = _mk_service(chunks)
        await svc.retrieve(
            RetrievalRequest(query="q", organ="thyroid", top_k=5, use_reranking=False)
        )

        assert fake_collector.not_measured, "生产检索没有报告质量指标的测量状态"
        assert any(
            reason == REASON_NO_GROUND_TRUTH
            for _metric, reason in fake_collector.not_measured
        )

    async def test_service_measures_when_ground_truth_provided(self, fake_collector):
        chunks = [
            ScoredChunk(
                chunk=_mk_chunk("c1"), score=0.9, retrieval_source=RetrievalMethod.DENSE
            ),
            ScoredChunk(
                chunk=_mk_chunk("c2"), score=0.8, retrieval_source=RetrievalMethod.DENSE
            ),
        ]
        svc = _mk_service(chunks)
        await svc.retrieve(
            RetrievalRequest(
                query="q",
                organ="thyroid",
                top_k=5,
                use_reranking=False,
                min_score_threshold=0.0,
                extra={"relevant_chunk_ids": ["c1"]},
            )
        )

        written = dict(fake_collector.gauges)
        assert written.get("precision") == pytest.approx(0.5)
        assert written.get("recall") == pytest.approx(1.0)

    async def test_metrics_failure_does_not_break_retrieval(self, monkeypatch):
        def _boom(*args, **kwargs):
            raise RuntimeError("metrics exploded")

        monkeypatch.setattr(quality_module, "evaluate_and_publish", _boom)
        monkeypatch.setattr(quality_module, "report_unavailable", _boom)
        chunks = [
            ScoredChunk(
                chunk=_mk_chunk("c1"), score=0.9, retrieval_source=RetrievalMethod.DENSE
            )
        ]
        svc = _mk_service(chunks)
        result = await svc.retrieve(
            RetrievalRequest(query="q", organ="thyroid", top_k=5, use_reranking=False)
        )
        assert result.has_results is True


def test_metrics_are_defined():
    """三个指标必须有定义（否则写了也读不到）。"""
    from app.observability.metrics_collector import MetricsCollector

    collector = MetricsCollector()
    assert hasattr(collector, "retrieval_quality")
    assert hasattr(collector, "retrieval_quality_measurements")
    text = collector.get_metrics_text()
    assert "ultrasound_retrieval_quality" in text
    assert "ultrasound_retrieval_quality_measurements_total" in text
