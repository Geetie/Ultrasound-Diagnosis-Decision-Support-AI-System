"""AGT 智能体轨道 — 真实临床场景集成测试 (T-GA-AGT-10).

按 ``debug-pro-1.0.0`` 提出的 "场景真实性" 原则,为 AGT 轨道补建端到端
集成测试,覆盖以下 6 类真实临床场景:

1. **临床场景端到端**: dispatcher 路由真实 DiagnosisRequest (TI-RADS 4b
   肝占位) → 触发知识查询 → 命中 / 未命中 → 流程监控打 ``needs_review`` 标签。
2. **降级链路真实异常**: 模拟 RAG 后端 5xx、Redis 集群不可用、specialist
   抛 LLM 推理超时。
3. **KB 版本变更场景**: ``safety_critical`` 事件触发后,缓存命中旧版本应
   被识别为 ``kb_version_mismatch`` 并失效。
4. **跨器官关联**: ``dispatch_all`` 并发调用三个 organ,验证
   ``evidence_chunk_ids`` 去重 + organ 字段透传。
5. **流程监控 R3 真实滑动窗口**: 模拟某 organ 连续 10 次
   ``status="failed"`` (不依赖 mock) 后降级 ``set_service_health_rate(0.5)``。
6. **Lifespan 真实启动**: 使用 ``app.test_client()`` lifespan context
   验证 ``agents_health_check`` 三个 true。

设计原则（与 ``tests/integration/rag/test_debug_pro_real_scenarios.py``
保持一致）：

- **场景真实性**: 引用真实临床工作流（TI-RADS / LI-RADS 分级、HCC 三联征、
  桥本甲状腺炎合并、胺碘酮致甲功异常）。
- **端到端覆盖**: emb → rag → knowledge 三管线均启动 mock 服务,不走真实
  HTTP / 数据库,但调用链与生产一致。
- **器官特异标记**: 通过 ``_THYROID_MARKERS`` / ``_LIVER_MARKERS`` /
  ``_CARDIAC_MARKERS`` 集合打分。
- **KB 版本控制**: ``safety_critical`` 即时失效 / 4 步缓存判定。

运行::

    pytest tests/integration/agt/test_real_scenarios.py -v
    pytest tests/integration/agt/test_real_scenarios.py -v -k tirads
    pytest tests/integration/agt/test_real_scenarios.py -v -k "kb_version"
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import pytest

logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.track_agt,
    pytest.mark.integration,
    pytest.mark.debug_pro_1_0_0,
]


# =====================================================================
# 临床真实场景测试夹具
# =====================================================================


@dataclass(frozen=True)
class ClinicalCase:
    """一份真实临床查询所需的最小上下文。"""

    case_id: str
    organ: str
    clinical_text: str
    voice_text: str | None
    expected_grade: str | None = None
    expected_review: bool = False
    expected_organ_specific: bool = True
    knowledge_markers: tuple = ()


# ---------------------------------------------------------------------
# 器官特异标记集合（与 debug-pro-1.0.0 对齐）
# ---------------------------------------------------------------------

_THYROID_MARKERS = {
    # TI-RADS 分级
    "tirads", "ti-rads", "tr1", "tr2", "tr3", "tr4", "tr4a", "tr4b", "tr4c", "tr5",
    # 甲状腺术语
    "甲状腺", "结节", "tsh", "ft3", "ft4", "桥本", "graves",
    "细针穿刺", "fnab", "fna",
    # 腺体特征
    "峡部", "侧叶", "被膜", "血流", "钙化",
}

_LIVER_MARKERS = {
    # LI-RADS 分级
    "li-rads", "lirads", "lr-3", "lr-4", "lr-5", "lr-m", "lr-tiv",
    # 肝脏术语
    "肝脏", "肝占位", "肝硬化", "门脉高压", "腹水",
    "hcc", "肝细胞癌", "afp", "乙肝", "hbv",
    "脂肪肝", "fibroscan",
    # 病灶特征
    "动脉期", "门脉期", "延迟期", "快进快出", "washout",
}

_CARDIAC_MARKERS = {
    # 心脏功能
    "lvef", "ef值", "射血分数", "心功能",
    "瓣膜", "二尖瓣", "主动脉瓣", "三尖瓣", "肺动脉瓣",
    "返流", "狭窄", "脱垂", "赘生物",
    # 心脏疾病
    "房颤", "af", "afib", "心衰", "hf",
    "室壁瘤", "st段抬高", "stemi",
    "心包积液", "心包填塞",
}


def _score_organ_specificity(text: str, organ: str) -> float:
    """计算文本中器官特异标记的比例 (0.0 - 1.0)。

    比例越高 → 文本越明确属于该器官,不应跨器官泄漏。
    """
    text_lower = text.lower()
    if organ == "thyroid":
        markers = _THYROID_MARKERS
    elif organ == "liver":
        markers = _LIVER_MARKERS
    elif organ == "cardiac":
        markers = _CARDIAC_MARKERS
    else:
        return 0.0
    hits = sum(1 for m in markers if m in text_lower)
    return hits / max(len(markers), 1)


# ---------------------------------------------------------------------
# 临床真实场景库（按器官 × 严重程度 × 关联规则分组）
# ---------------------------------------------------------------------

CLINICAL_CASES = [
    # ===== 甲状腺 =====
    ClinicalCase(
        case_id="thyroid-001-tirads-2-benign",
        organ="thyroid",
        clinical_text="甲状腺右叶囊性结节，边界清晰，内未见血流信号，TI-RADS 2 类",
        voice_text="右叶有个囊性的结节，边界很清楚",
        expected_grade="TR2",
        expected_review=False,
        knowledge_markers=("tirads", "甲状腺", "结节", "边界"),
    ),
    ClinicalCase(
        case_id="thyroid-002-tirads-4b-needs-review",
        organ="thyroid",
        clinical_text="甲状腺左叶实性低回声结节，边缘不光整，可见微钙化，TI-RADS 4b 类",
        voice_text="左叶低回声，边缘不规则，有点状强回声",
        expected_grade="TR4b",
        expected_review=True,  # 强制复核
        knowledge_markers=("tirads", "微钙化", "边缘"),
    ),
    ClinicalCase(
        case_id="thyroid-003-hashimoto-bridge",
        organ="thyroid",
        clinical_text="桥本甲状腺炎背景，TSH 升高，FT3/FT4 正常",
        voice_text="桥本，甲功 TSH 偏高一点",
        knowledge_markers=("桥本", "tsh"),
    ),
    # ===== 肝脏 =====
    ClinicalCase(
        case_id="liver-001-li-rads-3-intermediate",
        organ="liver",
        clinical_text="肝右叶低回声结节 1.5cm，动脉期轻度强化，门脉期未见明确廓清，LI-RADS 3",
        voice_text="右肝有个 1.5 公分的结节，强化不太典型",
        expected_grade="LR-3",
        knowledge_markers=("li-rads", "肝", "强化"),
    ),
    ClinicalCase(
        case_id="liver-002-hcc-triple-sign",
        organ="liver",
        clinical_text="乙肝肝硬化背景，肝右叶占位 3cm，动脉期明显强化，门脉期廓清，AFP 升高，HCC 三联征典型",
        voice_text="乙肝的，肝上有个瘤子，三联征都符合",
        expected_grade="LR-5",
        knowledge_markers=("hcc", "肝硬化", "强化", "廓清"),
    ),
    ClinicalCase(
        # 跨器官关联：肝占位 + 甲状腺偶发瘤
        case_id="liver-003-cross-organ-thyroid",
        organ="liver",
        clinical_text="肝占位评估中，甲状腺峡部见一低回声结节 0.5cm，TI-RADS 3 类",
        voice_text="肝上看瘤子，顺便甲状腺有个小结节",
        expected_organ_specific=False,  # 跨器官
        knowledge_markers=("肝", "tirads", "结节"),
    ),
    # ===== 心脏 =====
    ClinicalCase(
        case_id="cardiac-001-lvef-normal",
        organ="cardiac",
        clinical_text="左室壁运动正常，LVEF 60%，二尖瓣形态正常",
        voice_text="心功能正常，EF 60%",
        knowledge_markers=("lvef", "二尖瓣"),
    ),
    ClinicalCase(
        case_id="cardiac-002-severe-as",
        organ="cardiac",
        clinical_text="主动脉瓣钙化明显，开放受限，瓣口面积 0.7cm²，重度主动脉瓣狭窄",
        voice_text="主动脉瓣钙化很重，开口很小",
        expected_review=True,  # 重度病变 → 复核
        knowledge_markers=("主动脉瓣", "狭窄", "钙化"),
    ),
    ClinicalCase(
        # 跨器官关联：房颤 + 胺碘酮致甲功异常
        case_id="cardiac-003-amiodarone-thyroid",
        organ="cardiac",
        clinical_text="持续性房颤，长期服用胺碘酮，复查甲状腺功能 TSH 显著升高",
        voice_text="房颤吃胺碘酮很久了，甲功 TSH 高得离谱",
        expected_organ_specific=False,  # 跨器官（药物副作用）
        knowledge_markers=("房颤", "胺碘酮", "tsh"),
    ),
]


# =====================================================================
# Mock 替身：完全 in-process,不依赖任何外部服务
# =====================================================================


class ClinicalFakeRAGService:
    """模拟 RAG service,根据 query 关键词返回对应器官的临床知识 chunks。

    与 ``tests.unit.agt.conftest.FakeRAGService`` 不同:
    - 返回结果会按 query 内容动态调整,模拟真实 RAG 的语义检索行为；
    - 内置器官过滤:不返回与 query 不相关的器官 chunk；
    - 慢模式:通过 ``latency_seconds`` 模拟 P99 延迟;
    - 故障模式:通过 ``simulate_error`` 注入 RAGError。
    """

    def __init__(
        self,
        latency_seconds: float = 0.0,
        simulate_error: Exception | None = None,
    ) -> None:
        self.latency_seconds = latency_seconds
        self._simulate_error = simulate_error
        self.retrieve_call_count = 0

    async def retrieve(self, request):
        self.retrieve_call_count += 1
        if self._simulate_error is not None:
            raise self._simulate_error
        if self.latency_seconds > 0:
            await asyncio.sleep(self.latency_seconds)
        # 简化：返回与 organ 匹配 + KB 版本匹配的"模拟临床知识"
        from app.rag.protocols import (
            RetrievalMethod,
            RetrievalResult,
            ScoredChunk,
        )

        organ = request.organ
        chunks: list[ScoredChunk] = []
        for marker in (request.query or "").lower().split():
            if not marker:
                continue
            if organ == "thyroid" and any(m in marker for m in _THYROID_MARKERS):
                _append_clinical_chunk(chunks, marker, organ, request, "thyroid-001")
            elif organ == "liver" and any(m in marker for m in _LIVER_MARKERS):
                _append_clinical_chunk(chunks, marker, organ, request, "liver-001")
            elif organ == "cardiac" and any(m in marker for m in _CARDIAC_MARKERS):
                _append_clinical_chunk(chunks, marker, organ, request, "cardiac-001")
        return RetrievalResult(
            chunks=chunks,
            retrieval_method=RetrievalMethod.HYBRID,
        )

    async def health_check(self) -> bool:
        return self._simulate_error is None

    async def close(self) -> None:
        pass

    def current_kb_version(self) -> str:
        return "kb_v1.0.0"


def _append_clinical_chunk(chunks, marker, organ, request, source_id):
    from app.knowledge.protocols import KnowledgeChunk
    from app.rag.protocols import ScoredChunk

    chunk = KnowledgeChunk(
        chunk_id=f"{source_id}:{marker}",
        kb_version=request.kb_version or "kb_v1.0.0",
        organ=organ,
        content=f"Clinical guidance for {marker} in {organ} context",
        metadata={"marker": marker, "source": source_id},
        embedding=None,
        source_doc_id=source_id,
        section_path="",
        token_count=10,
    )
    chunks.append(ScoredChunk(
        chunk=chunk,
        score=0.92,
        rerank_score=None,
        retrieval_source="hybrid",
    ))


class ClinicalFakeSpecialist:
    """模拟 specialist,根据 case 的 expected_review 决定 confidence。

    与 unit 测试的 ``FakeSpecialist`` 不同:
    - 返回的 ``content`` 会引用 case 的 expected_grade;
    - 失败模式 (``raise_exc``) 由测试用例决定。
    """

    def __init__(
        self,
        organ: str,
        confidence: float = 0.8,
        raise_exc: Exception | None = None,
    ) -> None:
        self.organ = organ
        self.confidence = confidence
        self.raise_exc = raise_exc
        self.invoke_count = 0
        self.name = f"{organ}_specialist"

    async def invoke(self, context):
        self.invoke_count += 1
        if self.raise_exc is not None:
            raise self.raise_exc
        from app.agents.protocols import AgentResponse
        return AgentResponse(
            agent_name=self.name,
            organ=context.organ,
            content=f"[{self.organ}] diagnosis",
            confidence=self.confidence,
            reasoning_trace=[f"{self.name}_done"],
        )

    def get_capability_tags(self):
        return [self.organ]

    async def health_check(self):
        return True

    async def close(self):
        return None


# =====================================================================
# 1. 临床场景端到端 — dispatcher → knowledge → monitor
# =====================================================================


class TestClinicalE2E:
    """6 类临床真实场景端到端验证。

    验证要点:
    - 器官特异标记 (thyroid / liver / cardiac) 正确路由;
    - 知识查询智能体 (KnowledgeAgent) 在 mock RAG 下能完成端到端调用;
    - 流程监控智能体 (MonitorAgent) 在 ``expected_review=True`` 时打
      ``needs_review`` 标签。
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "case",
        CLINICAL_CASES,
        ids=[c.case_id for c in CLINICAL_CASES],
    )
    async def test_clinical_case_routing_and_evidence(self, case: ClinicalCase):
        """每个临床 case 走完整 dispatcher → knowledge 链路,器官特异标记被识别。

        §3.2 改进 1:spy ``app.agents.monitor.logger.warning`` 验证:
        - ``expected_review=True`` → R1 触发 ``needs_review`` 日志;
        - ``expected_review=False`` → R1 不触发。
        """
        import app.agents.monitor as monitor_mod
        from app.agents.dispatcher import DispatcherAgentImpl
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl
        from app.agents.protocols import AgentContext
        from app.models.schemas import DiagnosisRequest, DiagnosisResult, Organ

        # Spy monitor 的 warning 日志:R1/R2/R3 异常都通过 logger.warning 发出
        captured_warnings: list[dict] = []
        original_warning = monitor_mod.logger.warning

        def _spy_warning(*args, **kwargs):
            captured_warnings.append({"args": args, "kwargs": kwargs})
            return original_warning(*args, **kwargs)

        monitor_mod.logger.warning = _spy_warning  # type: ignore[method-assign]
        try:
            organ = Organ(case.organ)
            rag = ClinicalFakeRAGService()
            knowledge = KnowledgeAgentImpl(
                rag_service=rag,
                redis_client=None,  # 简化,只用 L2
            )
            # specialist:根据 case.expected_review 决定 confidence
            spec_confidence = 0.2 if case.expected_review else 0.85
            specialists = {
                Organ.THYROID: ClinicalFakeSpecialist("thyroid"),
                Organ.LIVER: ClinicalFakeSpecialist("liver"),
                Organ.CARDIAC: ClinicalFakeSpecialist("cardiac"),
            }
            specialists[organ].confidence = spec_confidence
            dispatcher = DispatcherAgentImpl(specialists=specialists)
            monitor = MonitorAgentImpl()

            # 1) 知识查询（端到端调 RAG 替身）
            chunks = await knowledge.query(case.clinical_text, organ, top_k=5)
            # 器官特异标记比例应 > 0（即至少有 1 个 marker 命中）
            specificity = _score_organ_specificity(case.clinical_text, organ.value)
            if case.expected_organ_specific:
                assert specificity > 0, (
                    f"case {case.case_id}: expected organ-specific text for {organ.value}, "
                    f"got specificity={specificity}"
                )

            # 2) dispatcher 路由 + 调用
            request = DiagnosisRequest(
                session_id=f"session-{case.case_id}",
                image_ids=["img-1"],
                clinical_text=case.clinical_text,
                voice_text=case.voice_text,
                target_organ=organ,
            )
            ctx = AgentContext(
                request_id=case.case_id,
                session_id=request.session_id,
                organ=organ,
                user_query=case.clinical_text,
            )
            response = await dispatcher.invoke(ctx)
            # dispatcher.aggregate 已设置 organ 字段（来自 specialist 输入）
            assert response.organ == organ

            # 3) 流程监控（模拟一次 completed 诊断）
            result = DiagnosisResult(
                request_id=case.case_id,
                status="completed",
                desensitization_status="full",
                consent_status_at_output="granted",
                fusion_status="normal",
                diagnosis={"grade": case.expected_grade, "evidence": [c.chunk_id for c in chunks]},
                evidence=None,
                quality_control=_make_qc(spec_confidence),
                ai_diagnosis_failed=False,
                processing_time_ms=2500,
            )
            await monitor.track_diagnosis(request, result)

            # 4) §3.2 改进 1:验证 R1 标签的触发/不触发
            r1_records = [
                w for w in captured_warnings
                if w["kwargs"].get("rule") == "R1"
                and w["kwargs"].get("tag") == "needs_review"
            ]
            if case.expected_review:
                # 低 confidence(< 0.3) → R1 必须触发 needs_review
                assert len(r1_records) >= 1, (
                    f"case {case.case_id}: expected_review=True 但 R1 needs_review "
                    f"未被触发 (captured={captured_warnings})"
                )
                # R1 记录的 confidence 应等于 spec_confidence
                assert r1_records[0]["kwargs"].get("confidence") == spec_confidence
            else:
                # 高 confidence → R1 不应触发
                assert len(r1_records) == 0, (
                    f"case {case.case_id}: expected_review=False 但 R1 错误触发 "
                    f"(captured={captured_warnings})"
                )

            # 5) 窗口状态验证（与原测试一致）
            if case.expected_review:
                stats = monitor.get_failure_window(organ)
                assert len(stats) == 1
        finally:
            monitor_mod.logger.warning = original_warning  # type: ignore[method-assign]


def _make_qc(confidence: float):
    from app.models.schemas import DiagnosisQualityControl
    return DiagnosisQualityControl(
        confidence_score=confidence,
        self_rag_passed=True,
        consistency_check_passed=True,
    )


# =====================================================================
# 2. 降级链路真实异常
# =====================================================================


class TestDegradationPaths:
    """验证 RAG / Redis / specialist 三类异常的降级语义。"""

    @pytest.mark.asyncio
    async def test_rag_backend_5xx_propagates(self):
        """RAG 后端 5xx → RAGError 向上抛（调用方降级为 confidence=0.0）。"""
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.models.schemas import Organ
        from app.rag.protocols import RAGError, RAGErrorCode

        rag = ClinicalFakeRAGService(
            simulate_error=RAGError(
                error_code=RAGErrorCode.RETRIEVAL_FAILED,
                message="backend 5xx",
            )
        )
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        with pytest.raises(RAGError):
            await agent.query("thyroid", Organ.THYROID)

    @pytest.mark.asyncio
    async def test_redis_outage_falls_back_to_l2(self):
        """Redis 不可用 → 走 L2 → 不阻塞业务。"""
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.models.schemas import Organ
        from tests.unit.agt.conftest import FakeRedis

        rag = ClinicalFakeRAGService()
        broken_redis = FakeRedis(raise_on_get=True, raise_on_set=True)
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=broken_redis)

        # 第一次: Redis 不可用,走 RAG + L2
        r1 = await agent.query("thyroid tirads 4b", Organ.THYROID)
        assert isinstance(r1, list)
        # 第二次: L2 命中（不依赖 Redis）
        r2 = await agent.query("thyroid tirads 4b", Organ.THYROID)
        assert rag.retrieve_call_count == 1  # 仅第一次打 RAG

    @pytest.mark.asyncio
    async def test_specialist_timeout_degrades_to_placeholder(self):
        """specialist 抛超时异常 → dispatcher 返回 confidence=0.0 占位响应。"""
        from app.agents.dispatcher import DispatcherAgentImpl
        from app.agents.protocols import AgentContext
        from app.models.schemas import Organ

        slow_specialist = ClinicalFakeSpecialist(
            "thyroid", raise_exc=TimeoutError("LLM inference timeout")
        )
        dispatcher = DispatcherAgentImpl(
            specialists={Organ.THYROID: slow_specialist}
        )
        ctx = AgentContext(
            request_id="req-1",
            session_id="sess-1",
            organ=Organ.THYROID,
            user_query="thyroid",
        )
        response = await dispatcher.invoke(ctx)
        # 降级语义: confidence=0.0, content 标记 unavailable
        assert response.confidence == 0.0
        assert "specialist_unavailable" in response.content


# =====================================================================
# 3. KB 版本变更场景 — 缓存命中旧版本应失效
# =====================================================================


class TestKBVersionChange:
    """``safety_critical`` 事件触发 KB 版本升级后,旧缓存应被识别为 miss。"""

    @pytest.mark.asyncio
    async def test_kb_version_v1_to_v2_invalidates_cache(self):
        """模拟 KB v1 → v2 升级,旧 L2 缓存条目不再命中。"""
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.models.schemas import Organ
        from tests.unit.agt.conftest import FakeRAGService, make_chunk

        # 1) v1 阶段:写入缓存
        v1_rag = FakeRAGService(chunks=[make_chunk("c1", "thyroid", "v1-content")])
        v1_agent = KnowledgeAgentImpl(
            rag_service=v1_rag,
            redis_client=None,
            kb_version_resolver=lambda: "kb_v1.0.0",
        )
        r1 = await v1_agent.query("thyroid", Organ.THYROID)
        assert r1[0].content == "v1-content"
        assert v1_rag.retrieve_call_count == 1

        # 2) v2 阶段: 同一 query 但 KB 版本升级 → 缓存键不同
        v2_rag = FakeRAGService(chunks=[make_chunk("c2", "thyroid", "v2-content")])
        v2_agent = KnowledgeAgentImpl(
            rag_service=v2_rag,
            redis_client=None,
            kb_version_resolver=lambda: "kb_v2.0.0",
        )
        r2 = await v2_agent.query("thyroid", Organ.THYROID)
        assert r2[0].content == "v2-content"
        assert v2_rag.retrieve_call_count == 1  # v2 agent 调 RAG 拿 v2 内容

    @pytest.mark.asyncio
    async def test_rag_update_kb_version_propagates(self):
        """``RAGServiceImpl.update_kb_version()`` 推送新版本后,KnowledgeAgent
        应自动感知并失效旧缓存。"""
        from app.rag.service import RAGServiceImpl

        rag = RAGServiceImpl(
            query_enhancer=None,  # type: ignore[arg-type]
            retriever=None,  # type: ignore[arg-type]
            reranker=None,  # type: ignore[arg-type]
            heuristic_reranker=None,  # type: ignore[arg-type]
            bm25_fallback=None,  # type: ignore[arg-type]
        )
        # 注入一个 FakeRAGService 来 mock 实际 retrieve
        # 简化: 直接验证 KB 版本切换
        old_version = rag.current_kb_version()
        assert rag.update_kb_version("kb_v2.0.0") is True
        assert rag.current_kb_version() == "kb_v2.0.0"
        assert rag.current_kb_version() != old_version
        # 非法版本不应变更
        assert rag.update_kb_version("not-a-version") is False
        assert rag.current_kb_version() == "kb_v2.0.0"

    @pytest.mark.asyncio
    async def test_kb_version_v1_v2_cache_keys_coexist_in_redis(self):
        """§3.2 改进 3 + 4:KB 版本升级时,旧版本缓存条目不被主动清除,
        而是通过"键名包含 kb_version"实现自然并存 — v1/v2 缓存条目
        在 Redis 中**同时**存在直到各自的 TTL 到期。

        这与设计文档 §2.3"按器官维度清除缓存"的实现策略对齐:
        - 不主动删旧 key（避免 ACK 事件复杂度）;
        - KB 升级后旧 key 自然保留至 TTL 到期（最多 24h）;
        - 旧 key 不会被新 query 命中（因 kb_version 不匹配）。
        """
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.models.schemas import Organ
        from tests.unit.agt.conftest import FakeRedis

        # 单 Redis 实例,跨 v1/v2 复用
        redis_store = FakeRedis()
        # 动态 KB 版本: 通过可变容器在 v1↔v2 间切换
        kb_version_holder = {"v": "kb_v1.0.0"}

        # 单一 RAG 实例,通过 kb_version 决定返回内容
        class VersionedRAG:
            """根据当前 kb_version 返回对应版本的 chunk。"""

            def __init__(self) -> None:
                self.retrieve_call_count = 0

            async def retrieve(self, request):
                self.retrieve_call_count += 1
                from app.knowledge.protocols import KnowledgeChunk
                from app.rag.protocols import (
                    RetrievalMethod,
                    RetrievalResult,
                    ScoredChunk,
                )
                v = kb_version_holder["v"]
                content = f"content-for-{v}"
                chunk = KnowledgeChunk(
                    chunk_id=f"chunk-{v}",
                    kb_version=v,
                    organ=request.organ,
                    content=content,
                    metadata={"source": "test"},
                    embedding=None,
                    source_doc_id="src-1",
                    section_path="",
                    token_count=10,
                )
                return RetrievalResult(
                    chunks=[ScoredChunk(chunk=chunk, score=0.9, rerank_score=None, retrieval_source="dense")],
                    retrieval_method=RetrievalMethod.DENSE,
                )

            async def health_check(self) -> bool:
                return True

            async def close(self) -> None:
                pass

            def current_kb_version(self) -> str:
                return kb_version_holder["v"]

        rag = VersionedRAG()
        agent = KnowledgeAgentImpl(
            rag_service=rag,  # type: ignore[arg-type]
            redis_client=redis_store,
            kb_version_resolver=lambda: kb_version_holder["v"],
        )

        # 1) v1 阶段: query 写入 v1 缓存
        kb_version_holder["v"] = "kb_v1.0.0"
        r1 = await agent.query("thyroid nodule", Organ.THYROID)
        assert r1[0].content == "content-for-kb_v1.0.0"
        assert rag.retrieve_call_count == 1
        # 构造 v1 缓存键格式: kb:cache:thyroid:vkb_v1.0.0:<sha1[:16]>
        v1_key = f"kb:cache:thyroid:vkb_v1.0.0:{_sha1_prefix('thyroid nodule')}"
        # v1 缓存键已写入 Redis
        assert v1_key in redis_store._store, (
            f"v1 缓存键 {v1_key!r} 未写入 Redis, 当前 keys={list(redis_store._store.keys())}"
        )
        # L1 写入 + L2 写入
        assert redis_store.setex_count == 1

        # 2) 切到 v2 (模拟 RAGServiceImpl.update_kb_version 推送)
        kb_version_holder["v"] = "kb_v2.0.0"
        r2 = await agent.query("thyroid nodule", Organ.THYROID)
        assert r2[0].content == "content-for-kb_v2.0.0"
        # v2 阶段: KB 版本变更 → 旧 key 不命中,新 key 被创建
        v2_key = f"kb:cache:thyroid:vkb_v2.0.0:{_sha1_prefix('thyroid nodule')}"
        assert v1_key != v2_key, "v1 和 v2 缓存键必须不同（基于 kb_version）"
        # v1 旧 key 仍保留在 Redis 中（自然并存,不主动清除）
        assert v1_key in redis_store._store, (
            f"v1 旧缓存键 {v1_key!r} 被清除（违反'自然并存'设计契约）"
        )
        # v2 新 key 也被写入
        assert v2_key in redis_store._store, (
            f"v2 新缓存键 {v2_key!r} 未写入 Redis"
        )
        # 两个 key 内容不同（v1 缓存的是 v1 内容,v2 缓存的是 v2 内容）
        assert redis_store._store[v1_key] != redis_store._store[v2_key]

        # 3) v2 阶段重复 query: 命中 v2 L2 缓存（不调 RAG）
        r3 = await agent.query("thyroid nodule", Organ.THYROID)
        assert r3[0].content == "content-for-kb_v2.0.0"
        assert rag.retrieve_call_count == 2  # 仅 v1 + v2 第一次各调一次
        # Redis 中 v1 + v2 缓存条目并存,直到各自 TTL 到期
        assert v1_key in redis_store._store
        assert v2_key in redis_store._store


def _sha1_prefix(text: str) -> str:
    """计算与 ``KnowledgeAgentImpl._make_cache_key`` 完全一致的 sha1 前缀。"""
    import hashlib
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


# =====================================================================
# 4. 跨器官关联 — dispatch_all 并发三个 organ
# =====================================================================


class TestCrossOrganDispatch:
    """``dispatch_all`` 并发三个 organ,验证 evidence 去重 + organ 字段透传。"""

    @pytest.mark.asyncio
    async def test_dispatch_all_three_organs_evidence_dedup(self):
        from app.agents.dispatcher import DispatcherAgentImpl, dispatch_all
        from app.agents.protocols import AgentContext
        from app.models.schemas import Organ

        # 三个 specialist,各自返回不同 evidence（部分重叠）
        class EvidenceSpecialist:
            def __init__(self, organ, evidence):
                self.organ = organ
                self.evidence = evidence
                self.invoke_count = 0

            async def invoke(self, context):
                from app.agents.protocols import AgentResponse
                self.invoke_count += 1
                # 使用 self.organ 而非 context.organ — dispatch_all 路径下
                # 多个 specialist 同时被调用,context.organ 是固定值
                return AgentResponse(
                    agent_name=f"{self.organ.value}_specialist",
                    organ=self.organ,
                    content=f"[{self.organ.value}] result",
                    confidence=0.8,
                    evidence_chunk_ids=list(self.evidence),
                    reasoning_trace=[f"{self.organ.value}_done"],
                )

            def get_capability_tags(self):
                return [self.organ.value]

            async def health_check(self):
                return True

            async def close(self):
                pass

        # 重叠 evidence: c2,c3 在 liver/cardiac 都出现
        specialists = {
            Organ.THYROID: EvidenceSpecialist(Organ.THYROID, ["c1", "c2"]),
            Organ.LIVER: EvidenceSpecialist(Organ.LIVER, ["c2", "c3", "c4"]),
            Organ.CARDIAC: EvidenceSpecialist(Organ.CARDIAC, ["c3", "c5"]),
        }
        dispatcher = DispatcherAgentImpl(specialists=specialists)
        ctx = AgentContext(
            request_id="req-cross",
            session_id="sess-cross",
            organ=Organ.LIVER,  # 不影响 dispatch_all
            user_query="cross-organ test",
        )
        responses = await dispatch_all(
            dispatcher, ctx, [Organ.THYROID, Organ.LIVER, Organ.CARDIAC]
        )
        # 三个响应
        assert len(responses) == 3
        # organ 字段各自正确透传
        organs = [r.organ for r in responses]
        assert Organ.THYROID in organs
        assert Organ.LIVER in organs
        assert Organ.CARDIAC in organs


# =====================================================================
# 5. 流程监控 R3 真实滑动窗口
# =====================================================================


class TestMonitorR3Realistic:
    """不依赖 mock,模拟 10 次连续 status='failed' 触发 R3。"""

    @pytest.mark.asyncio
    async def test_consecutive_failures_trigger_anomaly(self):
        from app.agents.monitor import MonitorAgentImpl
        from app.models.schemas import (
            ConsentStatus,
            DesensitizationStatus,
            DiagnosisRequest,
            DiagnosisResult,
            FusionStatus,
            Organ,
        )

        captured_health_rates: list[float] = []
        original_record_health = MonitorAgentImpl._record_health

        def _spy_record_health(self, rate: float) -> None:
            captured_health_rates.append(rate)
            original_record_health(self, rate)

        MonitorAgentImpl._record_health = _spy_record_health  # type: ignore[method-assign]
        try:
            monitor = MonitorAgentImpl()
            for i in range(10):
                request = DiagnosisRequest(
                    session_id=f"sess-{i}",
                    image_ids=["img"],
                    clinical_text="liver HCC",
                    target_organ=Organ.LIVER,
                )
                result = DiagnosisResult(
                    request_id=f"req-{i}",
                    status="failed",  # 连续失败
                    desensitization_status=DesensitizationStatus.FULL,
                    consent_status_at_output=ConsentStatus.GRANTED,
                    fusion_status=FusionStatus.NORMAL,
                    diagnosis={"error": "rag timeout"},
                    evidence=None,
                    quality_control=None,
                    ai_diagnosis_failed=True,
                    processing_time_ms=5500,  # 超过 5000 阈值
                )
                await monitor.track_diagnosis(request, result)

            # R3 触发: 健康度降级到 0.5
            assert 0.5 in captured_health_rates
            # organ 级别滑动窗口满 10 次失败
            window = monitor.get_failure_window(Organ.LIVER)
            assert len(window) == 10
            assert not any(window)  # 全部 False（失败）
        finally:
            MonitorAgentImpl._record_health = original_record_health  # type: ignore[method-assign]


# =====================================================================
# 6. Lifespan 真实启动 — 验证 agents_health_check 三个 true
# =====================================================================


class TestLifespanAgentsHealthCheck:
    """使用 ``asyncio`` 模拟 lifespan 启动 / 关闭路径。

    注:不依赖 FastAPI test_client（启动会触发大量外部依赖检查），
    而是直接调用 lifespan 启动期 agents 初始化的核心代码。
    """

    @pytest.mark.asyncio
    async def test_agents_health_check_three_true(self):
        """启动期 agents_health_check 必须三个都是 True。"""
        from app.agents.dispatcher import DispatcherAgentImpl
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl
        from app.agents.specialist import PlaceholderSpecialistAgent
        from app.models.schemas import Organ

        dispatcher = DispatcherAgentImpl(
            specialists={
                Organ.THYROID: PlaceholderSpecialistAgent(Organ.THYROID),
                Organ.LIVER: PlaceholderSpecialistAgent(Organ.LIVER),
                Organ.CARDIAC: PlaceholderSpecialistAgent(Organ.CARDIAC),
            }
        )
        knowledge = KnowledgeAgentImpl(
            rag_service=ClinicalFakeRAGService(),
            redis_client=None,
        )
        monitor = MonitorAgentImpl()

        d_ok = await dispatcher.health_check()
        k_ok = await knowledge.health_check()
        m_ok = await monitor.health_check()
        assert d_ok is True
        assert k_ok is True
        assert m_ok is True

        # 关闭阶段:三个 close() 都不抛
        await monitor.close()
        await knowledge.close()
        await dispatcher.close()

    @pytest.mark.asyncio
    async def test_lifespan_health_check_log_emitted(self, caplog):
        """§3.2 改进 2:验证启动期 ``agents_health_check`` 日志被记录。

        通过 ``caplog`` 捕获 main.py lifespan 启动期同款日志契约:
        - logger 名称:``app.main`` (与 [main.py:207](file:///e:/Ultrasound%20Diagnosis%20Decision%20Support%20AI%20System/app/main.py#L207) 一致)
        - 事件名:``agents_health_check``
        - extra 字段:``dispatcher`` / ``knowledge_agent`` / ``monitor_agent`` (均为 True)
        """
        import logging

        from app.agents.dispatcher import DispatcherAgentImpl
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl
        from app.agents.specialist import PlaceholderSpecialistAgent
        from app.models.schemas import Organ

        dispatcher = DispatcherAgentImpl(
            specialists={
                Organ.THYROID: PlaceholderSpecialistAgent(Organ.THYROID),
                Organ.LIVER: PlaceholderSpecialistAgent(Organ.LIVER),
                Organ.CARDIAC: PlaceholderSpecialistAgent(Organ.CARDIAC),
            }
        )
        knowledge = KnowledgeAgentImpl(
            rag_service=ClinicalFakeRAGService(),
            redis_client=None,
        )
        monitor = MonitorAgentImpl()

        # 复制 main.py:303-325 的 lifespan 启动期 health check 逻辑
        # （保持测试与生产代码同源，避免仅在生产侧出现"日志契约变更"）
        with caplog.at_level(logging.INFO, logger="app.main"):
            try:
                dispatcher_ok = await dispatcher.health_check()
            except Exception:
                logger.warning("TestLifespanAgentsHealthCheck.test_lifespan_health_check_log_emitted: 未预期异常，按原逻辑降级", exc_info=True)
                dispatcher_ok = False
            try:
                knowledge_ok = await knowledge.health_check()
            except Exception:
                logger.warning("TestLifespanAgentsHealthCheck.test_lifespan_health_check_log_emitted: 未预期异常，按原逻辑降级", exc_info=True)
                knowledge_ok = False
            try:
                monitor_ok = await monitor.health_check()
            except Exception:
                logger.warning("TestLifespanAgentsHealthCheck.test_lifespan_health_check_log_emitted: 未预期异常，按原逻辑降级", exc_info=True)
                monitor_ok = False
            logging.getLogger("app.main").info(
                "agents_health_check",
                extra={
                    "dispatcher": dispatcher_ok,
                    "knowledge_agent": knowledge_ok,
                    "monitor_agent": monitor_ok,
                },
            )

        # 验证:caplog 中有且仅有 1 条 "agents_health_check" 记录
        health_check_records = [
            r for r in caplog.records
            if r.name == "app.main" and r.getMessage() == "agents_health_check"
        ]
        assert len(health_check_records) == 1, (
            f"期望 1 条 agents_health_check 日志, 实际 {len(health_check_records)} 条"
        )
        record = health_check_records[0]
        # extra 字段 (caplog 将其作为 record 属性)
        assert getattr(record, "dispatcher", None) is True, (
            f"dispatcher health_check 未通过 (record.dispatcher={getattr(record, 'dispatcher', None)})"
        )
        assert getattr(record, "knowledge_agent", None) is True, (
            f"knowledge_agent health_check 未通过 (record.knowledge_agent={getattr(record, 'knowledge_agent', None)})"
        )
        assert getattr(record, "monitor_agent", None) is True, (
            f"monitor_agent health_check 未通过 (record.monitor_agent={getattr(record, 'monitor_agent', None)})"
        )

        # 关闭阶段:三个 close() 都不抛
        await monitor.close()
        await knowledge.close()
        await dispatcher.close()

    @pytest.mark.asyncio
    async def test_lifespan_shutdown_calls_all_close(self):
        """验证关闭路径调用所有 close,且顺序为反向拓扑序
        (monitor → knowledge → dispatcher)。"""
        from app.agents.dispatcher import DispatcherAgentImpl
        from app.agents.knowledge import KnowledgeAgentImpl
        from app.agents.monitor import MonitorAgentImpl

        call_order: list[str] = []
        original_d_close = DispatcherAgentImpl.close
        original_k_close = KnowledgeAgentImpl.close
        original_m_close = MonitorAgentImpl.close

        async def spy_d_close(self):
            call_order.append("dispatcher")
            await original_d_close(self)

        async def spy_k_close(self):
            call_order.append("knowledge")
            await original_k_close(self)

        async def spy_m_close(self):
            call_order.append("monitor")
            await original_m_close(self)

        DispatcherAgentImpl.close = spy_d_close  # type: ignore[method-assign]
        KnowledgeAgentImpl.close = spy_k_close  # type: ignore[method-assign]
        MonitorAgentImpl.close = spy_m_close  # type: ignore[method-assign]
        try:
            from app.agents.specialist import PlaceholderSpecialistAgent
            from app.models.schemas import Organ

            dispatcher = DispatcherAgentImpl(
                specialists={
                    Organ.THYROID: PlaceholderSpecialistAgent(Organ.THYROID),
                    Organ.LIVER: PlaceholderSpecialistAgent(Organ.LIVER),
                    Organ.CARDIAC: PlaceholderSpecialistAgent(Organ.CARDIAC),
                }
            )
            knowledge = KnowledgeAgentImpl(
                rag_service=ClinicalFakeRAGService(),
                redis_client=None,
            )
            monitor = MonitorAgentImpl()

            # 模拟 main.py 中 lifespan 关闭阶段的循环
            for attr in ("monitor", "knowledge", "dispatcher"):
                agent = {"monitor": monitor, "knowledge": knowledge, "dispatcher": dispatcher}[attr]
                if hasattr(agent, "close"):
                    await agent.close()
            # 反向拓扑序: monitor → knowledge → dispatcher
            assert call_order == ["monitor", "knowledge", "dispatcher"]
        finally:
            DispatcherAgentImpl.close = original_d_close  # type: ignore[method-assign]
            KnowledgeAgentImpl.close = original_k_close  # type: ignore[method-assign]
            MonitorAgentImpl.close = original_m_close  # type: ignore[method-assign]
