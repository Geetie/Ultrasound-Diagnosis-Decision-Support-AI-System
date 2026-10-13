"""Unit tests for app.agents.specialist.liver (T-GA-AGT-05).

覆盖：
- 核心 LI-RADS 等级（LR-1 ~ LR-5 / LR-M / LR-TIV）解析
- "rim APHE" → LR-M 路径
- 多个分级码取风险最高（LR-5 > LR-TIV > LR-M > LR-4 > LR-3 > LR-2 > LR-1）
- 边界：chunks 为空 / LLM 失败 / 分级码缺失
- RAG + LLM 调用参数
- 置信度（含风险权重加成）
- 证据 chunk 选择
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.protocols import AgentContext
from app.agents.specialist.liver import (
    LI_RADS_LEVELS,
    LI_RADS_RISK_WEIGHT,
    LiverSpecialistAgent,
)
from app.inference.protocols import InferenceError, InferenceErrorCode
from app.models.schemas import Organ
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)


def _ctx(query: str = "肝右叶 2.5cm 占位") -> AgentContext:
    return AgentContext(
        request_id="r1",
        session_id="s1",
        organ=Organ.LIVER,
        user_query=query,
    )


# ----------------------------------------------------------------------
# Fixture
# ----------------------------------------------------------------------


@pytest.fixture
def rag():
    return FakeKnowledgeAgent(chunks=make_chunks(5, organ="liver"))


@pytest.fixture
def inference():
    return FakeInferenceEngine(text="分级: LR-5\n理由: APHE+廓清+包膜+阈值增长")


@pytest.fixture
def specialist(rag, inference):
    return LiverSpecialistAgent(inference_engine=inference, knowledge_agent=rag)


# ----------------------------------------------------------------------
# 1) 各等级解析
# ----------------------------------------------------------------------


class TestGradeExtraction:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text,expected_grade",
        [
            ("分级: LR-1", "LR-1"),
            ("分级: LR-2", "LR-2"),
            ("分级: LR-3", "LR-3"),
            ("分级: LR-4", "LR-4"),
            ("分级: LR-5", "LR-5"),
            ("分级: LR-M", "LR-M"),
            ("分级: LR-TIV", "LR-TIV"),
        ],
    )
    async def test_all_main_grades(self, rag, inference, llm_text, expected_grade):
        inference.text = llm_text
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert expected_grade in resp.content
        assert resp.confidence > 0.0
        assert resp.organ == Organ.LIVER

    @pytest.mark.asyncio
    async def test_lr_m_extraction(self, rag, inference):
        inference.text = "考虑 LR-M，rim APHE 明显，建议活检"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "LR-M" in resp.content

    @pytest.mark.asyncio
    async def test_lr_tiv_extraction(self, rag, inference):
        inference.text = "门静脉主干受侵，LR-TIV"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "LR-TIV" in resp.content

    @pytest.mark.asyncio
    async def test_lowercase_lr_form(self, rag, inference):
        inference.text = "lr-4 中度可疑"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "LR-4" in resp.content


# ----------------------------------------------------------------------
# 2) 多分级取风险最高
# ----------------------------------------------------------------------


class TestMultipleGrades:
    @pytest.mark.asyncio
    async def test_lr4_vs_lr5_takes_lr5(self, rag, inference):
        inference.text = "LR-4 或 LR-5"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        # LR-5 风险权重 0.85 > LR-4 0.50
        assert "LR-5" in resp.content

    @pytest.mark.asyncio
    async def test_lr3_lr4_lr5_takes_lr5(self, rag, inference):
        inference.text = "鉴别 LR-3 / LR-4 / LR-5"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "LR-5" in resp.content

    @pytest.mark.asyncio
    async def test_lr_m_vs_lr5_takes_lr5(self, rag, inference):
        # LR-5 (0.85) > LR-M (0.65)
        inference.text = "LR-M 还是 LR-5"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "LR-5" in resp.content

    @pytest.mark.asyncio
    async def test_lr_tiv_vs_lr5_takes_lr5(self, rag, inference):
        # LR-5 (0.95) > LR-TIV (0.85) → LR-5 是更明确的 HCC 风险结论
        inference.text = "LR-5 还是 LR-TIV"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "LR-5" in resp.content


# ----------------------------------------------------------------------
# 3) 降级路径
# ----------------------------------------------------------------------


class TestDegradation:
    @pytest.mark.asyncio
    async def test_rag_returns_empty(self, inference):
        rag = FakeKnowledgeAgent(chunks=[])
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        # A.59：正文中文可读 + 必须转人工（英文技术串只留 trace）
        assert "本次未检索到与检查部位相关的知识条目" in resp.content
        assert resp.needs_review is True
        assert "evidence_insufficient" in " ".join(resp.reasoning_trace)
        assert inference.generate_call_count == 0

    @pytest.mark.asyncio
    async def test_rag_raises_exception(self, inference):
        rag = FakeKnowledgeAgent(raise_exc=ConnectionError("rag down"))
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "rag_failed" in str(resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_llm_raises(self, rag):
        inference = FakeInferenceEngine(
            raise_exc=InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="model error",
                retryable=False,
            )
        )
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "llm_failed" in str(resp.reasoning_trace)
        # 证据 chunk ID 仍被填充
        assert len(resp.evidence_chunk_ids) == 5

    @pytest.mark.asyncio
    async def test_llm_output_unparseable(self, rag, inference):
        inference.text = "无法判断，建议进一步检查。"
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        # 可读降级（2026-10-13）：技术串不得进医生可见正文
        assert "未能解析出" in resp.content
        assert "grade_unparseable" not in resp.content
        assert resp.needs_review is True
        assert any(t.startswith("grading_unparseable") for t in resp.reasoning_trace)


# ----------------------------------------------------------------------
# 4) RAG + LLM 调用参数
# ----------------------------------------------------------------------


class TestInvocationParams:
    @pytest.mark.asyncio
    async def test_rag_called_with_liver_organ(self, specialist, rag):
        await specialist.invoke(_ctx())
        assert rag.query_call_count == 1
        assert rag.last_organ == Organ.LIVER
        assert rag.last_top_k == 5

    @pytest.mark.asyncio
    async def test_llm_prompt_contains_liver_content(self, specialist, inference):
        await specialist.invoke(_ctx())
        req = inference.last_request
        # prompt 应包含 liver.md 提示词 + chunk
        assert "LI-RADS" in req.prompt
        assert "chunk content" in req.prompt


# ----------------------------------------------------------------------
# 5) 置信度
# ----------------------------------------------------------------------


class TestConfidence:
    @pytest.mark.asyncio
    async def test_lr5_higher_confidence_than_lr3(self, rag, inference):
        # 同等 RAG 证据下，LR-5 比 LR-3 置信度高（风险权重加成）
        inference.text = "分级: LR-5"
        s1 = LiverSpecialistAgent(inference, rag)
        resp1 = await s1.invoke(_ctx())

        inference.text = "分级: LR-3"
        s2 = LiverSpecialistAgent(inference, rag)
        resp2 = await s2.invoke(_ctx())

        assert resp1.confidence > resp2.confidence

    @pytest.mark.asyncio
    async def test_confidence_capped_below_1_for_high_risk(self, rag, inference):
        # LR-5 不应让 confidence 超过 0.95（保守策略）
        inference.text = "分级: LR-5\n" + ("详细分析 " * 100)
        s = LiverSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence <= 0.95

    @pytest.mark.asyncio
    async def test_confidence_in_range(self, specialist):
        resp = await specialist.invoke(_ctx())
        assert 0.0 <= resp.confidence <= 1.0


# ----------------------------------------------------------------------
# 6) 证据 chunk
# ----------------------------------------------------------------------


class TestEvidence:
    @pytest.mark.asyncio
    async def test_top_3_evidence_chunks(self, specialist):
        resp = await specialist.invoke(_ctx())
        assert len(resp.evidence_chunk_ids) == 3
        assert resp.evidence_chunk_ids == ["chunk_0", "chunk_1", "chunk_2"]


# ----------------------------------------------------------------------
# 7) Capability tags
# ----------------------------------------------------------------------


class TestCapabilityTags:
    def test_tags(self, specialist):
        tags = specialist.get_capability_tags()
        assert "liver" in tags
        assert "specialist" in tags
        assert "grading_li-rads" in tags


# ----------------------------------------------------------------------
# 8) Prompt 加载
# ----------------------------------------------------------------------


class TestPromptLoading:
    def test_loads_liver_prompt(self, specialist):
        assert "LI-RADS" in specialist._prompt_template
        assert "APHE" in specialist._prompt_template

    def test_empty_prompt_rejected(self, rag, inference):
        with pytest.raises(ValueError, match="prompt template is empty"):
            LiverSpecialistAgent(inference, rag, prompt_template="")


# ----------------------------------------------------------------------
# 9) 风险权重表完整性
# ----------------------------------------------------------------------


class TestRiskWeights:
    def test_all_levels_have_weights(self):
        for level in LI_RADS_LEVELS:
            assert level in LI_RADS_RISK_WEIGHT
            assert 0.0 <= LI_RADS_RISK_WEIGHT[level] <= 1.0

    def test_ordering(self):
        # 风险排序：LR-5 > LR-TIV > LR-M > LR-4 > LR-3 > LR-2 > LR-1
        assert LI_RADS_RISK_WEIGHT["LR-5"] > LI_RADS_RISK_WEIGHT["LR-TIV"]
        assert LI_RADS_RISK_WEIGHT["LR-TIV"] > LI_RADS_RISK_WEIGHT["LR-M"]
        assert LI_RADS_RISK_WEIGHT["LR-M"] > LI_RADS_RISK_WEIGHT["LR-4"]
        assert LI_RADS_RISK_WEIGHT["LR-4"] > LI_RADS_RISK_WEIGHT["LR-3"]
        assert LI_RADS_RISK_WEIGHT["LR-3"] > LI_RADS_RISK_WEIGHT["LR-2"]
        assert LI_RADS_RISK_WEIGHT["LR-2"] > LI_RADS_RISK_WEIGHT["LR-1"]
