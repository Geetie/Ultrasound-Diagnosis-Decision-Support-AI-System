"""P2 features tests (P2-4 / P2-6 / P2-8 / P2-9) — FUS track.

P2-4: ``_compare_with_guidelines`` 语义级比对（不仅是 chunk 数）
P2-6: CRAG 真正重新调用 specialist LLM
P2-8: CRAG 经 DispatcherAgent 中央调度路由
P2-9: 新功能测试覆盖（自身）

设计原则：
- 覆盖每条规则的「正常 / 边界 / 失败」三个场景；
- 沿用 conftest 提供的工厂函数与 FakeRAGService；
- 避免 LLM 调用（保持测试 P99 < 5s）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_fus]

from app.agents.protocols import AgentContext, AgentResponse
from app.fusion.consistency import ConsistencyChecker
from app.fusion.crag import CRAGHandler
from app.fusion.protocols import (
    ConsistencySeverity,
    FusionRequest,
)
from app.fusion.self_rag import SelfRAGEvalResult
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeRAGService,
    make_chunk,
    make_response,
    make_scored,
)

# ======================================================================
# P2-4 修复：_compare_with_guidelines 语义级比对
# ======================================================================


class TestP24SemanticGuidelineCompare:
    """P2-4 修复：_compare_with_guidelines 关键字级语义比对。"""

    @pytest.mark.asyncio
    async def test_p24_recommendation_conflict_biopsy_vs_followup_tr5(self):
        """P2-4 场景 1：response 建议活检（TR5），guideline 说 TR5 随访即可 → CRITICAL。

        临床背景：TR5 本应强烈推荐活检，guideline 误述为"随访即可，避免活检"
        会导致漏诊。冲突级别应为 CRITICAL。
        """
        # guideline chunk：TR5 + 建议随访（与 specialist 建议活检冲突）
        chunks = [
            make_scored(
                "g_tr5_followup",
                content="TI-RADS 5 应随访即可，避免活检（错误指南）",
            )
        ]
        rag = FakeRAGService(chunks=chunks)
        checker = ConsistencyChecker(rag_service=rag)
        resp = make_response(
            organ=Organ.THYROID,
            content="TI-RADS 5 高度怀疑恶性，建议 FNA 活检",
            confidence=0.92,
        )
        request = FusionRequest(agent_responses=[resp])

        result = await checker.check(request)

        # 同分级（TR5）下的强冲突（活检 vs 随访）→ CRITICAL
        assert result.severity == ConsistencySeverity.CRITICAL
        assert "guideline_recommendation_conflict" in result.detail

    @pytest.mark.asyncio
    async def test_p24_recommendation_mismatch_biopsy_vs_surgery(self):
        """P2-4 场景 2：response 建议活检，guideline 说手术 → WARNING（非强冲突）。

        临床背景：活检 vs 手术是同一器官同一分级的合理临床决策路径
        （活检确诊后手术），非活检 vs 随访那种「漏诊」型强冲突 → WARNING。
        """
        chunks = [
            make_scored(
                "g_tr4_surgery",
                content="TI-RADS 4 直接建议手术切除，无需活检",
            )
        ]
        rag = FakeRAGService(chunks=chunks)
        checker = ConsistencyChecker(rag_service=rag)
        resp = make_response(
            organ=Organ.THYROID,
            content="TI-RADS 4 可疑，建议 FNA 活检进一步确诊",
            confidence=0.85,
        )
        request = FusionRequest(agent_responses=[resp])

        result = await checker.check(request)

        # 活检 vs 手术 → 不在强冲突对中 → WARNING
        assert result.severity == ConsistencySeverity.WARNING
        assert "guideline_recommendation_mismatch" in result.detail

    @pytest.mark.asyncio
    async def test_p24_contraindication_conflict(self):
        """P2-4 场景 3：response 计划触及 guideline 列出的禁忌 → CRITICAL。

        临床背景：response 建议"手术"，但 guideline 明确说"手术禁忌，
        应保守治疗" → 严重临床冲突 → CRITICAL。
        """
        chunks = [
            make_scored(
                "g_contraindication",
                content="严重合并症患者手术为禁忌，应保守治疗",
            )
        ]
        rag = FakeRAGService(chunks=chunks)
        checker = ConsistencyChecker(rag_service=rag)
        resp = make_response(
            organ=Organ.THYROID,
            content="建议立即手术切除",
            confidence=0.88,
        )
        request = FusionRequest(agent_responses=[resp])

        result = await checker.check(request)

        assert result.severity == ConsistencySeverity.CRITICAL
        assert "guideline_contraindication" in result.detail

    @pytest.mark.asyncio
    async def test_p24_anticoagulant_risk_warning(self):
        """P2-4 场景 4：response 建议活检，guideline 提示抗凝/出血风险 → WARNING。

        临床背景：活检前需停用抗凝药，否则出血风险高。guideline 提示
        "抗凝治疗中" 是活检的相对禁忌。
        """
        chunks = [
            make_scored(
                "g_anticoag",
                content="患者长期口服华法林抗凝治疗，活检前需停药 5-7 天",
            )
        ]
        rag = FakeRAGService(chunks=chunks)
        checker = ConsistencyChecker(rag_service=rag)
        resp = make_response(
            organ=Organ.THYROID,
            content="TI-RADS 4 建议 FNA 活检",
            confidence=0.85,
        )
        request = FusionRequest(agent_responses=[resp])

        result = await checker.check(request)

        assert result.severity == ConsistencySeverity.WARNING
        assert "guideline_anticoagulant_risk" in result.detail

    @pytest.mark.asyncio
    async def test_p24_grade_escalation_warning(self):
        """P2-4 场景 5：response 自报 TR3，guideline 描述的同症状对应 TR5 → WARNING。

        临床背景：response 倾向良性，但 guideline 对同特征描述为恶性，
        分级差 2 → WARNING（提示 specialist 重新评估）。
        """
        chunks = [
            make_scored(
                "g_grade_escalation",
                content="微钙化 + 边缘不规则 + 形态竖立 → TR5 高度怀疑恶性",
            )
        ]
        rag = FakeRAGService(chunks=chunks)
        checker = ConsistencyChecker(rag_service=rag)
        resp = make_response(
            organ=Organ.THYROID,
            content="TI-RADS 3 边界清晰，考虑良性",
            confidence=0.80,
        )
        request = FusionRequest(agent_responses=[resp])

        result = await checker.check(request)

        # TR3 vs TR5 → diff=2 → WARNING
        assert result.severity == ConsistencySeverity.WARNING
        assert "guideline_grade_escalation" in result.detail

    @pytest.mark.asyncio
    async def test_p24_no_conflict_same_grade_same_recommendation(self):
        """P2-4 场景 6：response 与 guideline 建议一致 → INFO。

        验证 P2-4 不会误报。
        """
        chunks = [
            make_scored(
                "g_consistent",
                content="TI-RADS 3 建议随访观察，6-12 个月复查",
            )
        ]
        rag = FakeRAGService(chunks=chunks)
        checker = ConsistencyChecker(rag_service=rag)
        resp = make_response(
            organ=Organ.THYROID,
            content="TI-RADS 3 边界清晰，建议 6 个月随访观察",
            confidence=0.88,
        )
        request = FusionRequest(agent_responses=[resp])

        result = await checker.check(request)

        # 无冲突 → severity 应为 INFO
        assert result.severity == ConsistencySeverity.INFO
        assert "guideline_chunks_found" in result.detail  # 占位路径


# ======================================================================
# P2-6 修复：CRAG 真正重新调用 specialist LLM
# ======================================================================


class TestP26CRAGSpecialistReinvoke:
    """P2-6 修复：CRAG 应重新调用 specialist 而非仅 +0.2。"""

    @pytest.mark.asyncio
    async def test_p26_specialist_invoker_called_with_new_evidence(self):
        """P2-6 场景 1：注入 specialist_invoker → CRAG 真正调用 invoker。

        验证：CRAG 补充检索后应调用 invoker 重新生成 specialist 响应，
        而非仅在原 response 上 +0.2 confidence。
        """
        new_chunks = [
            make_chunk("crag_c1", content="补充证据 1"),
            make_chunk("crag_c2", content="补充证据 2"),
        ]

        # 模拟 specialist invoker：返回基于新证据的新 response
        async def fake_invoker(ctx: AgentContext) -> AgentResponse:
            return AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="TR4 基于新证据重新评估 → 建议随访",
                confidence=0.85,
                evidence_chunk_ids=["crag_c1", "crag_c2"],
                needs_review=False,
                disease_code="thyroid_tr4",
                used_evidence_ids=["crag_c1"],
            )

        handler = CRAGHandler(
            rag_service=FakeRAGService(
                chunks=[make_scored(c.chunk_id, content=c.content) for c in new_chunks]
            ),
            specialist_invoker=fake_invoker,
        )

        resp = make_response(
            organ=Organ.THYROID,
            content="TR4 建议活检",
            confidence=0.6,
            evidence=["old_c1"],
        )
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, reason="self_rag_failed"
        )
        request = FusionRequest(
            agent_responses=[resp],
            clinical_context={"user_query": "thyroid TR4 诊断"},
        )

        result = await handler.handle(resp, self_rag_result, request)

        # 验证真正重新调用了 specialist
        assert result.recovered is True
        assert result.specialist_reinvoked is True
        assert result.new_response is not None
        # 新 response 应来自 invoker（非原 response）
        assert result.new_response.content == "TR4 基于新证据重新评估 → 建议随访"
        assert "建议随访" in result.new_response.content
        assert result.new_response.confidence == 0.85

    @pytest.mark.asyncio
    async def test_p26_no_invoker_fallback_to_placeholder(self):
        """P2-6 场景 2：无 specialist_invoker → fallback 到 +0.2 占位实现。

        验证向后兼容：未注入 invoker 时仍能工作（仅 +0.2 confidence，
        标注 specialist_reinvoked=False）。
        """
        chunks = [make_scored("new_c1", content="new evidence")]
        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks)
            # 未注入 specialist_invoker
        )

        resp = make_response(
            organ=Organ.THYROID,
            content="TR4",
            confidence=0.6,
            evidence=["old_c1"],
        )
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, reason="self_rag_failed"
        )
        request = FusionRequest(agent_responses=[resp])

        result = await handler.handle(resp, self_rag_result, request)

        # 占位实现：+0.2 confidence
        assert result.recovered is True
        assert result.specialist_reinvoked is False
        assert result.new_response is not None
        assert result.new_response.confidence == pytest.approx(0.8, abs=0.001)

    @pytest.mark.asyncio
    async def test_p26_invoker_raises_fallback_to_placeholder(self):
        """P2-6 场景 3：invoker 抛异常 → fallback 到 +0.2 占位实现（不阻断）。

        验证：即使 invoker 异常，CRAG 仍能恢复（降级路径）。
        """
        chunks = [make_scored("new_c1", content="new evidence")]

        async def failing_invoker(ctx: AgentContext) -> AgentResponse:
            raise RuntimeError("LLM timeout")

        handler = CRAGHandler(
            rag_service=FakeRAGService(chunks=chunks),
            specialist_invoker=failing_invoker,
        )

        resp = make_response(
            organ=Organ.THYROID,
            content="TR4",
            confidence=0.6,
        )
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, reason="self_rag_failed"
        )
        request = FusionRequest(agent_responses=[resp])

        result = await handler.handle(resp, self_rag_result, request)

        # invoker 失败 → 降级到占位实现
        assert result.recovered is True
        assert result.specialist_reinvoked is False
        # confidence +0.2
        assert result.new_response is not None
        assert result.new_response.confidence == pytest.approx(0.8, abs=0.001)


# ======================================================================
# P2-8 修复：CRAG 经 DispatcherAgent 中央调度路由
# ======================================================================


class TestP28CRAGDispatcherRouting:
    """P2-8 修复：CRAG 应优先走 DispatcherAgent 中央调度。"""

    @pytest.mark.asyncio
    async def test_p28_dispatcher_routing_priority_over_knowledge_agent(self):
        """P2-8 场景 1：同时注入 dispatcher 和 knowledge_agent → 优先 dispatcher。

        验证路由优先级 dispatcher > knowledge_agent > rag_service。
        """
        new_chunks = [make_scored("disp_c1", content="via dispatcher")]

        class FakeDispatcher:
            def __init__(self, chunks):
                self._chunks = chunks
                self.route_crag_query_call_count = 0
                self.last_organ = None
                self.last_query = None
                self.last_top_k = None
                self.last_failure_category = None

            async def route_crag_query(
                self, organ, query, top_k=20, failure_category="unknown"
            ):
                self.route_crag_query_call_count += 1
                self.last_organ = organ
                self.last_query = query
                self.last_top_k = top_k
                self.last_failure_category = failure_category
                return list(self._chunks)

        class FakeKnowledgeAgent:
            def __init__(self):
                self.query_call_count = 0

            async def query(self, query, organ=None, top_k=10):
                self.query_call_count += 1
                return []

        dispatcher = FakeDispatcher(new_chunks)
        knowledge_agent = FakeKnowledgeAgent()
        handler = CRAGHandler(
            rag_service=FakeRAGService(),  # 兜底 RAG（不应被调用）
            knowledge_agent=knowledge_agent,  # 不应被调用
            dispatcher_agent=dispatcher,  # 优先
        )

        resp = make_response(organ=Organ.THYROID, content="TR4", confidence=0.6)
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, evidence_sufficiency=1.0
        )
        request = FusionRequest(
            agent_responses=[resp],
            clinical_context={"user_query": "thyroid TR4"},
        )

        result = await handler.handle(resp, self_rag_result, request)

        # 验证 dispatcher 优先被调用
        assert dispatcher.route_crag_query_call_count == 1
        assert knowledge_agent.query_call_count == 0
        # 验证 via_dispatcher=True
        assert result.via_dispatcher is True
        # 验证参数正确传递
        assert dispatcher.last_organ == Organ.THYROID
        assert dispatcher.last_top_k == 20  # CRAG 默认 top_k
        assert "thyroid" in (dispatcher.last_query or "").lower()
        assert "TR4" in dispatcher.last_query

    @pytest.mark.asyncio
    async def test_p28_dispatcher_failure_fallback_to_knowledge_agent(self):
        """P2-8 场景 2：dispatcher 抛异常 → fallback 到 knowledge_agent。

        验证：dispatcher 异常时不阻断 CRAG 流程，降级到下一级。
        """
        new_chunks = [make_scored("ka_c1", content="via knowledge agent")]

        class FailingDispatcher:
            async def route_crag_query(self, **kwargs):
                raise RuntimeError("dispatcher down")

        class FakeKnowledgeAgent:
            def __init__(self, chunks):
                self._chunks = chunks
                self.query_call_count = 0

            async def query(self, query, organ=None, top_k=10):
                self.query_call_count += 1
                return list(self._chunks)

        knowledge_agent = FakeKnowledgeAgent(new_chunks)
        handler = CRAGHandler(
            rag_service=FakeRAGService(),
            knowledge_agent=knowledge_agent,
            dispatcher_agent=FailingDispatcher(),
        )

        resp = make_response(organ=Organ.THYROID, content="TR4", confidence=0.6)
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, evidence_sufficiency=1.0
        )
        request = FusionRequest(agent_responses=[resp])

        result = await handler.handle(resp, self_rag_result, request)

        # dispatcher 失败 → fallback 到 knowledge_agent
        assert knowledge_agent.query_call_count == 1
        assert result.recovered is True
        assert result.via_dispatcher is False  # 实际走 knowledge_agent 路径

    @pytest.mark.asyncio
    async def test_p28_no_dispatcher_uses_knowledge_agent(self):
        """P2-8 场景 3：无 dispatcher 但有 knowledge_agent → 走 knowledge_agent。

        验证：保持向后兼容。
        """
        new_chunks = [make_scored("ka_c1", content="via knowledge agent")]

        class FakeKnowledgeAgent:
            def __init__(self, chunks):
                self._chunks = chunks
                self.query_call_count = 0

            async def query(self, query, organ=None, top_k=10):
                self.query_call_count += 1
                return list(self._chunks)

        knowledge_agent = FakeKnowledgeAgent(new_chunks)
        handler = CRAGHandler(
            rag_service=FakeRAGService(),
            knowledge_agent=knowledge_agent,
            # 未注入 dispatcher
        )

        resp = make_response(organ=Organ.THYROID, content="TR4", confidence=0.6)
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, evidence_sufficiency=1.0
        )
        request = FusionRequest(agent_responses=[resp])

        result = await handler.handle(resp, self_rag_result, request)

        assert knowledge_agent.query_call_count == 1
        assert result.via_dispatcher is False
        assert result.recovered is True


# ======================================================================
# P2-9 修复：完整 E2E 流程（CRAG dispatcher + specialist reinvoke）
# ======================================================================


class TestP29EndToEndCRAGFlow:
    """P2-9 E2E：CRAG 经 Dispatcher 路由 + 真正重生成 specialist。"""

    @pytest.mark.asyncio
    async def test_p29_full_flow_dispatcher_and_reinvoke(self):
        """P2-9 场景 1：完整 E2E 流程（dispatcher + specialist reinvoke）。

        链路：CRAG → Dispatcher.route_crag_query → KnowledgeAgent → 新证据
              → SpecialistInvoker → 新 response。
        """
        # 1) Dispatcher 内部委派给 KnowledgeAgent
        new_chunks = [
            make_chunk("e2e_c1", content="补充证据 1"),
            make_chunk("e2e_c2", content="补充证据 2"),
        ]

        class KnowledgeAgentMock:
            def __init__(self, chunks):
                self._chunks = chunks
                self.query_call_count = 0

            async def query(self, query, organ=None, top_k=10):
                self.query_call_count += 1
                return list(self._chunks)

            async def health_check(self):
                return True

        knowledge_agent = KnowledgeAgentMock(new_chunks)

        class DispatcherMock:
            def __init__(self, knowledge_agent):
                self._ka = knowledge_agent
                self.route_crag_query_call_count = 0
                self.last_failure_category = None

            async def route_crag_query(
                self, organ, query, top_k=20, failure_category="unknown"
            ):
                self.route_crag_query_call_count += 1
                self.last_failure_category = failure_category
                return await self._ka.query(query, organ=organ, top_k=top_k)

        # 2) Specialist invoker：基于新证据重新生成
        async def specialist_invoker(ctx: AgentContext) -> AgentResponse:
            return AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="TI-RADS 4 基于新证据：建议 6 个月短期随访",
                confidence=0.82,
                evidence_chunk_ids=["e2e_c1", "e2e_c2"],
                needs_review=False,
                disease_code="thyroid_tr4",
                used_evidence_ids=["e2e_c1"],
            )

        dispatcher = DispatcherMock(knowledge_agent)
        handler = CRAGHandler(
            dispatcher_agent=dispatcher,
            specialist_invoker=specialist_invoker,
        )

        resp = make_response(
            organ=Organ.THYROID,
            content="TI-RADS 4 建议活检",
            confidence=0.6,
            evidence=["old_c1"],
        )
        # 失败原因：evidence_sufficiency 低 → evidence_insufficient
        self_rag_result = SelfRAGEvalResult(
            passed=False, score=2.0, evidence_sufficiency=1.0
        )
        request = FusionRequest(
            agent_responses=[resp],
            clinical_context={"user_query": "thyroid TR4 诊断建议"},
        )

        result = await handler.handle(resp, self_rag_result, request)

        # 验证 E2E 链路
        assert result.recovered is True
        assert result.via_dispatcher is True
        assert result.specialist_reinvoked is True
        # dispatcher 路径
        assert dispatcher.route_crag_query_call_count == 1
        assert knowledge_agent.query_call_count == 1
        # failure_category 正确传递
        assert dispatcher.last_failure_category == "evidence_insufficient"
        # 新 response 来自 invoker
        assert result.new_response is not None
        assert "新证据" in result.new_response.content
        assert "随访" in result.new_response.content
        assert result.new_response.confidence == 0.82
        # 新 evidence 合并
        assert "e2e_c1" in result.new_response.evidence_chunk_ids
        assert "e2e_c2" in result.new_response.evidence_chunk_ids
        # 旧 evidence 保留
        assert "old_c1" in result.new_response.evidence_chunk_ids


# ======================================================================
# 单元测试：语义抽取辅助函数
# ======================================================================


class TestP24SemanticExtractors:
    """P2-4 修复：单元测试 _extract_recommendation / _extract_grade_label / 等。"""

    def test_extract_recommendation_biopsy(self):
        """抽取活检建议。"""
        from app.fusion.consistency import _extract_recommendation
        assert _extract_recommendation("建议 FNA 活检") == "biopsy"
        assert _extract_recommendation("FNAB is recommended") == "biopsy"

    def test_extract_recommendation_followup(self):
        """抽取随访建议。"""
        from app.fusion.consistency import _extract_recommendation
        assert _extract_recommendation("建议 6 个月随访") == "follow_up"
        assert _extract_recommendation("follow-up in 3 months") == "follow_up"

    def test_extract_recommendation_priority(self):
        """侵入性建议优先（biopsy > follow_up）。"""
        from app.fusion.consistency import _extract_recommendation
        # 同时提到活检和随访时，应优先返回 biopsy
        text = "如随访期间进展则建议活检"
        assert _extract_recommendation(text) == "biopsy"

    def test_extract_grade_label_ti_rads(self):
        """抽取 TI-RADS 分级。"""
        from app.fusion.consistency import _extract_grade_label
        assert _extract_grade_label("TI-RADS 4 边界不清") == "ti-rads4"
        assert _extract_grade_label("TR5 高度怀疑恶性") == "tr5"

    def test_extract_grade_label_li_rads(self):
        """抽取 LI-RADS 分级。"""
        from app.fusion.consistency import _extract_grade_label
        assert _extract_grade_label("LI-RADS 4 病灶") == "li-rads4"
        assert _extract_grade_label("LR-3 中度可疑") == "lr3"

    def test_extract_grade_label_lvef(self):
        """抽取 LVEF 数值。"""
        from app.fusion.consistency import _extract_grade_label
        assert _extract_grade_label("LVEF 35%") == "lvef35"
        assert _extract_grade_label("EF 50%") == "ef50"

    def test_extract_negation_window_detects(self):
        """检测 keyword 附近的禁忌表述。"""
        from app.fusion.consistency import _extract_negation_window
        # "活检" 30 字符内有 "禁忌"
        assert _extract_negation_window("手术为禁忌，活检也不建议", "活检") is True
        assert _extract_negation_window("活检 is contraindicated", "活检") is True

    def test_extract_negation_window_not_detected_far_away(self):
        """禁忌表述远离 keyword → 不应触发。"""
        from app.fusion.consistency import _extract_negation_window
        # 禁忌在文本开头，keyword 在末尾（距离 > 30 字符）
        text = "禁忌" + "x" * 100 + "活检"
        assert _extract_negation_window(text, "活检") is False

    def test_has_anticoagulant_risk(self):
        """检测抗凝/出血风险关键词。"""
        from app.fusion.consistency import _has_anticoagulant_risk
        assert _has_anticoagulant_risk("患者长期口服华法林") is True
        assert _has_anticoagulant_risk("bleeding risk noted") is True
        assert _has_anticoagulant_risk("常规随访无异常") is False
