"""Unit tests for app.agents.specialist.cardiac (T-GA-AGT-06).

覆盖：
- 4 档 LVEF 严重程度（normal / mildly_reduced / moderately_reduced / severely_reduced）
- 数值 → 严重程度 推算（≥55 / 45-54 / 30-44 / <30）
- 关键字抽取（备选路径）
- 数值 + 关键字一致性加成
- 多个 LVEF 数值取最低（保守）
- 边界：chunks 为空 / LLM 失败 / 数值缺失
- RAG + LLM 调用参数
- 置信度
- 证据 chunk 选择
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.protocols import AgentContext
from app.agents.specialist.cardiac import (
    LVEF_SEVERITY_LEVELS,
    CardiacSpecialistAgent,
    _lvef_to_severity,
)
from app.inference.protocols import InferenceError, InferenceErrorCode
from app.models.schemas import Organ
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)


def _ctx(query: str = "心脏超声：EF 50%") -> AgentContext:
    return AgentContext(
        request_id="r1",
        session_id="s1",
        organ=Organ.CARDIAC,
        user_query=query,
    )


# ----------------------------------------------------------------------
# Fixture
# ----------------------------------------------------------------------


@pytest.fixture
def rag():
    return FakeKnowledgeAgent(chunks=make_chunks(5, organ="cardiac"))


@pytest.fixture
def inference():
    return FakeInferenceEngine(text="LVEF: 50%\n分级: mildly_reduced\n理由: FS 25%")


@pytest.fixture
def specialist(rag, inference):
    return CardiacSpecialistAgent(inference_engine=inference, knowledge_agent=rag)


# ----------------------------------------------------------------------
# 1) ASE 2015 严重程度边界
# ----------------------------------------------------------------------


class TestLvefToSeverity:
    @pytest.mark.parametrize(
        "lvef,expected",
        [
            (75.0, "normal"),
            (60.0, "normal"),
            (55.0, "normal"),     # 边界
            (54.9, "mildly_reduced"),
            (50.0, "mildly_reduced"),
            (45.0, "mildly_reduced"),  # 边界
            (44.9, "moderately_reduced"),
            (40.0, "moderately_reduced"),
            (30.0, "moderately_reduced"),  # 边界
            (29.9, "severely_reduced"),
            (20.0, "severely_reduced"),
            (0.0, "severely_reduced"),
        ],
    )
    def test_severity_boundaries(self, lvef, expected):
        assert _lvef_to_severity(lvef) == expected


# ----------------------------------------------------------------------
# 2) 数值抽取
# ----------------------------------------------------------------------


class TestLvefNumberExtraction:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text,expected_value",
        [
            ("LVEF: 60%", 60.0),
            ("LVEF 65 %", 65.0),
            ("EF: 50%", 50.0),
            ("射血分数 38%", 38.0),
            ("EF值 = 22%", 22.0),
        ],
    )
    async def test_various_lvef_formats(self, rag, inference, llm_text, expected_value):
        inference.text = llm_text
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        # A.59 可读化：正文里的数值形态固定为「（LVEF）62%」（英文键值对不进正文），
        # 该形态同时是 `consistency._extract_lvef` 的解析前提（正则已容许中文右括号）
        assert f"（LVEF）{expected_value:g}%" in resp.content
        assert "LVEF=" not in resp.content

    @pytest.mark.asyncio
    async def test_multiple_lvef_takes_minimum(self, rag, inference):
        # 多个 LVEF 数值取最低（保守）
        inference.text = "EF 50% 或 55%"
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        # 50% → mildly_reduced（机器值进 trace；正文只给中文）
        assert "（LVEF）50%" in resp.content
        assert "轻度降低" in resp.content
        assert "severity -> mildly_reduced" in resp.reasoning_trace


# ----------------------------------------------------------------------
# 3) 关键字抽取（备选路径）
# ----------------------------------------------------------------------


class TestSeverityKeywordExtraction:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text,expected",
        [
            ("正常", "normal"),
            ("normal", "normal"),
            ("轻度降低", "mildly_reduced"),
            ("mildly reduced", "mildly_reduced"),
            ("中度降低", "moderately_reduced"),
            ("moderately_reduced", "moderately_reduced"),
            ("重度降低", "severely_reduced"),
            ("severely reduced", "severely_reduced"),
        ],
    )
    async def test_keyword_extraction(self, rag, inference, llm_text, expected):
        inference.text = llm_text
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        # A.59：机器值（英文枚举）移出医生可见正文 → 断言看 trace，正文断言中文标签
        assert f"severity -> {expected}" in resp.reasoning_trace
        assert expected not in resp.content


# ----------------------------------------------------------------------
# 4) 数值 + 关键字一致性加成
# ----------------------------------------------------------------------


class TestConsistencyBonus:
    @pytest.mark.asyncio
    async def test_consistent_value_and_keyword(self, rag, inference):
        # 数值 50% → mildly_reduced（计算）
        # 关键字 mildly_reduced（抽取）
        # → 一致，confidence 应较高
        inference.text = "EF 50%, mildly_reduced"
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "（LVEF）50%" in resp.content
        assert "轻度降低" in resp.content
        assert "severity -> mildly_reduced" in resp.reasoning_trace
        # 一致性加成 → score_bonus 至少 0.1（数值）+ 0.1（一致性）= 0.2
        assert resp.confidence >= 0.6

    @pytest.mark.asyncio
    async def test_inconsistent_value_and_keyword(self, rag, inference):
        # 数值 60% → normal（计算）
        # 关键字 severely_reduced（抽取 — 与数值矛盾）
        # → 不一致，无一致性加成
        inference.text = "EF 60%, severely_reduced"
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert "（LVEF）60%" in resp.content
        # 关键字优先级高于数值推算？让我们看看实现：
        # 实际实现：severity 优先用关键字（如果匹配），数值不交叉校验
        # 关键字抽取得到 severely_reduced（A.59：机器值在 trace，正文是中文标签）
        assert "重度降低" in resp.content
        assert "severity -> severely_reduced" in resp.reasoning_trace


# ----------------------------------------------------------------------
# 5) 降级路径
# ----------------------------------------------------------------------


class TestDegradation:
    @pytest.mark.asyncio
    async def test_rag_returns_empty(self, inference):
        rag = FakeKnowledgeAgent(chunks=[])
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        # A.59：正文给可读中文（英文技术串只留在 trace），并**必须转人工**（不许静默降级）
        assert "本次未检索到与检查部位相关的知识条目" in resp.content
        assert resp.needs_review is True
        assert "evidence_insufficient" in " ".join(resp.reasoning_trace)
        assert inference.generate_call_count == 0

    @pytest.mark.asyncio
    async def test_rag_raises_exception(self, inference):
        rag = FakeKnowledgeAgent(raise_exc=ConnectionError("rag down"))
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "rag_failed" in str(resp.reasoning_trace)

    @pytest.mark.asyncio
    async def test_llm_raises(self, rag):
        inference = FakeInferenceEngine(
            raise_exc=InferenceError(
                error_code=InferenceErrorCode.CONNECTION_FAILED,
                message="connection failed",
                retryable=True,
            )
        )
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        assert "llm_failed" in str(resp.reasoning_trace)
        # 证据 chunk ID 仍被填充
        assert len(resp.evidence_chunk_ids) == 5

    @pytest.mark.asyncio
    async def test_llm_no_lvef_no_keyword(self, rag, inference):
        # LLM 输出完全没有 LVEF / 严重程度信息
        inference.text = "心脏超声未见明显异常。"
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence == 0.0
        # 可读降级（2026-10-13）：技术串不得进医生可见正文
        assert "未取得可判定的左室射血分数" in resp.content
        assert "lvef_unparseable" not in resp.content
        assert resp.needs_review is True
        assert any(t.startswith("grading_unparseable") for t in resp.reasoning_trace)

    async def test_降级文案给出可行动指引且不含工程视角(self, rag, inference):
        """A.62 批 1：医生要看到「我该做什么」，而不是「AI 输出没给出什么」。

        真机证据：近端 30 条样本里 10 条降级、其中 9 条是心脏，正文全是
        「LVEF: 无法计算 分级: 无法判断」—— 医生看不出该补测还是该人工判读。
        """
        inference.text = "心脏超声未见明显异常。"
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())

        assert "补填" in resp.content, "要告诉医生已有测量值可以补填"
        # 补填写法必须是下游抽得到的形态（`LVEF 55%`）——A.59 踩过「（LVEF）55%」抽不到
        assert "LVEF 55%" in resp.content
        assert "已转人工判读" in resp.content, "无测量结果时仍要走转人工口径"
        # 不再把工程视角（AI 输出/解析）甩给医生
        assert "AI 输出" not in resp.content
        assert "未能解析" not in resp.content


# ----------------------------------------------------------------------
# 6) RAG + LLM 调用参数
# ----------------------------------------------------------------------


class TestInvocationParams:
    @pytest.mark.asyncio
    async def test_rag_called_with_cardiac_organ(self, specialist, rag):
        await specialist.invoke(_ctx())
        assert rag.query_call_count == 1
        assert rag.last_organ == Organ.CARDIAC
        assert rag.last_top_k == 5

    @pytest.mark.asyncio
    async def test_llm_prompt_contains_cardiac_content(self, specialist, inference):
        await specialist.invoke(_ctx())
        req = inference.last_request
        # prompt 应包含 cardiac.md 提示词 + chunk
        assert "LVEF" in req.prompt or "ASE" in req.prompt
        assert "chunk content" in req.prompt


# ----------------------------------------------------------------------
# 7) 置信度
# ----------------------------------------------------------------------


class TestConfidence:
    @pytest.mark.asyncio
    async def test_confidence_in_range(self, specialist):
        resp = await specialist.invoke(_ctx())
        assert 0.0 <= resp.confidence <= 1.0

    @pytest.mark.asyncio
    async def test_capped_at_1(self, rag, inference):
        # 长 LLM 输出 + 一致性加成 → cap 在 1.0
        long_text = "EF 60%, normal\n" + ("详细分析 " * 100)
        inference.text = long_text
        s = CardiacSpecialistAgent(inference, rag)
        resp = await s.invoke(_ctx())
        assert resp.confidence <= 1.0


# ----------------------------------------------------------------------
# 8) 证据 chunk
# ----------------------------------------------------------------------


class TestEvidence:
    @pytest.mark.asyncio
    async def test_top_3_evidence_chunks(self, specialist):
        resp = await specialist.invoke(_ctx())
        assert len(resp.evidence_chunk_ids) == 3


# ----------------------------------------------------------------------
# 9) Capability tags
# ----------------------------------------------------------------------


class TestCapabilityTags:
    def test_tags(self, specialist):
        tags = specialist.get_capability_tags()
        assert "cardiac" in tags
        assert "specialist" in tags
        assert "grading_lvef" in tags


# ----------------------------------------------------------------------
# 10) Prompt 加载
# ----------------------------------------------------------------------


class TestPromptLoading:
    def test_loads_cardiac_prompt(self, specialist):
        assert "ASE" in specialist._prompt_template
        assert "LVEF" in specialist._prompt_template

    def test_empty_prompt_rejected(self, rag, inference):
        with pytest.raises(ValueError, match="prompt template is empty"):
            CardiacSpecialistAgent(inference, rag, prompt_template="   ")


# ----------------------------------------------------------------------
# 11) 严重程度等级完整性
# ----------------------------------------------------------------------


class TestSeverityLevels:
    def test_all_levels_defined(self):
        expected = {"normal", "mildly_reduced", "moderately_reduced", "severely_reduced"}
        assert set(LVEF_SEVERITY_LEVELS) == expected
