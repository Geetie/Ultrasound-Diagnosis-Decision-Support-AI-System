"""Unit tests for app.agents.protocols (T-GA-AGT-01).

Covers:
- Pydantic field validation for AgentContext / AgentResponse
- ABC subclass registration (cannot instantiate abstract classes directly)
- Default factories (prior_messages, evidence_chunk_ids, reasoning_trace)
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.protocols import (
    Agent,
    AgentContext,
    AgentResponse,
    DispatcherAgent,
    KnowledgeAgent,
    MonitorAgent,
)
from app.models.schemas import Organ


class TestAgentContext:
    def test_minimal_valid(self):
        ctx = AgentContext(
            request_id="r1",
            session_id="s1",
            organ=Organ.THYROID,
            user_query="甲状腺结节",
        )
        assert ctx.request_id == "r1"
        assert ctx.session_id == "s1"
        assert ctx.organ == Organ.THYROID
        assert ctx.user_query == "甲状腺结节"
        assert ctx.imaging_metadata is None
        assert ctx.prior_messages == []

    def test_empty_request_id_rejected(self):
        with pytest.raises(ValueError):
            AgentContext(
                request_id="",
                session_id="s1",
                organ=Organ.THYROID,
                user_query="q",
            )

    def test_empty_session_id_rejected(self):
        with pytest.raises(ValueError):
            AgentContext(
                request_id="r1",
                session_id="   ",
                organ=Organ.THYROID,
                user_query="q",
            )

    def test_empty_user_query_rejected(self):
        with pytest.raises(ValueError):
            AgentContext(
                request_id="r1",
                session_id="s1",
                organ=Organ.THYROID,
                user_query="",
            )

    def test_whitespace_user_query_rejected(self):
        with pytest.raises(ValueError):
            AgentContext(
                request_id="r1",
                session_id="s1",
                organ=Organ.THYROID,
                user_query="\n\t  ",
            )

    def test_full_with_imaging_metadata(self):
        ctx = AgentContext(
            request_id="r1",
            session_id="s1",
            organ=Organ.LIVER,
            user_query="肝内占位",
            imaging_metadata={"modality": "US", "frame_index": 3},
            prior_messages=[{"role": "user", "content": "previous"}],
        )
        assert ctx.imaging_metadata == {"modality": "US", "frame_index": 3}
        assert ctx.prior_messages == [{"role": "user", "content": "previous"}]


class TestAgentResponse:
    def test_minimal_valid(self):
        resp = AgentResponse(agent_name="x")
        assert resp.agent_name == "x"
        assert resp.organ is None
        assert resp.content == ""
        assert resp.confidence == 0.0
        assert resp.evidence_chunk_ids == []
        assert resp.reasoning_trace == []
        assert resp.latency_ms == 0.0

    def test_empty_agent_name_rejected(self):
        with pytest.raises(ValueError):
            AgentResponse(agent_name="")

    def test_confidence_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            AgentResponse(agent_name="x", confidence=1.5)
        with pytest.raises(ValueError):
            AgentResponse(agent_name="x", confidence=-0.1)

    def test_confidence_boundary_inclusive(self):
        # 0.0 and 1.0 are valid
        AgentResponse(agent_name="x", confidence=0.0)
        AgentResponse(agent_name="x", confidence=1.0)

    def test_full_payload(self):
        resp = AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content="ok",
            confidence=0.9,
            evidence_chunk_ids=["c1"],
            reasoning_trace=["step1", "step2"],
            latency_ms=120.5,
        )
        assert resp.latency_ms == 120.5
        assert resp.evidence_chunk_ids == ["c1"]


class TestAbstractClasses:
    def test_cannot_instantiate_agent(self):
        with pytest.raises(TypeError):
            Agent()  # type: ignore[abstract]

    def test_cannot_instantiate_dispatcher_agent(self):
        with pytest.raises(TypeError):
            DispatcherAgent()  # type: ignore[abstract]

    def test_cannot_instantiate_knowledge_agent(self):
        with pytest.raises(TypeError):
            KnowledgeAgent()  # type: ignore[abstract]

    def test_cannot_instantiate_monitor_agent(self):
        with pytest.raises(TypeError):
            MonitorAgent()  # type: ignore[abstract]
