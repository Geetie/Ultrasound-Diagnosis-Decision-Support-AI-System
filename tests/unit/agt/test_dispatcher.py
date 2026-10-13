"""Unit tests for app.agents.dispatcher (T-GA-AGT-02).

Covers:
- route(): direct passthrough of target_organ
- aggregate(): empty input, single response, multi-response
- aggregate(): confidence averaging + content concatenation
- aggregate(): evidence dedup + reasoning_trace accumulation
- invoke(): single-organ dispatch with healthy specialist
- invoke(): single-organ dispatch with failing specialist (degradation)
- invoke(): unknown organ → degraded placeholder
- health_check(): all healthy / one unhealthy
- dispatch_all(): parallel multi-organ invocation
- ORGAN_TO_SPECIALIST mapping completeness
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.dispatcher import (
    ORGAN_TO_SPECIALIST,
    DispatcherAgentImpl,
    dispatch_all,
)
from app.agents.protocols import AgentContext, AgentResponse
from app.models.schemas import DiagnosisRequest, Organ


def _ctx(organ: Organ = Organ.THYROID) -> AgentContext:
    return AgentContext(
        request_id="r1",
        session_id="s1",
        organ=organ,
        user_query="q",
    )


def _req(organ: Organ = Organ.THYROID) -> DiagnosisRequest:
    return DiagnosisRequest(
        session_id="s1",
        image_ids=["img1"],
        target_organ=organ,
    )


class TestRoute:
    @pytest.mark.asyncio
    async def test_passthrough_thyroid(self):
        d = DispatcherAgentImpl(specialists={Organ.THYROID: object()})  # type: ignore[arg-type]
        # route() doesn't actually call specialists, so a placeholder is fine
        assert await d.route(_req(Organ.THYROID)) == Organ.THYROID

    @pytest.mark.asyncio
    async def test_passthrough_liver(self):
        d = DispatcherAgentImpl(specialists={Organ.LIVER: object()})  # type: ignore[arg-type]
        assert await d.route(_req(Organ.LIVER)) == Organ.LIVER

    @pytest.mark.asyncio
    async def test_passthrough_cardiac(self):
        d = DispatcherAgentImpl(specialists={Organ.CARDIAC: object()})  # type: ignore[arg-type]
        assert await d.route(_req(Organ.CARDIAC)) == Organ.CARDIAC


class TestAggregate:
    @pytest.mark.asyncio
    async def test_empty_input_returns_placeholder(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        resp = await d.aggregate([])
        assert resp.agent_name == "dispatcher"
        assert resp.confidence == 0.0
        # A.59：可读中文 + 转人工（原先正文是 `[dispatcher] no specialist responded`）
        assert "判读流程未能启动" in resp.content
        assert resp.needs_review is True
        assert resp.reasoning_trace == ["aggregated 0 specialist(s)"]

    @pytest.mark.asyncio
    async def test_single_response(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        single = AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content="[thyroid] result",
            confidence=0.85,
            evidence_chunk_ids=["c1"],
            reasoning_trace=["thyroid_done"],
        )
        resp = await d.aggregate([single])
        assert resp.confidence == 0.85
        assert resp.content == "[thyroid] result"
        assert resp.evidence_chunk_ids == ["c1"]
        assert resp.organ == Organ.THYROID
        assert "aggregated 1 specialist(s)" in resp.reasoning_trace
        # trace contains the original + aggregation summary
        assert "thyroid_done" in resp.reasoning_trace

    @pytest.mark.asyncio
    async def test_multi_response_averaging(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        responses = [
            AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="[thyroid] result",
                confidence=0.9,
                evidence_chunk_ids=["c1", "c2"],
                reasoning_trace=["t1"],
            ),
            AgentResponse(
                agent_name="liver_specialist",
                organ=Organ.LIVER,
                content="[liver] result",
                confidence=0.7,
                evidence_chunk_ids=["c2", "c3"],
                reasoning_trace=["l1"],
            ),
            AgentResponse(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="[cardiac] result",
                confidence=0.5,
                evidence_chunk_ids=["c4"],
                reasoning_trace=["c1"],
            ),
        ]
        resp = await d.aggregate(responses)
        # (0.9 + 0.7 + 0.5) / 3 = 0.7 (rounded to 4dp)
        assert resp.confidence == pytest.approx(0.7)
        # Content joined with double newline
        assert "[thyroid] result" in resp.content
        assert "[liver] result" in resp.content
        assert "[cardiac] result" in resp.content
        # Evidence dedup: c1, c2, c3, c4 (order preserved on first-seen)
        assert resp.evidence_chunk_ids == ["c1", "c2", "c3", "c4"]
        # Trace aggregation
        assert "aggregated 3 specialist(s)" in resp.reasoning_trace


class TestInvoke:
    @pytest.mark.asyncio
    async def test_healthy_specialist(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        resp = await d.invoke(_ctx(Organ.THYROID))
        assert resp.agent_name == "dispatcher"
        assert resp.confidence == pytest.approx(0.85)
        assert "[thyroid] result" in resp.content
        assert all_specialists[Organ.THYROID].invoke_count == 1
        # Latency is recorded and >= 0
        assert resp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_failing_specialist_degrades_gracefully(self, all_specialists):
        # Replace thyroid specialist with one that raises
        from tests.unit.agt.conftest import FakeSpecialist
        all_specialists[Organ.THYROID] = FakeSpecialist(
            name="broken_thyroid",
            organ=Organ.THYROID,
            raise_exc=RuntimeError("boom"),
        )
        d = DispatcherAgentImpl(specialists=all_specialists)
        resp = await d.invoke(_ctx(Organ.THYROID))
        assert resp.confidence == 0.0
        # A.59：正文给可读中文（原先直接是 `[thyroid] specialist_unavailable`）+ 转人工
        assert "甲状腺：专科判读不可用" in resp.content
        assert resp.needs_review is True
        # Implementation logs exception type name (not message) into trace
        # to avoid leaking sensitive error details.
        assert any("RuntimeError" in t for t in resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_unknown_organ_uses_placeholder(self, all_specialists):
        # Build a dispatcher that only knows thyroid
        d = DispatcherAgentImpl(
            specialists={Organ.THYROID: all_specialists[Organ.THYROID]}
        )
        # Invoke with LIVER (not registered)
        resp = await d.invoke(_ctx(Organ.LIVER))
        assert resp.confidence == 0.0
        # A.59：可读中文（部位名随响应 organ 走）+ 转人工
        assert "肝脏：专科判读不可用" in resp.content
        assert resp.needs_review is True
        assert "no registered specialist" in " ".join(resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_invoke_does_not_call_other_organs(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        await d.invoke(_ctx(Organ.THYROID))
        # Only thyroid specialist should be called
        assert all_specialists[Organ.THYROID].invoke_count == 1
        assert all_specialists[Organ.LIVER].invoke_count == 0
        assert all_specialists[Organ.CARDIAC].invoke_count == 0


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_all_healthy(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        assert await d.health_check() is True

    @pytest.mark.asyncio
    async def test_one_unhealthy(self, all_specialists):
        from tests.unit.agt.conftest import FakeSpecialist
        all_specialists[Organ.THYROID] = FakeSpecialist(
            name="unhealthy",
            organ=Organ.THYROID,
            healthy=False,
        )
        d = DispatcherAgentImpl(specialists=all_specialists)
        assert await d.health_check() is False


class TestCapabilityTags:
    def test_default_tags(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        tags = d.get_capability_tags()
        assert "dispatcher" in tags
        assert "routing" in tags


class TestOrganToSpecialist:
    def test_mapping_completeness(self):
        # All Organ enum values should be in the mapping
        for organ in Organ:
            assert organ in ORGAN_TO_SPECIALIST
        # Values should be lowercase strings
        for organ, name in ORGAN_TO_SPECIALIST.items():
            assert isinstance(name, str)
            assert name == name.lower()


class TestDispatchAll:
    @pytest.mark.asyncio
    async def test_parallel_multi_organ(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        responses = await dispatch_all(
            d, _ctx(Organ.THYROID), [Organ.THYROID, Organ.LIVER, Organ.CARDIAC]
        )
        assert len(responses) == 3
        organs = [r.organ for r in responses]
        assert organs == [Organ.THYROID, Organ.LIVER, Organ.CARDIAC]
        # All three specialists should have been called exactly once
        assert all_specialists[Organ.THYROID].invoke_count == 1
        assert all_specialists[Organ.LIVER].invoke_count == 1
        assert all_specialists[Organ.CARDIAC].invoke_count == 1

    @pytest.mark.asyncio
    async def test_empty_organ_list(self, all_specialists):
        d = DispatcherAgentImpl(specialists=all_specialists)
        responses = await dispatch_all(d, _ctx(), [])
        assert responses == []

    @pytest.mark.asyncio
    async def test_mixed_health_with_partial_failure(self, all_specialists):
        from tests.unit.agt.conftest import FakeSpecialist
        all_specialists[Organ.THYROID] = FakeSpecialist(
            name="broken",
            organ=Organ.THYROID,
            raise_exc=ConnectionError("nope"),
        )
        d = DispatcherAgentImpl(specialists=all_specialists)
        responses = await dispatch_all(
            d, _ctx(), [Organ.THYROID, Organ.LIVER, Organ.CARDIAC]
        )
        assert len(responses) == 3
        # Failed one: confidence 0
        assert responses[0].confidence == 0.0
        # A.59：可读中文 + 转人工（失败腿）
        assert "专科判读不可用" in responses[0].content
        assert responses[0].needs_review is True
        # Healthy ones: real confidence
        assert responses[1].confidence == pytest.approx(0.7)
        assert responses[2].confidence == pytest.approx(0.6)


class TestConstructor:
    def test_empty_specialists_rejected(self):
        with pytest.raises(ValueError):
            DispatcherAgentImpl(specialists={})
