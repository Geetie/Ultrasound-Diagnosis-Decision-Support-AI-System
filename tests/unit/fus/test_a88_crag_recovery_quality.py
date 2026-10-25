"""A.88 T5-6：CRAG ``recovered=True`` 必须**校验质量**，不能"检索非空就算恢复"。

## 为什么要这条测试

《架构真相》§2 最后一行：CRAG「真跑，单次、有界、有真实『重跑专科』接线；
但 ``recovered=True`` 只要求检索非空，不校验质量」。

后果：只要补充检索返回**一条**与结论无关的块，CRAG 就报 ``recovered=True``，
而 ``FusionEngineImpl`` 会据此**清掉** ``self_rag_failed`` 标记
（``service.py:214-217``）→ 一次其实没修好的质检从此"消失"，
``needs_review`` 也不再置位。这是"用空转冒充恢复"。

## 三条质量条件（任一满足才算恢复）

1. **重生成的响应真的引用了新证据**（``new_response.evidence_chunk_ids`` 与
   新检索到的 chunk 有交集）—— 说明新证据**被用上了**，不是拿回来堆着；
2. **检索分数达到阈值**（``crag_min_evidence_score``，默认 0.0 不启用，
   需要显式开启）—— 低分块不构成"证据充分"；
3. **重生成响应通过了 Self-RAG 复评**（给了 ``revalidator`` 时）—— 最直接的
   "这次真的够了"的判据。

不满足 → ``recovered=False``，``reason`` 写明是**哪一条**没过（AGENTS.md
硬规则 8：不许统一写死默认值）。

反证腿：把三条判断删回"检索非空即 True" → ``TestQualityGateRejects`` 全红。
"""
from __future__ import annotations

from app.agents.protocols import AgentResponse
from app.fusion.crag import CRAGHandler
from app.fusion.protocols import FusionRequest
from app.fusion.self_rag import SelfRAGEvalResult
from app.models.schemas import Organ
from tests.unit.fus.conftest import FakeRAGService, make_response, make_scored


def _request(resp: AgentResponse) -> FusionRequest:
    return FusionRequest(request_id="req-crag-q", agent_responses=[resp])


def _self_rag_failed() -> SelfRAGEvalResult:
    return SelfRAGEvalResult(
        passed=False,
        score=2.0,
        evidence_sufficiency=1.0,
        reason="self_rag_failed",
    )


def _invoker(*, evidence: list[str], content: str = "重生成结论", confidence: float = 0.9):
    """重生成时**明确引用**哪些 chunk（用来钉"新证据是否被用上"）。"""

    async def _invoke(context):
        return AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content=content,
            confidence=confidence,
            evidence_chunk_ids=list(evidence),
            reasoning_trace=["reinvoked"],
        )

    return _invoke


class TestQualityGateAccepts:
    async def test_recovers_when_new_evidence_is_actually_used(self):
        chunks = [make_scored("new_c1", score=0.9)]
        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks),
            specialist_invoker=_invoker(evidence=["new_c1"]),
        )
        resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)

        result = await handler.handle(resp, _self_rag_failed(), _request(resp))

        assert result.recovered is True
        assert result.new_response is not None
        assert result.quality_checked is True

    async def test_recovers_when_revalidation_passes(self):
        """新证据没被引用，但重生成响应**通过了 Self-RAG 复评** → 也算恢复。"""
        chunks = [make_scored("new_c1", score=0.9)]

        async def revalidator(response):
            return SelfRAGEvalResult(
                passed=True, score=4.0, evidence_sufficiency=4.0, reason=""
            )

        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks),
            specialist_invoker=_invoker(evidence=["old_only"]),
            revalidator=revalidator,
        )
        resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)

        result = await handler.handle(resp, _self_rag_failed(), _request(resp))

        assert result.recovered is True
        assert "revalidation" in result.reason or "quality" in result.reason


class TestQualityGateRejects:
    """反证腿集：三种"看起来恢复、实际没恢复"的情形都必须拒。"""

    async def test_rejects_when_new_evidence_ignored_and_no_revalidation(self):
        chunks = [make_scored("new_c1", score=0.9)]
        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks),
            # 重生成时**一条新证据都没引用**
            specialist_invoker=_invoker(evidence=["old_c1"]),
        )
        resp = make_response(
            organ=Organ.THYROID, content="TR3", confidence=0.7, evidence=["old_c1"]
        )

        result = await handler.handle(resp, _self_rag_failed(), _request(resp))

        assert result.recovered is False, (
            "新证据一条都没被引用 = 检索回来了但没用上，不能算恢复"
        )
        assert "evidence_not_used" in result.reason, (
            "必须说明是哪一条质量条件没过（不许统一写死）"
        )

    async def test_rejects_when_revalidation_fails(self):
        chunks = [make_scored("new_c1", score=0.9)]

        async def revalidator(response):
            return SelfRAGEvalResult(passed=False, score=1.0, reason="still_bad")

        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks),
            specialist_invoker=_invoker(evidence=["new_c1"]),
            revalidator=revalidator,
        )
        resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)

        result = await handler.handle(resp, _self_rag_failed(), _request(resp))

        assert result.recovered is False
        assert "revalidation_failed" in result.reason

    async def test_rejects_below_score_threshold(self):
        """开了分数阈值：低分块不构成"证据充分"。"""
        chunks = [make_scored("new_c1", score=0.12)]
        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks),
            specialist_invoker=_invoker(evidence=["new_c1"]),
            min_evidence_score=0.5,
        )
        resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)

        result = await handler.handle(resp, _self_rag_failed(), _request(resp))

        assert result.recovered is False
        assert "score_below_threshold" in result.reason

    async def test_placeholder_is_marked_unverified_not_silently_verified(self):
        """无 invoker（占位路径只合并 evidence，不重跑 LLM）→ **无法校验质量**。

        占位路径历史上同样返回 ``recovered=True``（``test_crag_invoker_wiring.py``
        记录了这个缺陷：从 ``FusionResult.crag_recovered`` 完全看不出它根本没重新
        生成）。本次不翻转它的 ``recovered`` 语义（那会破坏既有调用方契约），
        而是把"未校验"这件事**显式暴露**：``quality_checked=False`` +
        ``reason`` 带 ``unverified_`` 前缀。反证腿：把 quality_checked 硬写成
        True（或去掉前缀）→ 本用例红。
        """
        chunks = [make_scored("new_c1", score=0.9)]
        handler = CRAGHandler(rag_service=FakeRAGService(chunks=chunks))

        resp = make_response(
            organ=Organ.THYROID, content="TR3", confidence=0.7, evidence=["old_c1"]
        )
        result = await handler.handle(resp, _self_rag_failed(), _request(resp))

        assert result.recovered is True, "占位路径的既有 recovered 语义保持不变"
        assert result.quality_checked is False, (
            "没重新生成就没有可校验的东西 —— 不许自称已校验"
        )
        assert "unverified_" in result.reason


class TestReasonExplainsWhichConditionFailed:
    def test_conditions_are_named_individually(self):
        """每个条件的名字都要能从 reason 里读出来（便于运维定位）。"""
        from app.fusion.crag import CRAG_QUALITY_CONDITIONS

        assert set(CRAG_QUALITY_CONDITIONS) == {
            "evidence_not_used",
            "score_below_threshold",
            "revalidation_failed",
            "revalidation_error",
            "empty_reresponse_content",
        }


class TestDefaultThresholdIsOff:
    """默认不启用分数阈值（否则会改变既有线上行为）。"""

    def test_default_threshold_is_zero(self):
        handler = CRAGHandler(rag_service=FakeRAGService())

        assert handler._min_evidence_score == 0.0
