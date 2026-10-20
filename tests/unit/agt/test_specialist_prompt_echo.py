"""A.70 分支守卫：专科智能体必须**拒收**复述提示词示例的输出。

真机现场（A.70.1）：模型吐出的是
``证据: chunk_a1b2, chunk_c3d4 … 患者PATIENT_001: … # 输出 …（重复 4 次）``。
这类输出若继续走 `_parse_response`，会被解析成一个"看起来正常"的结论入库 ——
医生看到模板文字、评测被污染。本测试钉住：**解析前拒收 → 降级 + 转人工 + 留痕**。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.protocols import AgentContext
from app.agents.specialist.base import AgentResponse, RAGSpecialistAgent
from app.inference.protocols import InferenceResponse
from app.models.schemas import Organ

_ECHO = (
    "[thyroid] TR5 — # 输出 分级: TR2 总分: 2 置信度: 0.95 理由: 成分 1 + 回声 1 = 2 分 → TR2 "
    "证据: chunk_a1b2, chunk_c3d4 鉴别诊断: 1. 乳头状癌 | 0.60 "
    "患者PATIENT_001: 该结节为良性 # 输出 分级: TR5 总分: 14 置信度: 0.99"
)


class _Stub(RAGSpecialistAgent):
    organ = Organ.THYROID

    def __init__(self, llm_text: str, **kwargs) -> None:
        kwargs.setdefault("prompt_template", "stub prompt")
        kwargs.setdefault("inference_engine", MagicMock())
        kwargs.setdefault("knowledge_agent", MagicMock())
        super().__init__(**kwargs)
        self._llm_text = llm_text
        self.parse_called = False

    def get_organ(self) -> Organ:
        return self.organ

    def get_grading_system(self):
        return MagicMock()

    async def _retrieve_chunks(self, context, organ):
        return [MagicMock(chunk_id="kb_v1.3.0:pdf_x_0001#0000")]

    async def _call_llm_response(self, context, prompt, *, structured=False):
        return InferenceResponse(text=self._llm_text, finish_reason="stop")

    def _parse_response(self, llm_text, chunks, *, grade_override=None):
        self.parse_called = True
        return AgentResponse(
            agent_name="stub",
            organ=self.organ,
            content="正常结论",
            confidence=0.9,
            evidence_chunk_ids=["kb_v1.3.0:pdf_x_0001#0000"],
            reasoning_trace=[],
        )

    def _enrich_clinical_attributes(self, response, llm_text, chunks):
        return None


def _context() -> AgentContext:
    return AgentContext(
        request_id="req-echo-1",
        session_id="sess-echo-1",
        organ=Organ.THYROID,
        user_query="甲状腺低回声结节怎么判",
    )


@pytest.mark.asyncio
async def test_prompt_echo_output_is_rejected_before_parsing():
    agent = _Stub(_ECHO)
    response = await agent.invoke(_context())

    assert agent.parse_called is False, "脏输出不应进入解析（否则会被当成正常结论入库）"
    assert response.confidence == 0.0
    assert response.needs_review is True
    assert "格式异常" in response.content
    assert "示例" in response.content
    # 技术原因只进 trace，不进医生可见正文
    assert any(t.startswith("prompt_echo_rejected:") for t in response.reasoning_trace)
    assert "chunk_a1b2" not in response.content


@pytest.mark.asyncio
async def test_clean_output_still_goes_through_normal_parsing():
    agent = _Stub("甲状腺：右叶低回声结节，边界清。分级 TR3，建议六个月复查。")
    response = await agent.invoke(_context())

    assert agent.parse_called is True
    assert response.confidence == 0.9
    assert response.content == "正常结论"
