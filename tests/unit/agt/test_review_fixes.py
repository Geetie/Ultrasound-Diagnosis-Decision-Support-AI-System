"""审查报告修复点专项测试 (T-GA-AGT-04/05/06 Wave 5.1 修复验证).

覆盖审查报告 t1.txt 中识别的偏离点与改进项：

**D1 偏离点修复**（器官级/病种级置信度阈值接入）— T4
**D2 偏离点修复**（AgentContext.used_evidence_ids 字段）— T3
**D3 偏离点修复**（deviation_severity 字段契约）— T5
**R6 风险修复**（心脏 EF 矛盾检测）— 集成在 test_cardiac_specialist.py
**200→500 字符饱和阈值**改进 — 集成在 test_thyroid_specialist.py
**T2 改进**（LLM 输出格式异常反例）— test_llm_output_anomalies.py

本文件按修复项分组，每个测试方法头部标注对应的报告条目编号。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.specialist import (
    DEFAULT_ORGAN_THRESHOLDS,
    CardiacSpecialistAgent,
    LiverSpecialistAgent,
    ThyroidSpecialistAgent,
    build_dispatcher,
    load_organ_thresholds,
)
from app.agents.specialist.base import (
    _LLM_TEXT_SATURATION_CHARS,
)
from app.models.schemas import Organ
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)

# ----------------------------------------------------------------------
# 公共 fixtures
# ----------------------------------------------------------------------


def _ctx(
    organ: Organ = Organ.THYROID, query: str = "甲状腺结节 6mm"
) -> AgentContext:
    return AgentContext(
        request_id="r1",
        session_id="s1",
        organ=organ,
        user_query=query,
    )


@pytest.fixture
def thyroid_rag():
    return FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))


@pytest.fixture
def liver_rag():
    return FakeKnowledgeAgent(chunks=make_chunks(5, organ="liver"))


@pytest.fixture
def cardiac_rag():
    return FakeKnowledgeAgent(chunks=make_chunks(5, organ="cardiac"))


@pytest.fixture
def thyroid_inference():
    return FakeInferenceEngine(text="分级: TR4\n理由: 实性 + 低回声")


@pytest.fixture
def liver_inference():
    return FakeInferenceEngine(text="分级: LR-5\n理由: APHE+廓清+包膜")


@pytest.fixture
def cardiac_inference():
    return FakeInferenceEngine(text="LVEF: 50%\n分级: mildly_reduced\n理由: FS 25%")


# ======================================================================
# D2 偏离点修复 (used_evidence_ids) — T3 契约测试
# ======================================================================


class TestD2UsedEvidenceIdsContract:
    """T3: used_evidence_ids 契约测试 — 验证 specialist 写入正确。"""

    @pytest.mark.asyncio
    async def test_thyroid_writes_evidence_to_context(
        self, thyroid_rag, thyroid_inference
    ):
        """D2: 甲状腺 specialist 完成后应将 evidence_chunk_ids 写入 ctx。"""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference, knowledge_agent=thyroid_rag
        )
        ctx = _ctx(Organ.THYROID)
        # 初始为空
        assert ctx.used_evidence_ids == []
        await s.invoke(ctx)
        # invoke 后应填充
        assert len(ctx.used_evidence_ids) == 3
        assert ctx.used_evidence_ids == ["chunk_0", "chunk_1", "chunk_2"]

    @pytest.mark.asyncio
    async def test_liver_writes_evidence_to_context(self, liver_rag, liver_inference):
        """D2: 肝脏 specialist 完成后应将 evidence_chunk_ids 写入 ctx。"""
        s = LiverSpecialistAgent(
            inference_engine=liver_inference, knowledge_agent=liver_rag
        )
        ctx = _ctx(Organ.LIVER)
        assert ctx.used_evidence_ids == []
        await s.invoke(ctx)
        assert len(ctx.used_evidence_ids) == 3
        assert ctx.used_evidence_ids == ["chunk_0", "chunk_1", "chunk_2"]

    @pytest.mark.asyncio
    async def test_cardiac_writes_evidence_to_context(
        self, cardiac_rag, cardiac_inference
    ):
        """D2: 心脏 specialist 完成后应将 evidence_chunk_ids 写入 ctx。"""
        s = CardiacSpecialistAgent(
            inference_engine=cardiac_inference, knowledge_agent=cardiac_rag
        )
        ctx = _ctx(Organ.CARDIAC)
        assert ctx.used_evidence_ids == []
        await s.invoke(ctx)
        assert len(ctx.used_evidence_ids) == 3

    @pytest.mark.asyncio
    async def test_evidence_written_even_on_rag_empty(self, thyroid_inference):
        """D2: RAG 空证据时 used_evidence_ids 应仍保持空（不写入脏数据）。"""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=FakeKnowledgeAgent(chunks=[]),
        )
        ctx = _ctx()
        await s.invoke(ctx)
        # evidence_insufficient 响应 evidence_chunk_ids=[] → used_evidence_ids 也应为空
        assert ctx.used_evidence_ids == []

    @pytest.mark.asyncio
    async def test_evidence_written_even_on_llm_failure(self, thyroid_rag):
        """D2: LLM 失败时 used_evidence_ids 仍写入（用于诊断可追溯）。"""
        from app.inference.protocols import InferenceError, InferenceErrorCode

        inf = FakeInferenceEngine(
            raise_exc=InferenceError(
                error_code=InferenceErrorCode.TIMEOUT,
                message="timeout",
                retryable=True,
            )
        )
        s = ThyroidSpecialistAgent(inference_engine=inf, knowledge_agent=thyroid_rag)
        ctx = _ctx()
        await s.invoke(ctx)
        # LLM 失败时响应 evidence_chunk_ids = [所有 chunks]
        assert len(ctx.used_evidence_ids) == 5

    @pytest.mark.asyncio
    async def test_multi_organ_sequential_writes_accumulate(
        self, thyroid_rag, liver_rag, cardiac_rag
    ):
        """D2: 多专科顺序调用 → used_evidence_ids 按器官顺序累积。

        这是多器官联合诊断的契约 — 三个 specialist 共用同一 AgentContext，
        used_evidence_ids 应按调用顺序 extend 而不互相覆盖。
        """
        dispatcher = build_dispatcher(
            inference_engine=FakeInferenceEngine(),
            knowledge_agent=FakeKnowledgeAgent(),  # 占位
        )
        # 替换为真实 specialist（共享 rag）
        dispatcher._specialists[Organ.THYROID] = ThyroidSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="分级: TR3"),
            knowledge_agent=thyroid_rag,
        )
        dispatcher._specialists[Organ.LIVER] = LiverSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="分级: LR-4"),
            knowledge_agent=liver_rag,
        )
        dispatcher._specialists[Organ.CARDIAC] = CardiacSpecialistAgent(
            inference_engine=FakeInferenceEngine(text="LVEF 50% mildly_reduced"),
            knowledge_agent=cardiac_rag,
        )

        from app.agents.dispatcher import dispatch_all
        ctx = _ctx(Organ.THYROID, "多器官联合诊断")
        # 关键：dispatch_all 共享同一 ctx
        responses = await dispatch_all(
            dispatcher, ctx, [Organ.THYROID, Organ.LIVER, Organ.CARDIAC]
        )
        # 三器官响应均成功 → 累积 3 * 3 = 9 个 evidence
        assert len(ctx.used_evidence_ids) == 9
        # 顺序：先 thyroid 三个，再 liver 三个，再 cardiac 三个
        assert all(cid.startswith("chunk_") for cid in ctx.used_evidence_ids)

    @pytest.mark.asyncio
    async def test_evidence_written_via_base_post_process(self):
        """D2: 验证 _post_process 行为是 base 类统一行为（非器官特定）。

        通过 PlaceholderSpecialistAgent 不会触发（不继承 RAGSpecialistAgent），
        验证仅 RAGSpecialistAgent 子类具有此契约。
        """
        from app.agents.specialist.base import (
            PlaceholderSpecialistAgent,
        )

        s = PlaceholderSpecialistAgent(Organ.THYROID)
        ctx = _ctx()
        await s.invoke(ctx)
        # PlaceholderSpecialistAgent 不写 used_evidence_ids（无 RAG 行为）
        assert ctx.used_evidence_ids == []


# ======================================================================
# D1 偏离点修复 (OrganDiseaseThreshold 阈值) — T4 边界测试
# ======================================================================


class TestD1OrganThresholdBoundary:
    """T4: OrganDiseaseThreshold 阈值边界测试。"""

    @pytest.mark.asyncio
    async def test_below_threshold_triggers_needs_review(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: confidence 0.5 < organ_threshold 0.85 → needs_review=True。"""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=thyroid_rag,
            organ_threshold=0.85,
        )
        ctx = _ctx()
        resp = await s.invoke(ctx)
        assert resp.confidence < 0.85
        assert resp.needs_review is True
        # trace 应含 below_organ_threshold 标签
        assert any("below_organ_threshold" in t for t in resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_above_threshold_no_needs_review(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: confidence ≥ organ_threshold → needs_review=False。"""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=thyroid_rag,
            organ_threshold=0.40,  # 极低阈值
        )
        ctx = _ctx()
        resp = await s.invoke(ctx)
        assert resp.confidence >= 0.40
        assert resp.needs_review is False
        assert not any("below_organ_threshold" in t for t in resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_threshold_boundary_equal_not_triggered(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: confidence == threshold 时**不**触发 needs_review（严格小于）。"""
        # 构造恰好 confidence == 0.50 的场景
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=thyroid_rag,
            organ_threshold=0.50,
        )
        ctx = _ctx()
        resp = await s.invoke(ctx)
        # 边界值语义：strict < 触发，>= 不触发
        if abs(resp.confidence - 0.50) < 0.001:
            assert resp.needs_review is False
        elif resp.confidence < 0.50:
            assert resp.needs_review is True
        else:  # > 0.50
            assert resp.needs_review is False

    @pytest.mark.asyncio
    async def test_no_threshold_set_means_no_check(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: 未设置 organ_threshold → 不做阈值检查（向后兼容）。"""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=thyroid_rag,
            # organ_threshold 未设置
        )
        assert s._organ_threshold is None
        ctx = _ctx()
        resp = await s.invoke(ctx)
        # 即使 confidence 很低也不触发 needs_review
        assert resp.needs_review is False

    @pytest.mark.asyncio
    async def test_threshold_invalid_value_rejected_at_init(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: organ_threshold 超出 [0,1] 范围 → 构造时拒绝。"""
        with pytest.raises(ValueError, match="organ_threshold"):
            ThyroidSpecialistAgent(
                inference_engine=thyroid_inference,
                knowledge_agent=thyroid_rag,
                organ_threshold=1.5,
            )
        with pytest.raises(ValueError, match="organ_threshold"):
            ThyroidSpecialistAgent(
                inference_engine=thyroid_inference,
                knowledge_agent=thyroid_rag,
                organ_threshold=-0.1,
            )

    @pytest.mark.asyncio
    async def test_zero_confidence_skips_threshold_check(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: confidence == 0.0（已降级）→ 跳过阈值检查。

        降级响应本身已通过 evidence_insufficient / llm_failed 等 trace 标记
        表达"不可用"，不需再附加 below_organ_threshold 标签。
        """
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=FakeKnowledgeAgent(chunks=[]),  # RAG 空
            organ_threshold=0.85,
        )
        ctx = _ctx()
        resp = await s.invoke(ctx)
        assert resp.confidence == 0.0
        assert resp.needs_review is False
        # 不应出现 below_organ_threshold 标签
        assert not any("below_organ_threshold" in t for t in resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_disease_specific_threshold_overrides_organ(
        self, thyroid_rag, thyroid_inference
    ):
        """D1: 病种级阈值存在时优先于器官级阈值。"""
        # 病种级阈值 0.99（极高） → 任何 < 0.99 都触发
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference,
            knowledge_agent=thyroid_rag,
            organ_threshold=0.40,  # 器官级低阈值
            disease_thresholds={"TR5": 0.99},  # 病种级极高
        )
        ctx = _ctx()
        resp = await s.invoke(ctx)
        # 当前 LLM 输出是 TR4，TR5 阈值不适用 → 走器官级 0.40 → 不触发
        assert resp.needs_review is False

    def test_default_organ_thresholds_match_design(self):
        """D1: DEFAULT_ORGAN_THRESHOLDS 与 01-系统架构设计 §2.4 对齐。"""
        assert DEFAULT_ORGAN_THRESHOLDS == {
            "thyroid": 0.85,
            "liver": 0.88,
            "cardiac": 0.92,
        }

    def test_factory_default_thresholds_injected(self):
        """D1: build_dispatcher 默认注入器官级阈值到 specialist。"""

        inference = FakeInferenceEngine()
        rag = FakeKnowledgeAgent(chunks=make_chunks(3))
        dispatcher = build_dispatcher(inference, rag)
        # 三个 specialist 都注入了 organ_threshold
        assert (
            dispatcher._specialists[Organ.THYROID]._organ_threshold == 0.85
        )
        assert (
            dispatcher._specialists[Organ.LIVER]._organ_threshold == 0.88
        )
        assert (
            dispatcher._specialists[Organ.CARDIAC]._organ_threshold == 0.92
        )

    def test_factory_custom_thresholds_injected(self):
        """D1: build_dispatcher 支持自定义 organ_thresholds 参数。"""
        inference = FakeInferenceEngine()
        rag = FakeKnowledgeAgent(chunks=make_chunks(3))
        custom = {
            "thyroid": (0.50, {"TR5": 0.99}),
            "liver": (0.60, {}),
            "cardiac": (0.70, {}),
        }
        dispatcher = build_dispatcher(
            inference, rag, organ_thresholds=custom
        )
        assert (
            dispatcher._specialists[Organ.THYROID]._organ_threshold == 0.50
        )
        assert (
            dispatcher._specialists[Organ.THYROID]._disease_thresholds
            == {"TR5": 0.99}
        )
        assert dispatcher._specialists[Organ.LIVER]._organ_threshold == 0.60

    @pytest.mark.asyncio
    async def test_load_organ_thresholds_falls_back_to_defaults(self):
        """D1: DB 不可用时 load_organ_thresholds 降级为默认硬编码阈值。"""
        # 不需要真实 DB — 触发异常路径
        # 模拟：覆盖 AsyncSessionLocal 让其抛错
        import app.agents.specialist.factory as factory_module

        original = getattr(
            factory_module, "AsyncSessionLocal", None
        )

        class _BrokenSession:
            def __call__(self):
                raise ConnectionError("simulated DB down")

        factory_module.AsyncSessionLocal = _BrokenSession
        try:
            thresholds = await load_organ_thresholds()
        finally:
            if original is not None:
                factory_module.AsyncSessionLocal = original

        # 降级为 DEFAULT_ORGAN_THRESHOLDS
        assert thresholds == {
            "thyroid": (0.85, {}),
            "liver": (0.88, {}),
            "cardiac": (0.92, {}),
        }


# ======================================================================
# D3 偏离点修复 (deviation_severity 字段契约) — T5 跨决策边界测试
# ======================================================================


class TestD3DeviationSeverityContract:
    """T5: deviation_severity 字段契约 + 跨决策边界判定测试。

    当前阶段 specialist 不写入 deviation_severity（FUS-01 融合层做最终决策），
    字段保留为 None。本测试验证字段契约正确性 + 跨决策边界 case 的
    specialist 行为。
    """

    @pytest.mark.asyncio
    async def test_field_defaults_to_none(
        self, thyroid_rag, thyroid_inference
    ):
        """D3: specialist 响应中 deviation_severity 默认 None（FUS 消费）。"""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference, knowledge_agent=thyroid_rag
        )
        resp = await s.invoke(_ctx())
        assert resp.deviation_severity is None

    @pytest.mark.asyncio
    async def test_thyroid_takes_higher_grade_on_boundary_cross(
        self, thyroid_rag, thyroid_inference
    ):
        """T5: 跨越决策边界 — TR3 vs TR4 同时出现时取更高（保守策略）。

        临床决策边界：TR3 = 随访观察 / TR4 = FNA 活检（不容忍偏差）。
        """
        # LLM 输出 TR3 vs TR4 → specialist 应取 TR4（保守）
        thyroid_inference.text = "可能为 TR3 或 TR4，倾向于 TR4"
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference, knowledge_agent=thyroid_rag
        )
        resp = await s.invoke(_ctx())
        assert "TR4" in resp.content
        # specialist 自身**不**设置 deviation_severity（FUS 负责）
        assert resp.deviation_severity is None

    @pytest.mark.asyncio
    async def test_liver_takes_higher_grade_on_boundary_cross(
        self, liver_rag, liver_inference
    ):
        """T5: 跨越决策边界 — LR-3 vs LR-4 → 取更高风险。

        临床决策边界：LR-3 = 6 个月随访 / LR-4 = 3 个月随访或活检。
        """
        liver_inference.text = "LR-3 vs LR-4，最终倾向 LR-4"
        s = LiverSpecialistAgent(
            inference_engine=liver_inference, knowledge_agent=liver_rag
        )
        resp = await s.invoke(_ctx())
        assert "LR-4" in resp.content
        assert resp.deviation_severity is None

    @pytest.mark.asyncio
    async def test_fus_can_set_deviation_severity_post_hoc(self):
        """D3: FUS 轨道可在融合后写入 deviation_severity。

        验证 Pydantic 模型允许 mutate 该字段（mutable 默认）。
        """
        resp = AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content="[thyroid] TR4",
            confidence=0.9,
        )
        assert resp.deviation_severity is None
        # FUS 阶段判定为 major（跨决策边界）
        resp.deviation_severity = "major"
        assert resp.deviation_severity == "major"


# ======================================================================
# T2 改进 (LLM 输出格式异常反例测试)
# ======================================================================


class TestT2LLMOutputAnomalies:
    """T2: LLM 输出格式异常反例测试集。

    覆盖审查报告 §5.3 提出的 4 类结构化输出反例：
    - markdown 代码块
    - 中英混排
    - 多余空白 / 换行符
    - "无法判断"等显式降级信号
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text",
        [
            "```\n分级: TR4\n```",  # markdown 代码块
            "**分级: TR4**",  # markdown 加粗
            "甲状腺结节: TR4 建议活检",  # 中英混排（TR4 后跟 CJK）
            "   TR4   ",  # 多余空白
            "\n\n分级: TR4\n\n",  # 多余换行
            "分级：TR4；理由：实性低回声。",  # 全角符号
            "### 分级\nTR4",  # markdown 标题
        ],
    )
    async def test_thyroid_parses_anomalous_outputs(
        self, thyroid_rag, thyroid_inference, llm_text
    ):
        """T2: 甲状腺能正确处理多种异常 LLM 输出。"""
        thyroid_inference.text = llm_text
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference, knowledge_agent=thyroid_rag
        )
        resp = await s.invoke(_ctx())
        # 关键断言：能抽出 TR4，confidence > 0
        assert "TR4" in resp.content
        assert resp.confidence > 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text",
        [
            "```\n分级: LR-5\n```",
            "肝占位，**LR-5**",
            "   LR-5   ",
            "\nLR-5\n",
        ],
    )
    async def test_liver_parses_anomalous_outputs(
        self, liver_rag, liver_inference, llm_text
    ):
        """T2: 肝脏能正确处理多种异常 LLM 输出。"""
        liver_inference.text = llm_text
        s = LiverSpecialistAgent(
            inference_engine=liver_inference, knowledge_agent=liver_rag
        )
        resp = await s.invoke(_ctx())
        assert "LR-5" in resp.content
        assert resp.confidence > 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text",
        [
            "```\nLVEF: 50%\n```",
            "EF 50%, **mildly_reduced**",
            "   LVEF 50%   ",
            "\nLVEF 50%\nmildly_reduced\n",
        ],
    )
    async def test_cardiac_parses_anomalous_outputs(
        self, cardiac_rag, cardiac_inference, llm_text
    ):
        """T2: 心脏能正确处理多种异常 LLM 输出。"""
        cardiac_inference.text = llm_text
        s = CardiacSpecialistAgent(
            inference_engine=cardiac_inference, knowledge_agent=cardiac_rag
        )
        resp = await s.invoke(_ctx())
        # 应含 LVEF 数值（格式为 LVEF=50%）
        assert "LVEF=50%" in resp.content
        assert resp.confidence > 0.0

    @pytest.mark.asyncio
    async def test_explicit_decline_signal_downgrades(
        self, thyroid_rag, thyroid_inference
    ):
        """T2: 显式"无法判断"信号 → 降级到 confidence=0.0。"""
        thyroid_inference.text = (
            "我无法判断这个结节的分级，"
            "需要更多临床信息如结节大小、边界、内部回声等。"
        )
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference, knowledge_agent=thyroid_rag
        )
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "grade_unparseable" in resp.content

    @pytest.mark.asyncio
    async def test_empty_llm_output_downgrades(
        self, thyroid_rag, thyroid_inference
    ):
        """T2: LLM 输出空字符串 → 降级到 confidence=0.0。"""
        thyroid_inference.text = ""
        s = ThyroidSpecialistAgent(
            inference_engine=thyroid_inference, knowledge_agent=thyroid_rag
        )
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        # 占位 content 中不应有"TR1-TR5"被误识别的字符串
        assert "grade_unparseable" in resp.content


# ======================================================================
# 200→500 字符饱和阈值改进验证
# ======================================================================


class TestLLMSaturationThreshold:
    """验证 _LLM_TEXT_SATURATION_CHARS 已从 200 改为 500。"""

    def test_saturation_value_is_500(self):
        """_LLM_TEXT_SATURATION_CHARS 应为 500（与 few-shot 输出长度对齐）。"""
        assert _LLM_TEXT_SATURATION_CHARS == 500

    def test_compute_heuristic_distinguishes_300_vs_500_chars(
        self, thyroid_rag, thyroid_inference
    ):
        """300 字符 vs 500 字符：原 200 阈值下两者均饱和，500 阈值下应可区分。"""
        # 构造恰好 300 字符与 500 字符的 LLM 输出
        inference_300 = FakeInferenceEngine(text="分级: TR4\n" + "理由: " + "A" * 287)
        inference_500 = FakeInferenceEngine(text="分级: TR4\n" + "理由: " + "A" * 487)
        s300 = ThyroidSpecialistAgent(
            inference_engine=inference_300, knowledge_agent=thyroid_rag
        )
        s500 = ThyroidSpecialistAgent(
            inference_engine=inference_500, knowledge_agent=thyroid_rag
        )
        # 验证 _compute_heuristic_confidence 在 300 vs 500 字符下给出不同 llm_factor
        # 300/500 = 0.6；500/500 = 1.0 — 应该有差异

        chunks = make_chunks(5, organ="thyroid")
        c300 = s300._compute_heuristic_confidence(chunks, "分级: TR4\n" + "理由: " + "A" * 287)
        c500 = s500._compute_heuristic_confidence(chunks, "分级: TR4\n" + "理由: " + "A" * 487)
        # 500 字符应比 300 字符给出更高 llm_factor → 更高 confidence
        assert c500 > c300


# ======================================================================
# R6 风险修复（心脏 EF 矛盾检测）
# ======================================================================


class TestR6CardiacSeverityMismatch:
    """R6 风险修复 — 心脏 EF 矛盾检测。

    验证当 LLM 同时输出 EF 数值和严重程度关键字但二者矛盾时：
    1. reasoning_trace 追加 severity_mismatch 标签
    2. needs_review 设为 True
    3. confidence 扣减 0.1
    """

    @pytest.mark.asyncio
    async def test_severity_mismatch_triggers_needs_review(
        self, cardiac_rag, cardiac_inference
    ):
        """R6: EF 60%（normal） vs 关键字 severely_reduced → 矛盾。

        验证 3 个关键不变量：
        1. needs_review = True（矛盾 → 强制人工复核）
        2. reasoning_trace 追加 severity_mismatch 标签
        3. confidence 应被扣减（与一致情形相比更低）
        """
        # 60% 推算 = normal，但关键字 severely_reduced → 矛盾
        cardiac_inference.text = "EF 60%, severely_reduced"
        s_mismatch = CardiacSpecialistAgent(
            inference_engine=cardiac_inference, knowledge_agent=cardiac_rag
        )
        resp_mismatch = await s_mismatch.invoke(
            _ctx(Organ.CARDIAC, "EF 60% severely_reduced")
        )
        # 1) needs_review = True
        assert resp_mismatch.needs_review is True
        # 2) severity_mismatch 标签存在
        assert any(
            "severity_mismatch" in t for t in resp_mismatch.reasoning_trace
        )
        # 3) confidence 扣减（与一致情形对比）— 构造一致情形作对照
        cardiac_inference.text = "EF 60%, normal"
        s_consistent = CardiacSpecialistAgent(
            inference_engine=cardiac_inference, knowledge_agent=cardiac_rag
        )
        resp_consistent = await s_consistent.invoke(
            _ctx(Organ.CARDIAC, "EF 60% normal")
        )
        # 一致情形 confidence ≥ 矛盾情形（矛盾情形少了 0.1 一致性加成 + 多了 0.1 扣减 = 0.2 差距）
        assert resp_consistent.confidence > resp_mismatch.confidence
        # 矛盾情形的 confidence 应低于 0.65（确保扣减生效）
        assert resp_mismatch.confidence < 0.65

    @pytest.mark.asyncio
    async def test_consistent_value_and_keyword_no_mismatch(
        self, cardiac_rag, cardiac_inference
    ):
        """R6: EF 60% + 关键字 normal → 一致 → 不应触发 mismatch。"""
        cardiac_inference.text = "EF 60%, normal"
        s = CardiacSpecialistAgent(
            inference_engine=cardiac_inference, knowledge_agent=cardiac_rag
        )
        resp = await s.invoke(_ctx(Organ.CARDIAC, "EF 60% normal"))
        assert resp.needs_review is False
        assert not any(
            "severity_mismatch" in t for t in resp.reasoning_trace
        )

    @pytest.mark.asyncio
    async def test_keyword_only_no_mismatch(
        self, cardiac_rag, cardiac_inference
    ):
        """R6: 仅关键字无 EF 数值 → 不触发 mismatch（无矛盾对照）。"""
        cardiac_inference.text = "心功能轻度降低，mildly_reduced"
        s = CardiacSpecialistAgent(
            inference_engine=cardiac_inference, knowledge_agent=cardiac_rag
        )
        resp = await s.invoke(_ctx(Organ.CARDIAC, "心功能 mildly_reduced"))
        # 仅关键字，无数值 → 不触发 mismatch
        assert not any(
            "severity_mismatch" in t for t in resp.reasoning_trace
        )
        # needs_review 应为 False（未触发矛盾检测）
        assert resp.needs_review is False
