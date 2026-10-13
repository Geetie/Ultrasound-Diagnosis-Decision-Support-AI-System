"""分级提取**契约**（2026-10-13 分级提取根治）。

背景
----
专科 Agent 原先各自维护一套平行正则，与评测 / 双引擎一致性校验链路的
``app/inference/grading_definitions.py``（``_GRADING_PATTERNS`` /
``_GRADE_HIERARCHY``）**不同源** —— 同一份 LLM 输出「评测抽得到、生产结论抽不到」，
直接吐英文技术占位（``grade_unparseable``）进医生可见正文。

本契约钉住四件事：

1. **单一事实源**：三器官抽取统一走 ``grading_definitions`` / ``grading_schema``；
2. **口径统一**：thyroid ``4a``/``4b``/``4c`` 收敛为主级 ``TR4``；cardiac 的
   canonical short 名（``mild``/``moderate``/``severe``）映射到本专科报告标签
   （``mildly_reduced``/…）；
3. **保守取重**：多个分级码并存时取最严重（"TR3 或 TR4" → TR4）；
4. **未知码不猜**：``grade_major_level`` 对未知分级返回 ``None``，调用方走兜底 /
   降级 —— 「猜一个等级」比「如实说没解析出来」更危险。
"""
from __future__ import annotations

import pytest

from app.agents.protocols import AgentContext
from app.agents.specialist.cardiac import CardiacSpecialistAgent
from app.agents.specialist.liver import LiverSpecialistAgent
from app.agents.specialist.thyroid import ThyroidSpecialistAgent
from app.inference.grading_definitions import (
    extract_grading_severest,
    grade_major_level,
)
from app.inference.protocols import InferenceResponse
from app.models.schemas import Organ
from tests.unit.agt.conftest import (
    FakeInferenceEngine,
    FakeKnowledgeAgent,
    make_chunks,
)

pytestmark = [pytest.mark.track_agt]


def _ctx(organ: Organ, query: str = "q") -> AgentContext:
    return AgentContext(request_id="r1", session_id="s1", organ=organ, user_query=query)


def _agent(cls, organ: str, text: str):
    inference = FakeInferenceEngine(text=text)
    rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ=organ))
    return cls(inference_engine=inference, knowledge_agent=rag)


# ----------------------------------------------------------------------
# 1) 单一事实源：canonical 抽取 / 主级映射
# ----------------------------------------------------------------------


class TestCanonicalExtractionPrimitives:
    def test_major_level_collapses_subclasses(self):
        """4a/4b/4c 主级都是 4 —— 这是"4a/4b ↔ TR4"收敛的唯一依据。"""
        assert grade_major_level("thyroid", "4a") == 4
        assert grade_major_level("thyroid", "4b") == 4
        assert grade_major_level("thyroid", "4c") == 4
        assert grade_major_level("thyroid", "5") == 5
        assert grade_major_level("liver", "3") == 3
        assert grade_major_level("cardiac", "severe") == 3

    def test_unknown_grade_is_none_never_guessed(self):
        """未知分级/未知器官一律 None，绝不兜底成某个等级。"""
        assert grade_major_level("thyroid", "9") is None
        assert grade_major_level("thyroid", "") is None
        assert grade_major_level("thyroid", None) is None
        assert grade_major_level("not_an_organ", "4") is None

    def test_severest_picks_most_severe(self):
        """专科结论必须保守取重（LLM 写"或"时给医生看重的那个）。"""
        assert extract_grading_severest("分级: TR3 或 TR4", "thyroid") == "4"
        assert extract_grading_severest("TI-RADS 2，鉴别 TI-RADS 4b", "thyroid") == "4b"
        assert extract_grading_severest("考虑 LR-3 或 LR-5", "liver") == "5"
        assert extract_grading_severest("EF 60%，另考虑 severely_reduced", "cardiac") == "severe"

    def test_severest_returns_none_without_grading(self):
        assert extract_grading_severest("甲状腺结节 8mm，边界清晰", "thyroid") is None

    def test_explicit_grade_segment_beats_reason_range_note(self):
        """回归（真机 p27 实测的**假高**）：理由里的范围说明不得覆盖结论段。

        模型原样输出（截取自 p27 真实响应）：
        ``分级: TR3 总分: 3 … 理由: 实性(3) + 低回声(2) + 边界尚清(1) = 6 分 → TR4（注：4-6 分为 TR4）``
        —— 全文取最严重会拿到理由里的 TR4，把医生看到的结论抬高一级。
        """
        real = (
            "分级: TR3 总分: 3 置信度: 0.85 理由: 实性(3) + 低回声(2) + 边界尚清(1) "
            "= 6 分 → TR4（注：4-6 分为 TR4） 证据: chunk_a1"
        )
        assert extract_grading_severest(real, "thyroid") == "3"

    def test_conservative_take_within_explicit_segment(self):
        """保守取重仍生效 —— 但只在**同一结论段内**（"或"场景）。"""
        assert extract_grading_severest("分级: TR3 或 TR4 置信度: 0.8", "thyroid") == "4"
        assert extract_grading_severest("分级: LR-3 或 LR-5 置信度: 0.8", "liver") == "5"


# ----------------------------------------------------------------------
# 2) thyroid：4a/4b → TR4，结构化 JSON 也能吃
# ----------------------------------------------------------------------


class TestThyroidCanonicalContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "llm_text",
        [
            "分级: TI-RADS 4a",
            "分级: 4b",
            "TI-RADS: 4c",
            "分级: TR4a",
            '{"grading": "4b"}',  # 采样层结构化输出形态（约束解码重试）
        ],
    )
    async def test_subclasses_collapse_to_tr4(self, llm_text):
        s = _agent(ThyroidSpecialistAgent, "thyroid", llm_text)
        resp = await s.invoke(_ctx(Organ.THYROID))
        assert "TR4" in resp.content, f"{llm_text!r} 应归一为 TR4，实得 {resp.content[:80]!r}"
        assert resp.confidence > 0.0
        assert "grade_unparseable" not in resp.content

    @pytest.mark.asyncio
    async def test_conflicting_grades_take_severest(self):
        s = _agent(ThyroidSpecialistAgent, "thyroid", "分级: TR3 或 TR4")
        resp = await s.invoke(_ctx(Organ.THYROID))
        assert "TR4" in resp.content


# ----------------------------------------------------------------------
# 3) liver：canonical 数字路径 + LR-M/LR-TIV 由器官正则在兜底路径承接
# ----------------------------------------------------------------------


class TestLiverCanonicalContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("llm_text", "expected"),
        [
            ("分级: LI-RADS 3", "LR-3"),
            ('{"grading": "5"}', "LR-5"),
            ("考虑 LR-3 或 LR-5", "LR-5"),
        ],
    )
    async def test_canonical_paths(self, llm_text, expected):
        s = _agent(LiverSpecialistAgent, "liver", llm_text)
        resp = await s.invoke(_ctx(Organ.LIVER))
        assert expected in resp.content
        assert "grade_unparseable" not in resp.content

    @pytest.mark.asyncio
    @pytest.mark.parametrize("llm_text", ["分级: LR-M", "分级: LR-TIV"])
    async def test_lr_m_and_tiv_kept_by_organ_fallback(self, llm_text):
        """LR-M / LR-TIV 无主级语义（不在 `_GRADE_HIERARCHY`）→ 由器官正则承接。"""
        s = _agent(LiverSpecialistAgent, "liver", llm_text)
        resp = await s.invoke(_ctx(Organ.LIVER))
        assert llm_text.split(": ")[1] in resp.content


# ----------------------------------------------------------------------
# 4) cardiac：canonical short 名 → 本专科报告标签
# ----------------------------------------------------------------------


class TestCardiacCanonicalContract:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("llm_text", "expected"),
        [
            ('{"grading": "severe"}', "severely_reduced"),
            ('{"grading": "mild"}', "mildly_reduced"),
            ("分级: severely_reduced", "severely_reduced"),
            ("EF 38%", "moderately_reduced"),  # 数值路径（LVEF 表）仍生效
        ],
    )
    async def test_canonical_and_numeric_paths(self, llm_text, expected):
        s = _agent(CardiacSpecialistAgent, "cardiac", llm_text)
        resp = await s.invoke(_ctx(Organ.CARDIAC))
        assert expected in resp.content
        assert "lvef_unparseable" not in resp.content


# ----------------------------------------------------------------------
# 4b) 零静默 / 可读性守卫：技术占位串不得进医生可见正文
# ----------------------------------------------------------------------


class TestNoTechnicalStringInDoctorFacingContent:
    """医生可见 ``content`` 一律可读中文；英文技术串只准出现在 ``reasoning_trace``。

    现场证据（2026-10-13 真机走查）：结论区曾整段显示
    ``[thyroid] grade_unparseable: LLM output did not contain a valid TI-RADS grade …
    LLM preview: …`` —— 医生看到的是技术日志而不是结论。本守卫按器官逐个钉住。
    """

    _TECHNICAL = (
        "unparseable",
        "manual review required",
        "LLM preview",
        "LLM output did not",
    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("cls", "organ", "text"),
        [
            (ThyroidSpecialistAgent, "thyroid", "本图无法判断"),
            (LiverSpecialistAgent, "liver", "本图无法判断"),
            (CardiacSpecialistAgent, "cardiac", "心脏超声未见明显异常"),
        ],
    )
    async def test_degraded_content_is_readable_and_review_flagged(self, cls, organ, text):
        s = _agent(cls, organ, text)
        resp = await s.invoke(_ctx(Organ(organ)))
        for bad in self._TECHNICAL:
            assert bad not in resp.content, f"{organ} 降级正文混入技术串：{bad}"
        assert resp.needs_review is True
        assert any(t.startswith("grading_unparseable") for t in resp.reasoning_trace)


# ----------------------------------------------------------------------
# 5) 有界重试 + 截断识别（base.invoke 步骤 5b）
# ----------------------------------------------------------------------


class _SequencedInference:
    """按调用次序返回不同文本的 fake（结构化重试用例专用）。

    记录每次 ``InferenceRequest``，用于断言「重试确实下发了采样层约束」而不是
    拿同一份自由文本再问一遍。
    """

    def __init__(self, texts: list[str], finish_reasons: list[str] | None = None):
        self._texts = list(texts)
        self._finish = list(finish_reasons or [])
        self.requests: list = []

    async def generate(self, request) -> InferenceResponse:
        self.requests.append(request)
        idx = min(len(self.requests) - 1, len(self._texts) - 1)
        finish = self._finish[idx] if idx < len(self._finish) else "stop"
        return InferenceResponse(text=self._texts[idx], finish_reason=finish)

    async def health_check(self) -> bool:
        return True


class TestBoundedRetry:
    @pytest.mark.asyncio
    async def test_structured_retry_recovers_grade_without_losing_rich_text(self):
        """首轮无分级码 → **一次**结构化重试救回；富文本正文仍来自首轮。"""
        inference = _SequencedInference(
            [
                "影像特征：实性、低回声、分叶状边缘。",  # 首轮：有特征、无分级码（跑偏）
                '{"grading": "4b"}',  # 重试：约束解码输出
            ]
        )
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))
        s = ThyroidSpecialistAgent(inference_engine=inference, knowledge_agent=rag)

        resp = await s.invoke(_ctx(Organ.THYROID))

        assert "TR4" in resp.content
        assert resp.confidence > 0.0
        assert len(inference.requests) == 2
        # 主调用不带约束；重试带约束（这是"重试"与"再问一遍"的本质区别）
        assert "response_format" not in inference.requests[0].extra
        assert inference.requests[1].extra["response_format"]["type"] == "json_schema"
        assert any(
            t.startswith("structured_grading_retry: recovered") for t in resp.reasoning_trace
        )

    @pytest.mark.asyncio
    async def test_retry_is_bounded_to_one(self):
        """两次都抽不到 → 直接可读降级：**不得无界重试**（判读 P50 预算）。"""
        inference = _SequencedInference(["只有特征描述。", "仍然没有分级码。"])
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))
        s = ThyroidSpecialistAgent(inference_engine=inference, knowledge_agent=rag)

        resp = await s.invoke(_ctx(Organ.THYROID))

        assert len(inference.requests) == 2, "应为 1 次主调用 + 至多 1 次重试"
        assert "未能解析出" in resp.content
        assert resp.needs_review is True
        assert any(
            t.startswith("structured_grading_retry: still_unparseable")
            for t in resp.reasoning_trace
        )

    @pytest.mark.asyncio
    async def test_truncated_output_is_marked_distinctly(self):
        """``finish_reason=length``（输出被截断）必须与"模型跑偏"区分开留痕。"""
        inference = _SequencedInference(
            ["特征描述写了一半就没了"],
            finish_reasons=["stop"],
        )
        rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ="thyroid"))
        s = ThyroidSpecialistAgent(inference_engine=inference, knowledge_agent=rag)

        resp = await s.invoke(_ctx(Organ.THYROID))
        assert any(t.startswith("finish_reason:") for t in resp.reasoning_trace)

        # 重试腿被截断 → trace 里要有显式标记（便于运维判断是 max_tokens 太小）
        inference2 = _SequencedInference(
            ["还是没给分级", "被截断"],
            finish_reasons=["stop", "length"],
        )
        s2 = ThyroidSpecialistAgent(inference_engine=inference2, knowledge_agent=rag)
        resp2 = await s2.invoke(_ctx(Organ.THYROID))
        assert any("output_truncated" in t for t in resp2.reasoning_trace)
