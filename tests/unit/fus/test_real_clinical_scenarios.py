"""Real clinical scenario tests for FUS track (P1-5 修复).

设计目的：
    弥补基础 mock 测试的"虚假信号"问题，使用真实临床场景构造测试用例，
    验证修复后的 FUS 组件在真实数据下的行为正确性。

参考：``CODE_REVIEW.md`` §10.1 — 真实临床场景测试用例构造建议。

覆盖场景：
    1. 跨器官关联规则 3 条关键规则（肝心综合征 / 甲亢性心脏病 / 甲减心包积液）；
    2. 真实 EVIDENCE_CONFLICT 检出（共享证据 + 互斥建议 / 分级冲突）；
    3. Self-RAG 重试机制（LLM 首次低分 → 第二次高分通过 / 全部失败 → retries_exhausted）；
    4. CRAG 经 KnowledgeAgent 路由（享受缓存）+ BM25 兜底开启；
    5. needs_review 透传（specialist 标 True → fusion 保留）；
    6. 多器官复合真实场景（甲状腺癌 + 肝转移 + 心脏累及）。

设计原则：
    - **场景真实性**：所有 content / evidence 字符串均模拟真实临床报告风格；
    - **多样性**：每个场景至少 3 个变体（不同器官 / 不同严重度 / 不同证据组合）；
    - **可断言**：每个测试都验证具体 severity / conflict_type / call_count，
      避免基础 mock 测试的"通过即满足"虚假信号。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_fus]

from app.fusion.conflict import ConflictDetector
from app.fusion.consistency import ConsistencyChecker
from app.fusion.crag import CRAGHandler
from app.fusion.protocols import (
    ConflictType,
    ConsistencySeverity,
    FusionRequest,
)
from app.fusion.self_rag import SelfRAGEvaluator
from app.inference.protocols import (
    InferenceRequest,
    InferenceResponse,
    TokenUsage,
)
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeInferenceEngine,
    FakeRAGService,
    make_chunk,
    make_response,
)

# ======================================================================
# 真实临床场景 1：跨器官关联规则（3 条关键规则）
# ======================================================================


class TestRealClinicalCrossOrganRules:
    """真实临床场景下的跨器官关联规则测试。

    覆盖审查报告 P1-4 修复后的 3 条关键规则：
    - 肝心综合征（liver HCC advanced + cardiac LVEF<40%）
    - 甲亢性心脏病（thyroid hyperthyroidism + cardiac arrhythmia）
    - 甲减心包积液（thyroid hypothyroidism + cardiac pericardial effusion）
    """

    @pytest.mark.asyncio
    async def test_real_clinical_hepato_cardiac_syndrome(self):
        """真实场景 1.1：肝硬化 + LVEF 35% → CRITICAL 肝-心综合征。

        临床背景：肝硬化常合并循环高动力状态 → 心脏负荷增加 →
        LVEF 下降。漏诊会错过治疗窗口。

        注：UDT 编码格式（02-核心流程与知识库.md §4 权威定义）：
        肝硬化 = ``liver_cirrhosis``（含子类如 ``liver_post_hepatitic_cirrhosis``）。
        """
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="liver_specialist",
                organ=Organ.LIVER,
                content="肝硬化，门静脉高压，脾大，腹水",
                confidence=0.92,
                disease_code="liver_cirrhosis",
                evidence=["liver_chunk_1", "liver_chunk_2"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="LVEF 35%，室壁运动异常，射血分数降低",
                confidence=0.88,
                disease_code="cardiac_right_heart_failure",
                evidence=["cardiac_chunk_1"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        result = await checker.check(request)

        assert result.is_consistent is False, "肝-心综合征应触发 CRITICAL"
        assert result.severity == ConsistencySeverity.CRITICAL
        assert ("liver", "cardiac") in result.inconsistent_organ_pairs
        # 详情应包含规则 ID
        assert "heart_liver_syndrome" in result.detail

    @pytest.mark.asyncio
    async def test_real_clinical_hepato_cardiac_syndrome_via_keywords(self):
        """真实场景 1.2：通过关键词匹配（无 disease_code）也能触发肝-心综合征。

        验证 UDT 编码缺失时的关键词 fallback 路径（与设计文档底线要求
        "UDT 优先"一致，关键词仅作 fallback）。
        """
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="liver_specialist",
                organ=Organ.LIVER,
                content="肝硬化失代偿期，门静脉高压，腹水",
                confidence=0.90,
                # 注意：未传 disease_code
                evidence=["liver_chunk_1"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="LVEF 30%，reduced ejection fraction",
                confidence=0.85,
                evidence=["cardiac_chunk_1"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        result = await checker.check(request)

        assert result.severity == ConsistencySeverity.CRITICAL
        assert "heart_liver_syndrome" in result.detail

    @pytest.mark.asyncio
    async def test_real_clinical_hyperthyroid_heart_disease(self):
        """真实场景 1.3：甲亢 + 房颤 → WARNING 甲亢性心脏病。

        临床背景：甲亢导致心率增快、心律失常，常见房颤，需 β 受体阻滞剂
        + 抗甲亢治疗。
        """
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="Graves 病，TSH 降低，T3/T4 升高，确诊甲亢",
                confidence=0.93,
                disease_code="thyroid_hyperthyroidism",  # UDT 编码
                evidence=["thyroid_chunk_1", "thyroid_chunk_2"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="心电图示心房颤动，atrial fibrillation，心动过速",
                confidence=0.89,
                disease_code="cardiac_arrhythmia",  # UDT 编码
                evidence=["cardiac_chunk_1"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        result = await checker.check(request)

        assert result.severity == ConsistencySeverity.WARNING
        assert "hyperthyroid_heart_disease" in result.detail
        # WARNING 不破坏 is_consistent
        assert result.is_consistent is True

    @pytest.mark.asyncio
    async def test_real_clinical_hypothyroid_pericardial_effusion(self):
        """真实场景 1.4：甲减 + 心包积液 → WARNING 甲减心包积液。

        临床背景：严重甲减可致黏液性水肿，累及心包形成心包积液。
        """
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="桥本甲状腺炎，TSH 升高，T3/T4 降低，确诊甲减",
                confidence=0.91,
                disease_code="thyroid_hypothyroidism",  # UDT 编码
                evidence=["thyroid_chunk_1"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="心包积液（中量），pericardial effusion",
                confidence=0.87,
                disease_code="cardiac_pericardial_effusion",  # UDT 编码
                evidence=["cardiac_chunk_1", "cardiac_chunk_2"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        result = await checker.check(request)

        assert result.severity == ConsistencySeverity.WARNING
        assert "hypothyroid_pericardial_effusion" in result.detail

    @pytest.mark.asyncio
    async def test_real_clinical_no_cross_organ_issue(self):
        """真实场景 1.5：良性甲状腺结节（无合并症）→ 不触发跨器官规则。

        验证规则不会误报。
        """
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="TR2 良性结节，建议年度随访",
                confidence=0.90,
                evidence=["thyroid_chunk_1"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="LVEF 65%，心脏结构及功能未见异常",
                confidence=0.88,
                evidence=["cardiac_chunk_1"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        result = await checker.check(request)

        # 不应触发任何跨器官规则
        assert "heart_liver_syndrome" not in result.detail
        assert "hyperthyroid_heart_disease" not in result.detail
        assert "hypothyroid_pericardial_effusion" not in result.detail
        assert result.severity == ConsistencySeverity.INFO


# ======================================================================
# 真实临床场景 2：EVIDENCE_CONFLICT 真实检测
# ======================================================================


class TestRealClinicalEvidenceConflict:
    """真实临床场景下的 EVIDENCE_CONFLICT 检测测试。

    覆盖审查报告 P0-3 修复后的 3 类触发条件：
    1. 共享 evidence + 互斥建议（活检 vs 随访）
    2. 共享 evidence + 同器官分级冲突
    3. 共享 evidence + 否定关系冲突
    """

    def test_real_clinical_shared_evidence_contradicting_recommendations(self):
        """真实场景 2.1：两位 specialist 共享同一 chunk_1，结论互斥。

        临床背景：thyroid_a 引用 chunk_malignant 建议活检（恶性），
        thyroid_b 引用同一 chunk 建议随访（良性）→ EVIDENCE_CONFLICT。
        """
        detector = ConflictDetector()
        responses = [
            make_response(
                agent_name="thyroid_a",
                organ=Organ.THYROID,
                content="TI-RADS 5 高度怀疑恶性，建议 FNA 活检进一步确诊",
                confidence=0.92,
                evidence=["chunk_malignant_001"],
            ),
            make_response(
                agent_name="thyroid_b",
                organ=Organ.THYROID,
                content="TI-RADS 3 良性可能性大，建议 6 个月随访观察",
                confidence=0.85,
                evidence=["chunk_malignant_001"],  # 共享同一 evidence
            ),
        ]

        report = detector.detect(responses)

        assert report.has_conflict is True
        # 至少触发 EVIDENCE_CONFLICT（也可能同时触发 RECOMMENDATION_CONFLICT）
        assert ConflictType.EVIDENCE_CONFLICT in report.conflict_types
        # 两位 specialist 都应被报告
        conflicting_agents = {r.agent_name for r in report.conflicting_responses}
        assert "thyroid_a" in conflicting_agents
        assert "thyroid_b" in conflicting_agents

    def test_real_clinical_shared_evidence_same_organ_different_grades(self):
        """真实场景 2.2：同器官共享 evidence 但分级不同 → EVIDENCE_CONFLICT。

        临床背景：两位 liver specialist 引用同一 chunk，对同一肝脏占位
        给出 LR-3 vs LR-5 的不同分级。
        """
        detector = ConflictDetector()
        responses = [
            make_response(
                agent_name="liver_a",
                organ=Organ.LIVER,
                content="LI-RADS 3 中度可疑，3 个月短期随访",
                confidence=0.82,
                evidence=["liver_chunk_shared"],
            ),
            make_response(
                agent_name="liver_b",
                organ=Organ.LIVER,
                content="LR-5 高度怀疑恶性，建议立即增强 MRI",
                confidence=0.91,
                evidence=["liver_chunk_shared"],  # 共享 evidence
            ),
        ]

        report = detector.detect(responses)

        assert report.has_conflict is True
        # 同时触发 GRADING_CONFLICT（同器官不同分级）
        assert ConflictType.GRADING_CONFLICT in report.conflict_types
        # 也触发 EVIDENCE_CONFLICT（共享 evidence）
        assert ConflictType.EVIDENCE_CONFLICT in report.conflict_types

    def test_real_clinical_evidence_conflict_does_not_trigger_without_sharing(self):
        """真实场景 2.3：不共享 evidence 的不同 specialist 不触发 EVIDENCE_CONFLICT。

        验证 P0-3 修复的保守策略（避免误报）。
        """
        detector = ConflictDetector()
        responses = [
            make_response(
                agent_name="thyroid_a",
                organ=Organ.THYROID,
                content="TR5 高度怀疑恶性，建议活检",
                confidence=0.92,
                evidence=["chunk_a"],  # 不同的 evidence
            ),
            make_response(
                agent_name="thyroid_b",
                organ=Organ.THYROID,
                content="TR3 良性可能性大，建议随访",
                confidence=0.85,
                evidence=["chunk_b"],  # 不同的 evidence
            ),
        ]

        report = detector.detect(responses)

        # 不共享 evidence → 不应触发 EVIDENCE_CONFLICT
        assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types
        # 但会触发 GRADING_CONFLICT 和 RECOMMENDATION_CONFLICT
        assert ConflictType.GRADING_CONFLICT in report.conflict_types


# ======================================================================
# 真实临床场景 3：Self-RAG 重试机制
# ======================================================================


class TestRealClinicalSelfRAGRetry:
    """真实临床场景下的 Self-RAG 重试机制测试。

    覆盖审查报告 P1-1 修复后的重试行为：
    - 首次低分 → 重试 → 第二次高分 → 通过；
    - 持续低分 → 重试耗尽 → 标记 retries_exhausted；
    - 重试成功立即返回（不浪费重试次数）。
    """

    @pytest.mark.asyncio
    async def test_real_clinical_self_rag_retry_then_pass(self):
        """真实场景 3.1：LLM 首次评估低分 → 第二次高分 → 通过。

        临床背景：LLM 首次可能因格式 / 解读偏差给出低分，重试给 LLM
        第二次机会可显著提升通过率。
        """
        # 构造一个动态 LLM：第一次返回低分，第二次返回满分
        class FlakeyLLM:
            def __init__(self):
                self.call_count = 0

            async def generate(self, request: InferenceRequest) -> InferenceResponse:
                self.call_count += 1
                if self.call_count == 1:
                    # 第一次：低分
                    text = (
                        "evidence_sufficiency: 2\n"
                        "evidence_consistency: 3\n"
                        "feature_completeness: 2\n"
                        "missing_features: 钙化, 边界"
                    )
                else:
                    # 第二次：满分
                    text = (
                        "evidence_sufficiency: 5\n"
                        "evidence_consistency: 5\n"
                        "feature_completeness: 4\n"
                        "missing_features: 无"
                    )
                return InferenceResponse(
                    text=text,
                    model="flakey-llm",
                    usage=TokenUsage(prompt_tokens=200, completion_tokens=50, total_tokens=250),
                    finish_reason="stop",
                    request_id=request.request_id,
                    session_id=request.session_id,
                    latency_ms=20.0,
                )

            async def health_check(self) -> bool:
                return True

            async def get_model_info(self):
                from app.inference.protocols import ModelInfo
                return ModelInfo(model_name="flakey-llm", model_version="v1")

            async def close(self) -> None:
                pass

        llm = FlakeyLLM()
        evaluator = SelfRAGEvaluator(inference_engine=llm, max_retries=2)
        resp = make_response(
            organ=Organ.THYROID,
            content="TR4 边界不清晰，内见钙化，建议活检",
            confidence=0.85,
            evidence=["c1", "c2", "c3"],
        )

        result = await evaluator.evaluate(resp)

        # 第二次高分应通过
        assert result.passed is True
        # 验证至少调用了 2 次
        assert llm.call_count == 2
        # total = min(5, 5, 4) = 4.0
        assert result.score == 4.0
        assert result.evidence_sufficiency == 5.0
        assert result.evidence_consistency == 5.0
        assert result.feature_completeness == 4.0
        # reason 不应包含 retries_exhausted（最终通过）
        assert "retries_exhausted" not in (result.reason or "")
        # reason 应包含 retry#2 标记
        assert "retry#2" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_real_clinical_self_rag_retry_exhausted(self):
        """真实场景 3.2：LLM 持续低分 → 重试耗尽 → 标记 retries_exhausted。

        临床背景：诊断结论本身证据不足时，无论重试多少次都无法通过，
        此时应明确告知 CRAG 失败原因。
        """
        # 持续返回低分的 LLM
        class AlwaysFailingLLM(FakeInferenceEngine):
            def __init__(self):
                super().__init__(
                    text=(
                        "evidence_sufficiency: 1\n"
                        "evidence_consistency: 1\n"
                        "feature_completeness: 1\n"
                        "missing_features: 全部缺失"
                    )
                )

        llm = AlwaysFailingLLM()
        evaluator = SelfRAGEvaluator(inference_engine=llm, max_retries=2)
        resp = make_response(
            organ=Organ.LIVER,
            content="肝脏占位性质待定",  # 模糊诊断
            confidence=0.3,
            evidence=[],  # 无证据
        )

        result = await evaluator.evaluate(resp)

        # 持续低分 → 失败
        assert result.passed is False
        # 调用了 3 次（1 首次 + 2 重试）
        assert llm.generate_call_count == 3
        # reason 应包含 retries_exhausted
        assert "retries_exhausted" in (result.reason or "")

    @pytest.mark.asyncio
    async def test_real_clinical_self_rag_pass_first_attempt_no_retry(self):
        """真实场景 3.3：LLM 首次即高分 → 不重试，立即返回。

        验证重试机制不浪费 LLM 资源。
        """
        llm = FakeInferenceEngine()  # 默认返回满分
        evaluator = SelfRAGEvaluator(inference_engine=llm, max_retries=2)
        resp = make_response(
            organ=Organ.THYROID,
            content="TR4 边界不清晰，建议活检",
            confidence=0.9,
            evidence=["c1", "c2", "c3", "c4", "c5"],
        )

        result = await evaluator.evaluate(resp)

        assert result.passed is True
        # 首次通过 → 仅调用 1 次
        assert llm.generate_call_count == 1


# ======================================================================
# 真实临床场景 4：CRAG 经 KnowledgeAgent 路由
# ======================================================================


class TestRealClinicalCRAGRouting:
    """真实临床场景下的 CRAG 路由测试。

    覆盖审查报告 P1-2 修复后的路由行为：
    - 注入 knowledge_agent → 调用 knowledge_agent.query（享受缓存）；
    - 无 knowledge_agent → fallback RAG Service，且 use_bm25_fallback=True。
    """

    @pytest.mark.asyncio
    async def test_real_clinical_crag_via_knowledge_agent(self):
        """真实场景 4.1：CRAG 经 KnowledgeAgent 路由 → knowledge_agent.query 被调用。

        临床背景：生产环境中 CRAG 应经知识查询智能体路由，享受两层缓存
        （Redis + LRU）+ 负载均衡，而非直连 RAG Service。
        """
        # 模拟 KnowledgeAgent
        class FakeKnowledgeAgent:
            def __init__(self, chunks: list[KnowledgeChunk]):
                self._chunks = chunks
                self.query_call_count = 0
                self.last_query = None
                self.last_organ = None
                self.last_top_k = None

            async def query(self, query, organ=None, top_k=10) -> list[KnowledgeChunk]:
                self.query_call_count += 1
                self.last_query = query
                self.last_organ = organ
                self.last_top_k = top_k
                return self._chunks

            async def health_check(self) -> bool:
                return True

        # 注入 3 条新证据
        new_chunks = [
            make_chunk("crag_chunk_1", organ="thyroid", content="甲状腺活检指南"),
            make_chunk("crag_chunk_2", organ="thyroid", content="TR4 临床路径"),
            make_chunk("crag_chunk_3", organ="thyroid", content="FNA 适应症"),
        ]
        knowledge_agent = FakeKnowledgeAgent(new_chunks)
        handler = CRAGHandler(knowledge_agent=knowledge_agent)

        resp = make_response(
            organ=Organ.THYROID,
            content="TR4",
            confidence=0.7,
            evidence=["old_chunk_1"],
        )
        self_rag_result = type("R", (), {
            "passed": False,
            "score": 2.0,
            "evidence_sufficiency": 1.0,
            "evidence_consistency": 3.0,
            "feature_completeness": 3.0,
            "reason": "self_rag_failed",
        })()
        request = FusionRequest(
            agent_responses=[resp],
            clinical_context={"user_query": "甲状腺 TR4 诊断建议"},
        )

        result = await handler.handle(resp, self_rag_result, request)

        # 验证走 knowledge_agent 路由
        assert knowledge_agent.query_call_count == 1
        assert knowledge_agent.last_organ == Organ.THYROID
        # 验证恢复成功
        assert result.recovered is True
        assert result.new_response is not None
        # 合并证据
        assert "old_chunk_1" in result.new_response.evidence_chunk_ids
        assert "crag_chunk_1" in result.new_response.evidence_chunk_ids

    @pytest.mark.asyncio
    async def test_real_clinical_crag_bm25_fallback_enabled(self):
        """真实场景 4.2：CRAG fallback 路径开启 BM25 终极兜底。

        临床背景：dense 检索失败时，BM25 关键词检索作为终极兜底，
        与设计文档 01-系统架构设计.md 嵌入降级链路一致。
        """
        rag_service = FakeRAGService()
        handler = CRAGHandler(rag_service=rag_service)  # 无 knowledge_agent

        resp = make_response(organ=Organ.LIVER, content="LR-4", confidence=0.7)
        self_rag_result = type("R", (), {
            "passed": False, "score": 0.0,
            "evidence_sufficiency": 0.0, "evidence_consistency": 0.0,
            "feature_completeness": 0.0, "reason": "self_rag_failed",
        })()
        request = FusionRequest(agent_responses=[resp])

        await handler.handle(resp, self_rag_result, request)

        # 验证 RAG Service 被调用，且 use_bm25_fallback=True
        assert rag_service.retrieve_call_count == 1
        assert rag_service.last_request is not None
        assert rag_service.last_request.use_bm25_fallback is True, (
            "P1-3 修复：CRAG 应开启 BM25 终极兜底"
        )


# ======================================================================
# 真实临床场景 5：needs_review 透传
# ======================================================================


class TestRealClinicalNeedsReviewPropagation:
    """真实临床场景下的 needs_review 透传测试。

    覆盖审查报告 P0-1 修复后的透传契约：
    - specialist 标 needs_review=True → fusion 保留 True；
    - specialist 标 needs_review=False → fusion 不擅自置 True。
    """

    @pytest.mark.asyncio
    async def test_real_clinical_specialist_flagged_needs_review(self):
        """真实场景 5.1：specialist 标 needs_review=True → fusion 透传。

        临床背景：specialist 自身判定证据不足 / 边界 / 罕见病时，应主动
        标记 needs_review=True，融合层不能覆盖该标记。

        注：needs_review 任一来源触发即保持 True；review_reason 以首个
        触发的原因（self_rag_failed）为准 — specialist_flagged 仅在
        无其他原因时设置（service.py:207-209 的 not result.review_reason 判断）。
        """
        from app.fusion.service import FusionEngineImpl

        # 构造一个简单 FusionEngine（无需外部依赖）
        # 使用 confidence 高于 heuristic_threshold=0.7，让 self_rag 通过，
        # 避免 self_rag_failed 抢占 review_reason。
        self_rag = SelfRAGEvaluator(inference_engine=None)
        crag = CRAGHandler(rag_service=FakeRAGService())
        conflict_det = ConflictDetector()
        consistency_check = ConsistencyChecker(rag_service=None)
        engine = FusionEngineImpl(
            conflict_detector=conflict_det,
            consistency_checker=consistency_check,
            self_rag_evaluator=self_rag,
            crag_handler=crag,
        )

        # specialist 主动标 needs_review=True（罕见病 / 证据不足）
        resp = make_response(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content="甲状腺占位，性质待定，建议上级医院会诊",
            confidence=0.85,  # 高于 heuristic_threshold，self_rag 通过
            needs_review=True,  # ← specialist 主动标记
            disease_code="thyroid_undiagnosed",
            evidence=["chunk_1", "chunk_2", "chunk_3", "chunk_4"],  # 4 条证据
        )
        request = FusionRequest(agent_responses=[resp])

        result = await engine.fuse(request)

        # 验证 needs_review 透传
        assert result.needs_review is True, (
            "specialist 标记 needs_review=True 后，融合结果必须保留 True"
        )
        # review_reason 应该是 specialist_flagged（self_rag 通过时）
        assert result.review_reason == "specialist_flagged", (
            f"self_rag 通过时，specialist_flagged 应该是唯一原因，得到 {result.review_reason}"
        )
        assert result.final_response is not None
        assert result.final_response.needs_review is True

    @pytest.mark.asyncio
    async def test_real_clinical_specialist_not_flagged_no_override(self):
        """真实场景 5.2：specialist 未标记 → fusion 不擅自置 needs_review。

        验证 fusion 不会对所有结果都置 needs_review（避免误报）。
        """
        from app.fusion.service import FusionEngineImpl

        self_rag = SelfRAGEvaluator(inference_engine=None)
        crag = CRAGHandler(rag_service=FakeRAGService())
        conflict_det = ConflictDetector()
        consistency_check = ConsistencyChecker(rag_service=None)
        engine = FusionEngineImpl(
            conflict_detector=conflict_det,
            consistency_checker=consistency_check,
            self_rag_evaluator=self_rag,
            crag_handler=crag,
        )

        # 高置信度、明确的诊断，specialist 未标 needs_review
        resp = make_response(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content="TR2 良性结节，边界清晰",
            confidence=0.95,
            needs_review=False,
            evidence=["chunk_1", "chunk_2", "chunk_3"],
        )
        request = FusionRequest(agent_responses=[resp])

        result = await engine.fuse(request)

        # 不应被 fusion 层擅自置 needs_review
        assert result.needs_review is False
        assert result.final_response is not None
        assert result.final_response.needs_review is False


# ======================================================================
# 真实临床场景 6：多器官复合真实场景
# ======================================================================


class TestRealClinicalMultiOrganComposite:
    """真实临床场景下的多器官复合测试。

    模拟真实临床多器官联合检查：甲状腺 + 肝脏 + 心脏 同时评估。
    """

    @pytest.mark.asyncio
    async def test_real_clinical_thyroid_cancer_with_liver_metastasis(self):
        """真实场景 6.1：甲状腺癌 + 肝转移 + 心脏累及（多器官复合）。

        临床注意：肝转移瘤应使用 LI-RADS 类别 LR-M（明确恶性）或
        描述"转移性占位"，而非 LR-5（LR-5 专指原发性 HCC）— 避免
        误触发肝-心综合征规则。
        """
        detector = ConflictDetector()
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="TI-RADS 5，甲状腺乳头状癌可能大",
                confidence=0.94,
                disease_code="thyroid_malignant",  # UDT 编码（甲状腺癌）
                evidence=["thy_chunk_1", "thy_chunk_2"],
            ),
            make_response(
                agent_name="liver_specialist",
                organ=Organ.LIVER,
                content="肝内多发低回声占位，LR-M，明确转移性病变",
                confidence=0.89,
                disease_code="liver_metastasis",  # UDT 编码（肝转移）
                evidence=["liv_chunk_1", "liv_chunk_2"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="LVEF 38%，心包少量积液",
                confidence=0.82,
                disease_code="cardiac_hf_reduced",  # UDT 编码（心衰）
                evidence=["car_chunk_1"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        # 冲突检测
        conflict_report = detector.detect(responses)
        # 一致性检查
        consistency_result = await checker.check(request)

        # 注：LR-M 不应触发肝心综合征（HCC-ADVANCED 才触发）
        assert "heart_liver_syndrome" not in consistency_result.detail
        # 但应触发甲减心包积液？没有 hypothyroidism → 不触发
        assert "hypothyroid_pericardial_effusion" not in consistency_result.detail
        # LVEF 38% < 40% 是单器官的 cardiac issue，未涉及 HCC
        # → severity 应为 INFO（无跨器官规则匹配）
        assert consistency_result.severity == ConsistencySeverity.INFO, (
            f"未匹配任何跨器官规则时，severity 应为 INFO，得到 {consistency_result.severity}: {consistency_result.detail}"
        )

    @pytest.mark.asyncio
    async def test_real_clinical_complex_scenario_with_conflicts(self):
        """真实场景 6.2：多器官复合 + 实际冲突 + 跨器官规则。

        场景：甲亢（thyroid）+ 甲亢性心脏病（cardiac）+ 肝脏无关异常。
        预期：跨器官规则触发 WARNING。
        """
        checker = ConsistencyChecker(rag_service=None)
        responses = [
            make_response(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="Graves 病，甲亢确诊",
                confidence=0.95,
                disease_code="UDT-THYROID-HYPERTHYROIDISM",
                evidence=["thy_chunk_1"],
            ),
            make_response(
                agent_name="cardiac_specialist",
                organ=Organ.CARDIAC,
                content="心房颤动，心动过速",
                confidence=0.90,
                disease_code="UDT-CARDIAC-AF",
                evidence=["car_chunk_1"],
            ),
            make_response(
                agent_name="liver_specialist",
                organ=Organ.LIVER,
                content="肝脏未见明显异常",
                confidence=0.85,
                evidence=["liv_chunk_1"],
            ),
        ]
        request = FusionRequest(agent_responses=responses)

        result = await checker.check(request)

        # 应触发甲亢性心脏病
        assert result.severity == ConsistencySeverity.WARNING
        assert "hyperthyroid_heart_disease" in result.detail
        assert ("thyroid", "cardiac") in result.inconsistent_organ_pairs
        # WARNING 级别 → is_consistent 仍为 True
        assert result.is_consistent is True


# ======================================================================
# 真实临床场景 7：used_evidence_ids 精确撤销（KB safety_critical）
# ======================================================================


class TestRealClinicalUsedEvidenceIds:
    """真实临床场景下的 used_evidence_ids 字段测试。

    覆盖审查报告 P2-1 增强：used_evidence_ids 用于 KB safety_critical
    精确撤销。区别于 evidence_chunk_ids（所有引用），used_evidence_ids
    仅记录最终结论实际依赖的 chunk 子集。
    """

    def test_real_clinical_used_evidence_ids_distinct_from_evidence(self):
        """真实场景 7.1：used_evidence_ids ⊆ evidence_chunk_ids（子集关系）。

        验证：specialist 引用 5 个 chunk，但最终结论仅依赖 2 个。
        """
        resp = make_response(
            organ=Organ.THYROID,
            content="TR4",
            evidence=["c1", "c2", "c3", "c4", "c5"],  # 5 个引用
            used_evidence_ids=["c2", "c4"],  # 仅 2 个实际使用
        )
        # 验证子集关系
        assert set(resp.used_evidence_ids).issubset(set(resp.evidence_chunk_ids))
        assert len(resp.used_evidence_ids) < len(resp.evidence_chunk_ids)

    def test_real_clinical_used_evidence_ids_for_kb_upgrade_simulation(self):
        """真实场景 7.2：KB 升级模拟 — 仅重新评估 used_evidence_ids 命中的结论。

        临床背景：safety_critical 知识升级时，只需重跑依赖了旧 KB 的
        结论，而非所有结论（节省资源）。
        """
        # 旧 KB 下的诊断
        old_resp = make_response(
            organ=Organ.LIVER,
            content="LR-3 中度可疑",
            evidence=["old_c1", "old_c2", "old_c3"],
            used_evidence_ids=["old_c2"],  # 结论仅依赖 old_c2
        )

        # 模拟 KB 升级：old_c2 被标记为 safety_critical 升级
        safety_critical_chunks = {"old_c2"}

        # 仅 used_evidence_ids 命中 safety_critical → 需要重新评估
        needs_reeval = bool(
            set(old_resp.used_evidence_ids) & safety_critical_chunks
        )
        assert needs_reeval is True

        # 如果 used_evidence_ids 不包含 safety_critical chunk → 无需重跑
        other_resp = make_response(
            organ=Organ.THYROID,
            content="TR2",
            evidence=["old_c1", "old_c3", "old_c4"],
            used_evidence_ids=["old_c4"],  # 不依赖 old_c2
        )
        needs_reeval2 = bool(
            set(other_resp.used_evidence_ids) & safety_critical_chunks
        )
        assert needs_reeval2 is False
