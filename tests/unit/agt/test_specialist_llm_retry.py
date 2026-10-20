"""A.74 分支守卫：LLM 调用**失败一次要重试一次**，仍失败才降级。

真机：411 条里 5 条带图请求死在 `llm_unavailable`，多为 vLLM 瞬时抖动。
一次 0.5s 退避重试的代价，远小于"整条判读作废"；但重试必须**可见**（trace + 日志），
不能悄悄多打一次请求。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.agents.protocols import AgentContext
from app.agents.specialist.base import AgentResponse, RAGSpecialistAgent
from app.inference.protocols import InferenceResponse
from app.models.schemas import Organ


class _FlakyAgent(RAGSpecialistAgent):
    organ = Organ.THYROID

    def __init__(self, fail_times: int, **kwargs) -> None:
        kwargs.setdefault("prompt_template", "stub prompt")
        kwargs.setdefault("inference_engine", MagicMock())
        kwargs.setdefault("knowledge_agent", MagicMock())
        super().__init__(**kwargs)
        self.fail_times = fail_times
        self.calls = 0

    def get_organ(self) -> Organ:
        return self.organ

    def get_grading_system(self):
        return MagicMock()

    async def _retrieve_chunks(self, context, organ):
        return [MagicMock(chunk_id="kb_v1.3.0:pdf_x_0001#0000")]

    async def _call_llm_response(self, context, prompt, *, structured=False):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("vllm reset")
        return InferenceResponse(text="甲状腺：右叶低回声结节，分级 TR3。", finish_reason="stop")

    def _parse_response(self, llm_text, chunks, *, grade_override=None):
        return AgentResponse(
            agent_name="stub",
            organ=self.organ,
            content="结论",
            confidence=0.9,
            evidence_chunk_ids=["kb_v1.3.0:pdf_x_0001#0000"],
            reasoning_trace=[],
        )

    def _enrich_clinical_attributes(self, response, llm_text, chunks):
        return None


def _context() -> AgentContext:
    return AgentContext(
        request_id="req-retry-1",
        session_id="sess-retry-1",
        organ=Organ.THYROID,
        user_query="甲状腺结节怎么判",
    )


@pytest.mark.asyncio
async def test_transient_failure_is_retried_once_and_recovers():
    agent = _FlakyAgent(fail_times=1)
    response = await agent.invoke(_context())

    assert agent.calls == 2, "第一次失败后必须且只能再试一次"
    assert response.confidence == 0.9
    assert any(t.startswith("llm_retry:") for t in response.reasoning_trace)
    assert any("recovered" in t for t in response.reasoning_trace)


@pytest.mark.asyncio
async def test_persistent_failure_degrades_after_two_attempts():
    agent = _FlakyAgent(fail_times=5)
    response = await agent.invoke(_context())

    assert agent.calls == 2, "有界重试：不允许无限重试"
    assert response.confidence == 0.0
    assert response.needs_review is True
