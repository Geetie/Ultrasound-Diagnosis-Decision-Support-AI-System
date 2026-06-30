"""Unit tests for app.agents.specialist.thyroid (T-GA-AGT-04).

覆盖：
- 5 个 TI-RADS 等级（TR1-TR5）解析
- 简化 TR 形式 vs 完整 TI-RADS 形式
- 多个分级码取最高（保守）
- 评分 + 等级一致性加成
- 边界：chunks 为空 / LLM 失败 / 分级码缺失
- RAG + LLM 调用次数
- 置信度范围
- 证据 chunk ID 选择
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.protocols import AgentContext
from app.agents.specialist.thyroid import (
    TI_RADS_LEVELS,
    ThyroidSpecialistAgent,
)
from app.inference.protocols import InferenceError, InferenceErrorCode
from app.models.schemas import Organ
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)


def _ctx(organ: Organ = Organ.THYROID, query: str = "甲状腺结节 8mm") -> AgentContext:
    return AgentContext(
        request_id="r1",
        session_id="s1",
        organ=organ,
        user_query=query,
    )


# ----------------------------------------------------------------------
# 构造 fixture（参数化）
# ----------------------------------------------------------------------


@pytest.fixture
def rag():
    return FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))


@pytest.fixture
def inference():
    return FakeInferenceEngine(text="分级: TR4\n理由: 实性 + 低回声")


@pytest.fixture
def specialist(rag, inference):
    return ThyroidSpecialistAgent(inference_engine=inference, knowledge_agent=rag)


# ----------------------------------------------------------------------
# 1) 5 等级解析
# ----------------------------------------------------------------------


class TestGradeExtraction:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text,expected_grade",
        [
            ("分级: TR1", "TR1"),
            ("分级: TR2", "TR2"),
            ("分级: TR3", "TR3"),
            ("分级: TR4", "TR4"),
            ("分级: TR5", "TR5"),
        ],
    )
    async def test_simple_tr_form_all_levels(
        self, rag, inference, llm_text, expected_grade
    ):
        inference.text = llm_text
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert expected_grade in resp.content
        assert resp.confidence > 0.0
        assert resp.organ == Organ.THYROID

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text,expected_grade",
        [
            ("TIRADS 1 - 良性", "TR1"),
            ("TI-RADS 2 - 不怀疑恶性", "TR2"),
            ("TIRADS: 3 - 低度可疑", "TR3"),
            ("TI-RADS: 4 - 中度可疑", "TR4"),
            ("TIRADS 5 高度可疑", "TR5"),
        ],
    )
    async def test_full_ti_rads_form(
        self, rag, inference, llm_text, expected_grade
    ):
        inference.text = llm_text
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert expected_grade in resp.content

    @pytest.mark.asyncio
    async def test_lowercase_tr_form(self, rag, inference):
        inference.text = "推荐分级：tr3"
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "TR3" in resp.content


# ----------------------------------------------------------------------
# 2) 多分级码取最高
# ----------------------------------------------------------------------


class TestMultipleGrades:
    @pytest.mark.asyncio
    async def test_two_grades_takes_higher(self, rag, inference):
        inference.text = "可能为 TR3 或 TR4，倾向于 TR4"
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        # 多分级时取最高（数字最大）
        assert "TR4" in resp.content
        assert "TR3" not in resp.content.replace("TR4", "", 1)

    @pytest.mark.asyncio
    async def test_three_grades_takes_higher(self, rag, inference):
        inference.text = "鉴别诊断：TR2 vs TR3 vs TR4"
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "TR4" in resp.content


# ----------------------------------------------------------------------
# 3) 评分 + 等级一致性
# ----------------------------------------------------------------------


class TestScoreConsistency:
    @pytest.mark.asyncio
    async def test_score_matches_grade_adds_bonus(self, rag, inference):
        # TR4 期望总分 4-6，给 5 分
        inference.text = "分级: TR4\n总分: 5\n理由: 实性 + 低回声"
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "TR4" in resp.content
        # 评分加成 → confidence 至少达到 0.65
        assert resp.confidence >= 0.65

    @pytest.mark.asyncio
    async def test_score_mismatch_no_bonus(self, rag, inference):
        # TR3 期望总分 3，给 8 分（不一致）
        inference.text = "分级: TR3\n总分: 8\n理由: 高度可疑"
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "TR3" in resp.content
        # 评分不一致，无加成 — confidence 应低于有加成情况
        # （启发式 + TR4 简化形式 0.1）
        assert resp.confidence < 0.95


# ----------------------------------------------------------------------
# 4) 降级路径
# ----------------------------------------------------------------------


class TestDegradation:
    @pytest.mark.asyncio
    async def test_rag_returns_empty(self, inference):
        rag = FakeKnowledgeAgent(chunks=[])
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "evidence_insufficient" in resp.content
        # LLM 不应被调用（RAG 空 → 直接降级）
        assert inference.generate_call_count == 0

    @pytest.mark.asyncio
    async def test_rag_raises_exception(self, inference):
        rag = FakeKnowledgeAgent(raise_exc=ConnectionError("rag down"))
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "rag_failed" in str(resp.reasoning_trace)
        # LLM 不应被调用
        assert inference.generate_call_count == 0

    @pytest.mark.asyncio
    async def test_llm_raises(self, rag):
        inference = FakeInferenceEngine(
            raise_exc=InferenceError(
                error_code=InferenceErrorCode.TIMEOUT,
                message="timeout",
                retryable=True,
            )
        )
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "llm_failed" in str(resp.reasoning_trace)
        assert "InferenceError" in str(resp.reasoning_trace)
        # 证据 chunk ID 仍应被填充（即使 LLM 失败）
        assert len(resp.evidence_chunk_ids) == 5

    @pytest.mark.asyncio
    async def test_llm_output_unparseable(self, rag, inference):
        inference.text = "我无法判断这个结节的分级，需要更多信息。"
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "grade_unparseable" in resp.content
        # 占位 content 中没有 LLM 抽出的独立 TR1-TR5 分级码
        # （content 里的 "(one of TR1..TR5)" 是范围说明，不算有效分级）
        import re as _re
        standalone = _re.findall(r"\bTR[1-5]\b", resp.content)
        assert standalone == [], f"expected no extracted grade, got {standalone}"


# ----------------------------------------------------------------------
# 5) RAG + LLM 调用次数 & 参数
# ----------------------------------------------------------------------


class TestInvocationCount:
    @pytest.mark.asyncio
    async def test_rag_called_once(self, specialist, rag, inference):
        await specialist.invoke(_ctx(query="甲状腺 6mm 结节"))
        assert rag.query_call_count == 1
        assert rag.last_organ == Organ.THYROID
        assert rag.last_top_k == 5  # DEFAULT_RAG_TOP_K
        assert rag.last_query == "甲状腺 6mm 结节"

    @pytest.mark.asyncio
    async def test_llm_called_once(self, specialist, rag, inference):
        await specialist.invoke(_ctx())
        assert inference.generate_call_count == 1
        # InferenceRequest 参数
        req = inference.last_request
        assert req.max_tokens == 1024
        assert req.request_id == "r1"
        assert req.session_id == "s1"
        # prompt 应包含 thyroid.md 内容和知识 chunk
        assert "TR" in req.prompt  # 提示词模板
        assert "chunk content" in req.prompt  # 至少 1 个 chunk

    @pytest.mark.asyncio
    async def test_no_llm_call_when_rag_empty(self, inference):
        rag = FakeKnowledgeAgent(chunks=[])
        s = ThyroidSpecialistAgent(inference, rag)
        await s.invoke(_ctx())
        assert inference.generate_call_count == 0


# ----------------------------------------------------------------------
# 6) 置信度
# ----------------------------------------------------------------------


class TestConfidence:
    @pytest.mark.asyncio
    async def test_confidence_in_range(self, specialist):
        resp = await specialist.invoke(_ctx())
        assert 0.0 <= resp.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_confidence_capped_at_1(self, rag, inference):
        # 给一个超长 LLM 输出 + 高评分加成 → 应被 cap 在 1.0
        long_text = "分级: TI-RADS 4\n总分: 6\n理由: " + ("A" * 500)
        inference.text = long_text
        s = ThyroidSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence <= 1.0


# ----------------------------------------------------------------------
# 7) 证据 chunk 选择
# ----------------------------------------------------------------------


class TestEvidence:
    @pytest.mark.asyncio
    async def test_evidence_top_3_chunks(self, specialist):
        resp = await specialist.invoke(_ctx())
        # _parse_response 选择 top-3 证据
        assert len(resp.evidence_chunk_ids) == 3
        assert resp.evidence_chunk_ids == ["chunk_0", "chunk_1", "chunk_2"]


# ----------------------------------------------------------------------
# 8) Capability tags
# ----------------------------------------------------------------------


class TestCapabilityTags:
    def test_tags_include_organ_and_grading(self, specialist):
        tags = specialist.get_capability_tags()
        assert "thyroid" in tags
        assert "specialist" in tags
        assert "grading_ti-rads" in tags


# ----------------------------------------------------------------------
# 9) Prompt 模板加载
# ----------------------------------------------------------------------


class TestPromptLoading:
    def test_loads_default_prompt(self, specialist):
        # 默认从 app/agents/prompts/thyroid.md 加载
        assert specialist._prompt_template
        assert "TI-RADS" in specialist._prompt_template
        assert "ACR" in specialist._prompt_template or "tr" in specialist._prompt_template.lower()

    def test_custom_prompt_overrides_default(self, rag, inference):
        custom = "# Custom\nSpecialist prompt"
        s = ThyroidSpecialistAgent(inference, rag, prompt_template=custom)
        assert s._prompt_template == custom

    def test_empty_prompt_rejected(self, rag, inference):
        with pytest.raises(ValueError, match="prompt template is empty"):
            ThyroidSpecialistAgent(inference, rag, prompt_template="   ")
