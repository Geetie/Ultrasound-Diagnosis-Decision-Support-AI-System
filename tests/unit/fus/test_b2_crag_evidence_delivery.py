"""B2（02-4）CRAG 新证据「真进 prompt」+ M1~M12 不归零 的钉子测试。

覆盖 00-总览与对账表 §对账表 B2 的两条验收判据：
① 注入一条只有新块能回答的病例 → 结论/prompt 必须引用新块；
② 反证：删掉传参（S1 接线回归）后，质量门必须拒绝（假恢复不可能）。

同时钉住 03 §公共事实 5：CRAG 恢复成功后 M1~M12 整族字段不得归零。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = [pytest.mark.track_fus]

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.specialist.base import SpecialistAgent
from app.fusion.crag import CRAGHandler
from app.fusion.protocols import FusionRequest
from app.fusion.self_rag import SelfRAGEvalResult
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeRAGService,
    make_chunk,
    make_response,
    make_scored,
)


def _original(**kwargs) -> AgentResponse:
    kwargs.setdefault("agent_name", "thyroid_specialist")
    kwargs.setdefault("organ", Organ.THYROID)
    kwargs.setdefault("content", "TR3")
    kwargs.setdefault("confidence", 0.4)
    kwargs.setdefault("evidence", ["old_c1"])
    return make_response(**kwargs)


def _request(response: AgentResponse) -> FusionRequest:
    return FusionRequest(
        request_id="req-b2",
        session_id="sess-b2",
        agent_responses=[response],
        clinical_context={"user_query": "甲状腺结节 TR3 判读"},
    )


def _new_chunk() -> "object":
    return make_chunk(chunk_id="new_c1", content="新证据：TR3 边界模糊需随访")


# ======================================================================
# ① AgentContext.clinical_context 字段透传（protocols.py S1）
# ======================================================================


class TestAgentContextField:
    def test_default_empty_dict(self):
        ctx = AgentContext(
            request_id="r1", session_id="s1",
            organ=Organ.THYROID, user_query="q",
        )
        assert ctx.clinical_context == {}

    def test_crag_context_round_trip(self):
        ctx = AgentContext(
            request_id="r1", session_id="s1",
            organ=Organ.THYROID, user_query="q",
            clinical_context={"crag_evidence_chunks": [{"chunk_id": "new_c1"}]},
        )
        assert ctx.clinical_context["crag_evidence_chunks"][0]["chunk_id"] == "new_c1"

    def test_build_specialist_context_carries_crag_evidence(self):
        """S1 核心：_build_specialist_context 的新证据必须出现在
        AgentContext.clinical_context（改造前：1 写 0 读，整包被丢）。"""
        ctx = CRAGHandler._build_specialist_context(
            original=_original(),
            new_evidence=[_new_chunk()],
            request=_request(_original()),
        )
        chunks = ctx.clinical_context["crag_evidence_chunks"]
        assert [c["chunk_id"] for c in chunks] == ["new_c1"]
        assert chunks[0]["content"] == "新证据：TR3 边界模糊需随访"
        assert "new_c1" in ctx.clinical_context["crag_evidence_ids"]
        assert ctx.clinical_context["user_query"] == "甲状腺结节 TR3 判读"


# ======================================================================
# ② prompt 渲染：新块真的出现在 specialist prompt 里（base.py S2）
# ======================================================================


class _PromptHost:
    """_build_prompt 的最小宿主（只依赖 _prompt_template + _log_evidence_chain）。"""

    def __init__(self) -> None:
        self._prompt_template = "SYSTEM"

    def _log_evidence_chain(self, *args, **kwargs) -> None:  # noqa: D401
        return None


def _render(context: AgentContext) -> str:
    return SpecialistAgent._build_prompt(_PromptHost(), context, [], [])


class TestPromptRendering:
    def test_prompt_contains_crag_chunk(self):
        ctx = CRAGHandler._build_specialist_context(
            original=_original(),
            new_evidence=[_new_chunk()],
            request=_request(_original()),
        )
        prompt = _render(ctx)
        assert "[CRAG:new_c1]" in prompt
        assert "新证据：TR3 边界模糊需随访" in prompt
        assert "补充检索依据" in prompt

    def test_prompt_unchanged_without_crag_context(self):
        """非 CRAG 路径（clinical_context 为空）prompt 逐字不变 —— 行为零漂移。"""
        ctx = AgentContext(
            request_id="r1", session_id="s1",
            organ=Organ.THYROID, user_query="q",
        )
        assert "CRAG" not in _render(ctx)
        assert "补充检索依据" not in _render(ctx)


# ======================================================================
# ③ M1~M12 不归零（crag.py model_copy 两行修）
# ======================================================================


class TestMetricsNotZeroed:
    def _m_rich_response(self) -> AgentResponse:
        resp = _original(
            needs_review=True, disease_code="UDT-THY-TR3",
            used_evidence_ids=["old_c1"],
        )
        resp.differential_diagnoses = [SimpleNamespace(rank=1)]  # 占位断言用不到
        return resp

    def test_reinvoke_path_keeps_m_family(self):
        """重生成路径：invoker 返回的响应带 M 字段 → final 必须原样保留。"""
        from app.knowledge.protocols import KnowledgeChunk  # noqa: F401

        m_fields = {
            "measurement": None,
            "differential_diagnoses": ["ddx1"],
            "malignancy_risk": None,
            "clinical_actions": ["随访 6 个月"],
            "patient_explanation": None,
            "research_metrics": None,
            "cross_modality_hints": ["hint1"],
            "posterior_acoustic": None,
        }

        async def invoker(context):
            resp = AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="TR3（重生成，引用新证据）",
                confidence=0.9,
                evidence_chunk_ids=["new_c1"],
                reasoning_trace=["reinvoked"],
                needs_review=True,
                disease_code="UDT-THY-TR3",
                used_evidence_ids=["new_c1"],
            )
            for k, v in m_fields.items():
                setattr(resp, k, v)
            return resp

        handler = CRAGHandler(specialist_invoker=invoker)
        original = _original()
        new_resp, reinvoked = handler._build_new_response(
            original=original,
            new_evidence=[_new_chunk()],
            request=_request(original),
        )
        assert reinvoked is True
        assert new_resp.differential_diagnoses == ["ddx1"]
        assert new_resp.clinical_actions == ["随访 6 个月"]
        assert new_resp.cross_modality_hints == ["hint1"]
        assert new_resp.needs_review is True
        assert new_resp.disease_code == "UDT-THY-TR3"
        assert new_resp.used_evidence_ids == ["new_c1"]
        assert any(
            "crag_reinvoked_with_1_new_chunks" in t
            for t in new_resp.reasoning_trace
        )

    def test_placeholder_path_keeps_m_family(self):
        """占位路径（无 invoker）：confidence/证据变化，M 字段与 needs_review 保留。"""
        handler = CRAGHandler(specialist_invoker=None)
        original = _original(confidence=0.4, needs_review=True,
                             disease_code="UDT-THY-TR3")
        original.differential_diagnoses = ["ddx1"]
        new_resp, reinvoked = handler._build_new_response(
            original=original,
            new_evidence=[_new_chunk()],
            request=_request(original),
        )
        assert reinvoked is False
        assert new_resp.confidence == pytest.approx(0.6)
        assert new_resp.differential_diagnoses == ["ddx1"]
        assert new_resp.needs_review is True
        assert new_resp.disease_code == "UDT-THY-TR3"


# ======================================================================
# ④ 质量门前置断言：新块不在本次 prompt 里 → 不许判 recovered（S3）
# ======================================================================


class TestQualityGatePromptPrecondition:
    def _handler(self, invoker) -> CRAGHandler:
        return CRAGHandler(rag_service=FakeRAGService(
            chunks=[make_scored("new_c1", score=0.9,
                                content="新证据：TR3 边界模糊需随访")]
        ), specialist_invoker=invoker)

    async def _handle(self, handler):
        original = _original()
        return await handler.handle(
            original,
            SelfRAGEvalResult(passed=False, score=0.0),
            _request(original),
        )

    @pytest.mark.asyncio
    async def test_recovered_when_new_chunk_cited(self):
        """判据①：接线正确 + specialist 引用新块 → recovered=True。"""

        async def invoker(context):
            assert context.clinical_context["crag_evidence_chunks"], (
                "新块必须已在 context 里（判据①的前半）"
            )
            return AgentResponse(
                agent_name="thyroid_specialist", organ=Organ.THYROID,
                content="TR3", confidence=0.9,
                evidence_chunk_ids=["new_c1"],
            )

        result = await self._handle(self._handler(invoker))
        assert result.recovered is True
        assert "evidence_used" in result.reason

    @pytest.mark.asyncio
    async def test_rejected_when_wiring_broken(self, monkeypatch):
        """判据②反证：模拟"删掉传参"（context 里没有新块）→
        即使 specialist 独立命中同一 chunk，也必须判 evidence_not_used。
        这就是"假恢复不可能"的机器证据。"""
        real_build = CRAGHandler._build_specialist_context

        def stripped(**kwargs):
            ctx = real_build(**kwargs)
            # 模拟 S1 接线回归：clinical_context 未带上新块
            ctx = ctx.model_copy(update={"clinical_context": {}})
            return ctx

        monkeypatch.setattr(
            CRAGHandler, "_build_specialist_context",
            staticmethod(stripped),
        )

        async def invoker(context):
            # specialist 自己重跑检索"碰巧"命中同一 chunk —— 旧质量门挡不住
            return AgentResponse(
                agent_name="thyroid_specialist", organ=Organ.THYROID,
                content="TR3", confidence=0.9,
                evidence_chunk_ids=["new_c1"],
            )

        result = await self._handle(self._handler(invoker))
        assert result.recovered is False, (
            "新块不在本次 prompt 里时不得判恢复（S3 前置断言）"
        )
        assert "evidence_not_used" in result.reason

    @pytest.mark.asyncio
    async def test_not_cited_rejected(self):
        """specialist 不引用新块 → 既有判据保持：evidence_not_used。"""

        async def invoker(context):
            return AgentResponse(
                agent_name="thyroid_specialist", organ=Organ.THYROID,
                content="TR3", confidence=0.9, evidence_chunk_ids=["other"],
            )

        result = await self._handle(self._handler(invoker))
        assert result.recovered is False
        assert "evidence_not_used" in result.reason
