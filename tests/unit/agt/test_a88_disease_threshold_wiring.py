"""A.88 T5-2：病种级阈值必须有**生产调用方**（接线守卫 + 反证腿）。

## 为什么要这条测试

``app/agents/specialist/base.py:826`` 的 ``_check_disease_threshold`` 此前
**零调用方**：三个专科智能体都没在 ``_parse_response`` 末尾调它，而
``_post_process`` 自己又只查器官级阈值。于是 ``disease_thresholds``
（``factory.load_organ_thresholds`` 会真的从 ``organ_disease_thresholds`` 表
加载出来、注入进每个 specialist）**在生产上从未生效** —— 配置了病种级更严的
阈值，系统仍按器官级放行（《架构真相》§3.3「病种级阈值：无调用方」）。

## 钉住的四条不变量

1. 有 ``disease_code`` 且该病种配了阈值 → **走病种级**（更严的那个生效）；
2. 无 ``disease_code`` / 病种没配 → **走器官级**，**不猜**（不得按正文关键词
   硬编一个病种出来）；
3. 采用的**阈值与来源**必须写进 ``reasoning_trace``（医生/审计能问"凭什么让他
   复核/凭什么没让他复核"）；
4. 两个都没配 → 不检查、不改 trace（向后兼容：既有子类不该被凭空要求复核）。

反证腿：把 ``_post_process`` 里的调用删掉（回到"只查器官级"），
``TestPostProcessActuallyCallsIt`` 立刻红。
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.protocols import AgentResponse
from app.agents.specialist.base import RAGSpecialistAgent
from app.models.schemas import Organ


class _StubSpecialist(RAGSpecialistAgent):
    """只测阈值分支，检索/LLM 全部固定住。"""

    organ = Organ.THYROID

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("prompt_template", "stub prompt")
        kwargs.setdefault("inference_engine", MagicMock())
        kwargs.setdefault("knowledge_agent", MagicMock())
        super().__init__(**kwargs)

    def get_organ(self) -> Organ:
        return self.organ

    def get_grading_system(self):
        return MagicMock()

    async def _retrieve_chunks(self, context, organ):
        return [MagicMock(chunk_id="c1")]

    async def _call_llm_response(self, context, prompt, *, structured=False):
        """固定住 LLM（否则 MagicMock 引擎不能 await，会走降级分支）。"""
        from app.inference.protocols import InferenceResponse

        return InferenceResponse(text="llm output", finish_reason="stop")

    def _parse_response(self, llm_text, chunks, *, grade_override=None):
        return AgentResponse(
            agent_name="stub",
            organ=Organ.THYROID,
            content="TR4",
            confidence=0.70,
            evidence_chunk_ids=["c1"],
            reasoning_trace=[],
        )


def _ctx():
    from app.agents.protocols import AgentContext

    return AgentContext(
        request_id="req-threshold",
        session_id="sess-threshold",
        organ=Organ.THYROID,
        user_query="甲状腺结节",
    )


def _resp(confidence: float, disease_code: str | None = None) -> AgentResponse:
    return AgentResponse(
        agent_name="stub",
        organ=Organ.THYROID,
        content="TR4",
        confidence=confidence,
        disease_code=disease_code,
        reasoning_trace=[],
    )


class TestPostProcessActuallyCallsIt:
    """核心接线：``_post_process`` 必须真的走病种级阈值。"""

    def test_disease_threshold_wins_when_disease_code_present(self):
        """反证腿：删掉 _post_process 里的调用 → 本用例红（只会报器官级 0.50）。"""
        agent = _StubSpecialist(
            organ_threshold=0.50, disease_thresholds={"UDT-THYROID-PTC": 0.90}
        )
        response = _resp(0.70, disease_code="UDT-THYROID-PTC")

        agent._post_process(_ctx(), response)

        assert response.needs_review is True, "病种级 0.90 > 0.70，必须转人工复核"
        assert any("below_disease_threshold" in t for t in response.reasoning_trace), (
            "必须留'因何复核'的 trace，且来源要写明是病种级"
        )
        assert not any(
            "below_organ_threshold" in t for t in response.reasoning_trace
        ), "病种级已判定，不该再重复一条器官级标签"

    def test_organ_threshold_still_applies_without_disease_code(self):
        agent = _StubSpecialist(
            organ_threshold=0.90, disease_thresholds={"UDT-THYROID-PTC": 0.95}
        )
        response = _resp(0.70, disease_code=None)

        agent._post_process(_ctx(), response)

        assert response.needs_review is True
        assert any("below_organ_threshold" in t for t in response.reasoning_trace)

    def test_unknown_disease_code_falls_back_to_organ(self):
        """有病种码但该病种没配阈值 → 器官级；**不猜**、不报错。"""
        agent = _StubSpecialist(
            organ_threshold=0.90, disease_thresholds={"UDT-THYROID-PTC": 0.95}
        )
        response = _resp(0.70, disease_code="UDT-THYROID-SOMETHING-NEW")

        agent._post_process(_ctx(), response)

        assert response.needs_review is True
        assert any("below_organ_threshold" in t for t in response.reasoning_trace)


class TestAdoptedThresholdIsTraced:
    """采用的阈值与来源必须可审计（不管越没越线）。"""

    def test_trace_records_disease_scope_and_value(self):
        agent = _StubSpecialist(
            organ_threshold=0.50, disease_thresholds={"UDT-THYROID-PTC": 0.90}
        )
        response = _resp(0.95, disease_code="UDT-THYROID-PTC")

        agent._post_process(_ctx(), response)

        assert response.needs_review is False
        adopted = [t for t in response.reasoning_trace if t.startswith("threshold_adopted")]
        assert adopted, "没越线也必须记录采用的阈值与来源（否则无法解释'为什么不复核'）"
        assert "disease" in adopted[0]
        assert "0.9" in adopted[0]
        assert "UDT-THYROID-PTC" in adopted[0]

    def test_trace_records_organ_scope_without_disease_code(self):
        agent = _StubSpecialist(organ_threshold=0.85)
        response = _resp(0.95)

        agent._post_process(_ctx(), response)

        adopted = [t for t in response.reasoning_trace if t.startswith("threshold_adopted")]
        assert adopted and "organ" in adopted[0] and "0.85" in adopted[0]

    def test_no_threshold_configured_writes_nothing(self):
        """两者都没配 → 不检查、不写 trace（向后兼容）。"""
        agent = _StubSpecialist()
        response = _resp(0.10, disease_code="UDT-THYROID-PTC")

        agent._post_process(_ctx(), response)

        assert response.needs_review is False
        assert response.reasoning_trace == []

    def test_zero_confidence_skips_and_writes_no_threshold_trace(self):
        """已降级（conf=0）→ 不叠加阈值判定（A.59 语义：降级本身已转人工）。"""
        agent = _StubSpecialist(organ_threshold=0.85)
        response = _resp(0.0)

        agent._post_process(_ctx(), response)

        assert not any(
            "threshold_adopted" in t for t in response.reasoning_trace
        )


class TestInvokePathWiresIt:
    """端到端：``invoke`` 走完也必须产出病种级判定（而非只有单测直接调用）。"""

    @pytest.mark.asyncio
    async def test_invoke_with_disease_code_hits_disease_threshold(self):
        agent = _StubSpecialist(
            organ_threshold=0.50, disease_thresholds={"UDT-THYROID-PTC": 0.90}
        )
        # _parse_response 固定 confidence=0.70；病种码由响应带出
        original = agent._parse_response

        def _parse_with_code(llm_text, chunks, *, grade_override=None):
            resp = original(llm_text, chunks, grade_override=grade_override)
            resp.disease_code = "UDT-THYROID-PTC"
            return resp

        agent._parse_response = _parse_with_code  # type: ignore[method-assign]

        result = await agent.invoke(_ctx())

        assert result.needs_review is True
        assert any("below_disease_threshold" in t for t in result.reasoning_trace)
