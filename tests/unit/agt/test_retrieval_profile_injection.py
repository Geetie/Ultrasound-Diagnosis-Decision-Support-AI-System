"""T-GB-AGT-02 检索档位注入闭环测试。

覆盖「判定 → 注入 → 生效 → 医生确认回写」全链路：

1. ``RetrievalProfile`` / ``RoutingDecision`` 的序列化往返（确认回写依赖）；
2. ``KnowledgeAgentImpl.query(profile=...)`` 把档位真正写进 ``RetrievalRequest``；
3. 不同档位使用不同缓存键（标准路径结果不得被全量路径复用，反之亦然）；
4. 专科智能体把 ``AgentContext.retrieval_profile`` 透传给知识智能体，
   且对不支持 ``profile`` 的历史实现保持兼容；
5. 诊断管线把路由判定的档位注入 ``AgentContext``；
6. ``QueryEnhancer`` 的 ``use_llm=False`` 跳过 LLM 改写（标准路径更快）；
7. 确认接口：确认 → 改档位并重跑；拒绝 → 不改档位；无待确认项 → 400。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.knowledge import KnowledgeAgentImpl
from app.agents.protocols import Agent, AgentContext, AgentResponse
from app.agents.routing import (
    QueryComplexity,
    RetrievalProfile,
    RoutingDecision,
    RoutingReason,
)
from app.agents.specialist.base import RAGSpecialistAgent
from app.models.schemas import Organ, ResponseTier
from app.rag.query_enhancer import QueryEnhancer

from tests.unit.agt.conftest import FakeRAGService, FakeRedis, make_chunk


# ----------------------------------------------------------------------
# 桩件
# ----------------------------------------------------------------------


class _ProfileAwareKnowledge:
    """支持 ``profile`` 的知识智能体替身。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def query(self, query, organ, top_k=10, profile=None):
        self.calls.append(
            {"query": query, "organ": organ, "top_k": top_k, "profile": profile}
        )
        return []


class _LegacyKnowledge:
    """仅支持三参数签名的历史实现（不得因新增 profile 而崩）。"""

    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []

    async def query(self, query, organ, top_k=10):
        self.calls.append({"query": query, "organ": organ, "top_k": top_k})
        return []


class _StubSpecialist(RAGSpecialistAgent):
    organ = Organ.THYROID

    def get_organ(self) -> Organ:
        return Organ.THYROID

    def get_grading_system(self) -> str:
        return "TI-RADS"

    async def _parse_response(self, llm_text, chunks, context):  # noqa: D102
        return AgentResponse(
            agent_name="stub_thyroid", organ=Organ.THYROID, content=llm_text or ""
        )


class _StubUDT:
    def __init__(self, organ: Optional[str] = None, terms: Optional[list] = None):
        self._organ = organ
        self._terms = terms or []

    def parse(self, text, organ=None):
        from app.knowledge.udt_parser import ParsedAttribute, UDTParseResult

        attrs = [
            ParsedAttribute(category="echogenicity", value=t, raw_text=t)
            for t in self._terms
        ]
        return UDTParseResult(
            raw_text=text,
            attributes=attrs,
            normalized_terms=[a.value for a in attrs],
            organ=organ or self._organ,
            unrecognized_terms=[],
        )


def _decision(profile: RetrievalProfile, *, requires_confirmation: bool = True) -> RoutingDecision:
    return RoutingDecision(
        requested_tier=ResponseTier.STANDARD,
        effective_tier=ResponseTier.STANDARD,
        target_tier=ResponseTier.FULL,
        complexity=QueryComplexity.MULTI_FEATURE,
        requires_confirmation=requires_confirmation,
        reasons=(RoutingReason.MULTI_FEATURE_NEEDS_CONFIRMATION,),
        retrieval_profile=profile,
        feature_categories=("echogenicity", "margin"),
        detected_organs=("thyroid",),
    )


# ----------------------------------------------------------------------
# 1. 序列化往返
# ----------------------------------------------------------------------


class TestProfileRoundTrip:
    def test_standard_round_trip(self):
        profile = RetrievalProfile.standard(top_k=7)
        restored = RetrievalProfile.from_dict(profile.as_dict())
        assert restored == profile

    def test_full_round_trip(self):
        profile = RetrievalProfile.full(top_k=25)
        restored = RetrievalProfile.from_dict(profile.as_dict())
        assert restored == profile

    def test_from_retrieval_kwargs_subset(self):
        kwargs = RetrievalProfile.full().as_retrieval_kwargs()
        restored = RetrievalProfile.from_dict(kwargs)
        assert restored.use_reranking is True
        assert restored.top_k == kwargs["top_k"]

    def test_unknown_keys_ignored_and_defaults_filled(self):
        restored = RetrievalProfile.from_dict({"use_reranking": True, "junk": 1})
        assert restored.use_reranking is True
        # 缺失字段回退到 standard（保守：宁可少升级）
        assert restored.use_llm_enhancement is False
        assert restored.top_k == RetrievalProfile.standard().top_k

    def test_empty_payload_falls_back_to_standard(self):
        assert RetrievalProfile.from_dict(None) == RetrievalProfile.standard()
        assert RetrievalProfile.from_dict({}) == RetrievalProfile.standard()


class TestDecisionRoundTrip:
    def test_round_trip_preserves_pending_upgrade(self):
        decision = _decision(RetrievalProfile.standard())
        restored = RoutingDecision.from_dict(decision.as_dict())
        assert restored is not None
        assert restored.pending_upgrade is True
        assert restored.retrieval_profile == decision.retrieval_profile
        assert restored.reasons == decision.reasons
        assert restored.complexity == decision.complexity

    def test_upgraded_decision_is_not_pending(self):
        decision = _decision(RetrievalProfile.full(), requires_confirmation=False)
        restored = RoutingDecision.from_dict(decision.as_dict())
        assert restored is not None
        assert restored.pending_upgrade is False

    def test_invalid_payload_returns_none(self):
        assert RoutingDecision.from_dict(None) is None
        assert RoutingDecision.from_dict({"requested_tier": "nope"}) is None
        assert RoutingDecision.from_dict("not-a-dict") is None


# ----------------------------------------------------------------------
# 2/3. 知识智能体：档位生效 + 缓存键隔离
# ----------------------------------------------------------------------


class TestKnowledgeProfileInjection:
    @pytest.mark.asyncio
    async def test_profile_applied_to_retrieval_request(self):
        rag = FakeRAGService(chunks=[make_chunk()])
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        await agent.query(
            "低回声结节", Organ.THYROID, top_k=5,
            profile=RetrievalProfile.full(top_k=20).as_retrieval_kwargs(),
        )

        assert rag.last_request is not None
        assert rag.last_request.use_reranking is True
        assert rag.last_request.use_llm_enhancement is True
        assert rag.last_request.top_k == 20  # 档位的检索深度

    @pytest.mark.asyncio
    async def test_standard_profile_disables_reranking(self):
        rag = FakeRAGService(chunks=[make_chunk()])
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        await agent.query(
            "低回声结节", Organ.THYROID, top_k=5,
            profile=RetrievalProfile.standard(top_k=10).as_retrieval_kwargs(),
        )

        assert rag.last_request.use_reranking is False
        assert rag.last_request.use_llm_enhancement is False
        assert rag.last_request.top_k == 10

    @pytest.mark.asyncio
    async def test_no_profile_keeps_default_behaviour(self):
        rag = FakeRAGService(chunks=[make_chunk()])
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        await agent.query("低回声结节", Organ.THYROID, top_k=5)

        # 未传档位 → 使用 RetrievalRequest 自身默认值（与引入前一致）
        assert rag.last_request.use_reranking is True
        assert rag.last_request.top_k == 5

    @pytest.mark.asyncio
    async def test_caller_top_k_still_truncates_result(self):
        rag = FakeRAGService(chunks=[make_chunk(f"c{i}") for i in range(5)])
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        chunks = await agent.query(
            "q", Organ.THYROID, top_k=2,
            profile=RetrievalProfile.full(top_k=50).as_retrieval_kwargs(),
        )
        assert len(chunks) == 2

    def test_cache_key_differs_by_profile(self):
        std = KnowledgeAgentImpl._make_cache_key(
            Organ.THYROID, "甲状腺", "kb_v1.0.0",
            KnowledgeAgentImpl._profile_fingerprint(RetrievalProfile.standard().as_dict()),
        )
        full = KnowledgeAgentImpl._make_cache_key(
            Organ.THYROID, "甲状腺", "kb_v1.0.0",
            KnowledgeAgentImpl._profile_fingerprint(RetrievalProfile.full().as_dict()),
        )
        assert std != full

    def test_cache_key_unchanged_without_profile(self):
        """不传档位时缓存键格式保持不变（兼容既有缓存与断言）。"""
        key = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "甲状腺", "kb_v1.0.0")
        assert key == KnowledgeAgentImpl._make_cache_key(
            Organ.THYROID, "甲状腺", "kb_v1.0.0", ""
        )

    @pytest.mark.asyncio
    async def test_profile_scoped_cache_isolated_end_to_end(self):
        """同一查询在标准/全量档位下各自回源一次，不互相命中。"""
        rag = FakeRAGService(chunks=[make_chunk()])
        redis = FakeRedis()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        std = RetrievalProfile.standard(top_k=10).as_retrieval_kwargs()
        full = RetrievalProfile.full(top_k=20).as_retrieval_kwargs()

        await agent.query("结节", Organ.THYROID, top_k=5, profile=std)
        await agent.query("结节", Organ.THYROID, top_k=5, profile=full)
        assert rag.retrieve_call_count == 2

        # 各自再查一次 → 命中缓存，不再回源
        await agent.query("结节", Organ.THYROID, top_k=5, profile=std)
        await agent.query("结节", Organ.THYROID, top_k=5, profile=full)
        assert rag.retrieve_call_count == 2


# ----------------------------------------------------------------------
# 4. 专科智能体透传
# ----------------------------------------------------------------------


class TestSpecialistPropagatesProfile:
    def _specialist(self, knowledge):
        return _StubSpecialist(
            inference_engine=MagicMock(),
            knowledge_agent=knowledge,
            prompt_template="stub",
        )

    @pytest.mark.asyncio
    async def test_profile_passed_when_supported(self):
        knowledge = _ProfileAwareKnowledge()
        specialist = self._specialist(knowledge)
        profile = RetrievalProfile.full(top_k=20).as_retrieval_kwargs()
        context = AgentContext(
            request_id="req_1",
            session_id="sess_1",
            organ=Organ.THYROID,
            user_query="低回声结节",
            retrieval_profile=profile,
        )

        await specialist._retrieve_chunks(context, Organ.THYROID)

        assert knowledge.calls[0]["profile"] == profile

    @pytest.mark.asyncio
    async def test_legacy_knowledge_agent_without_profile_still_works(self):
        knowledge = _LegacyKnowledge()
        specialist = self._specialist(knowledge)
        assert specialist._knowledge_accepts_profile is False

        context = AgentContext(
            request_id="req_1",
            session_id="sess_1",
            organ=Organ.THYROID,
            user_query="低回声结节",
            retrieval_profile=RetrievalProfile.full().as_retrieval_kwargs(),
        )
        await specialist._retrieve_chunks(context, Organ.THYROID)

        assert "profile" not in knowledge.calls[0]

    @pytest.mark.asyncio
    async def test_no_profile_in_context_omits_kwarg(self):
        knowledge = _ProfileAwareKnowledge()
        specialist = self._specialist(knowledge)
        context = AgentContext(
            request_id="req_1",
            session_id="sess_1",
            organ=Organ.THYROID,
            user_query="低回声结节",
        )

        await specialist._retrieve_chunks(context, Organ.THYROID)

        assert knowledge.calls[0]["profile"] is None


# ----------------------------------------------------------------------
# 5. 管线注入
# ----------------------------------------------------------------------


class TestPipelineInjectsProfile:
    @pytest.mark.asyncio
    async def test_safe_dispatch_sets_context_profile(self):
        from app.services.diagnosis_pipeline import _safe_dispatch

        captured: Dict[str, Any] = {}

        async def _invoke(context):
            captured["context"] = context
            return AgentResponse(agent_name="d", organ=context.organ, content="ok")

        dispatcher = MagicMock()
        dispatcher.invoke = AsyncMock(side_effect=_invoke)

        request = MagicMock()
        request.request_id = "req_1"
        request.session_id = "sess_1"
        request.organ = "thyroid"
        request.clinical_text = "低回声结节"

        decision = _decision(RetrievalProfile.full(top_k=20))

        with patch("app.main.app") as mock_app:
            mock_app.state.dispatcher = dispatcher
            await _safe_dispatch(request, "req_1", routing=decision)

        assert captured["context"].retrieval_profile == decision.retrieval_profile.as_retrieval_kwargs()

    @pytest.mark.asyncio
    async def test_safe_dispatch_without_routing_leaves_profile_none(self):
        from app.services.diagnosis_pipeline import _safe_dispatch

        captured: Dict[str, Any] = {}

        async def _invoke(context):
            captured["context"] = context
            return AgentResponse(agent_name="d", organ=context.organ, content="ok")

        dispatcher = MagicMock()
        dispatcher.invoke = AsyncMock(side_effect=_invoke)

        request = MagicMock()
        request.session_id = "sess_1"
        request.organ = "thyroid"
        request.clinical_text = "x"

        with patch("app.main.app") as mock_app:
            mock_app.state.dispatcher = dispatcher
            await _safe_dispatch(request, "req_1")

        assert captured["context"].retrieval_profile is None


# ----------------------------------------------------------------------
# 6. 查询增强：use_llm 档位
# ----------------------------------------------------------------------


class _RecordingLLM:
    def __init__(self) -> None:
        self.calls = 0

    async def enhance(self, original_query, udaf_expansion, organ=None):
        self.calls += 1
        return "llm rewritten"


class TestQueryEnhancerUseLLM:
    @pytest.mark.asyncio
    async def test_use_llm_false_skips_llm(self):
        llm = _RecordingLLM()
        enhancer = QueryEnhancer(
            vocabulary=MagicMock(), udt_parser=_StubUDT(), llm_enhancer=llm
        )
        # UDAF 扩展走空词表（MagicMock），主要断言 LLM 未被调用
        result = await enhancer.enhance("低回声结节", use_llm=False)
        assert llm.calls == 0
        assert result.llm_enhanced is False

    @pytest.mark.asyncio
    async def test_use_llm_true_calls_llm(self):
        llm = _RecordingLLM()
        enhancer = QueryEnhancer(
            vocabulary=MagicMock(), udt_parser=_StubUDT(), llm_enhancer=llm
        )
        result = await enhancer.enhance("低回声结节", use_llm=True)
        assert llm.calls == 1
        assert result.llm_enhanced is True


# ----------------------------------------------------------------------
# 7. 医生确认接口
# ----------------------------------------------------------------------


class _FakeRepo:
    def __init__(self, result) -> None:
        self._result = result
        self.db = MagicMock()
        self.db.commit = AsyncMock()
        self.update_findings = AsyncMock(return_value=True)
        self.update_tier = AsyncMock(return_value=True)
        self.update_result = AsyncMock(return_value=result)

    async def get_result(self, request_id):
        return self._result


class TestConfirmUpgradeEndpoint:
    def _result_row(self, decision: RoutingDecision):
        row = MagicMock()
        row.request_id = "req_1"
        row.session_id = "sess_1"
        row.status = "completed"
        row.confidence = 0.8
        row.recommendations = "rec"
        row.knowledge_ids = ["k1"]
        row.findings = {"organ": "thyroid", "routing": decision.as_dict()}
        return row

    @pytest.mark.asyncio
    async def test_confirm_enqueues_rerun_with_full_tier(self):
        from app.api.v1.diagnosis import confirm_routing_upgrade
        from app.models.schemas import RoutingConfirmationRequest

        decision = _decision(RetrievalProfile.standard())
        repo = _FakeRepo(self._result_row(decision))
        audit = MagicMock()
        audit.log_action = AsyncMock()

        with patch(
            "app.api.v1.diagnosis.enqueue_diagnosis", AsyncMock(return_value=True)
        ) as enqueue:
            resp = await confirm_routing_upgrade(
                request_id="req_1",
                payload=RoutingConfirmationRequest(confirmed=True),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user={"user_id": "doc_1"},
            )

        assert resp.confirmed is True
        assert resp.effective_tier == ResponseTier.FULL
        assert resp.requires_confirmation is False
        assert resp.rerun_enqueued is True
        repo.update_tier.assert_awaited()
        assert repo.update_tier.await_args.args[1] == "full"
        enqueue.assert_awaited_once_with("req_1")
        # 重跑期间状态回到 processing
        assert repo.update_result.await_args.kwargs["status"] == "processing"
        assert audit.log_action.await_args.kwargs["action"] == "routing_upgrade_confirmation"

    @pytest.mark.asyncio
    async def test_decline_keeps_tier_and_does_not_rerun(self):
        from app.api.v1.diagnosis import confirm_routing_upgrade
        from app.models.schemas import RoutingConfirmationRequest

        decision = _decision(RetrievalProfile.standard())
        repo = _FakeRepo(self._result_row(decision))
        audit = MagicMock()
        audit.log_action = AsyncMock()

        with patch(
            "app.api.v1.diagnosis.enqueue_diagnosis", AsyncMock(return_value=True)
        ) as enqueue:
            resp = await confirm_routing_upgrade(
                request_id="req_1",
                payload=RoutingConfirmationRequest(confirmed=False),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user={"user_id": "doc_1"},
            )

        assert resp.confirmed is False
        assert resp.effective_tier == ResponseTier.STANDARD
        assert resp.rerun_enqueued is False
        repo.update_tier.assert_not_awaited()
        enqueue.assert_not_awaited()
        repo.update_findings.assert_awaited()
        assert repo.update_findings.await_args.args[1]["routing"]["confirmation"][
            "confirmed"
        ] is False

    @pytest.mark.asyncio
    async def test_confirm_when_rerun_enqueue_fails_restores_status(self):
        from app.api.v1.diagnosis import confirm_routing_upgrade
        from app.models.schemas import RoutingConfirmationRequest

        decision = _decision(RetrievalProfile.standard())
        repo = _FakeRepo(self._result_row(decision))
        audit = MagicMock()
        audit.log_action = AsyncMock()

        with patch(
            "app.api.v1.diagnosis.enqueue_diagnosis", AsyncMock(return_value=False)
        ):
            resp = await confirm_routing_upgrade(
                request_id="req_1",
                payload=RoutingConfirmationRequest(confirmed=True),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user=None,
            )

        assert resp.rerun_enqueued is False
        # 最后一次写回把状态恢复为原值，避免前端永久轮询
        assert repo.update_result.await_args.kwargs["status"] == "completed"

    @pytest.mark.asyncio
    async def test_no_pending_upgrade_raises_bad_request(self):
        from app.api.v1.diagnosis import confirm_routing_upgrade
        from app.api.v1.errors import BadRequestException
        from app.models.schemas import RoutingConfirmationRequest

        decision = _decision(RetrievalProfile.full(), requires_confirmation=False)
        repo = _FakeRepo(self._result_row(decision))
        audit = MagicMock()
        audit.log_action = AsyncMock()

        with pytest.raises(BadRequestException):
            await confirm_routing_upgrade(
                request_id="req_1",
                payload=RoutingConfirmationRequest(confirmed=True),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user=None,
            )

    @pytest.mark.asyncio
    async def test_missing_routing_raises_bad_request(self):
        from app.api.v1.diagnosis import confirm_routing_upgrade
        from app.api.v1.errors import BadRequestException
        from app.models.schemas import RoutingConfirmationRequest

        row = MagicMock()
        row.findings = {"organ": "thyroid"}
        repo = _FakeRepo(row)
        audit = MagicMock()
        audit.log_action = AsyncMock()

        with pytest.raises(BadRequestException):
            await confirm_routing_upgrade(
                request_id="req_1",
                payload=RoutingConfirmationRequest(confirmed=True),
                diagnosis_repo=repo,
                audit_service=audit,
                current_user=None,
            )
