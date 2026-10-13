"""语义层「反向索要」守卫（A.58；20 号「零静默」研究产出 2 第 3 条）。

背景
----
**反向索要** = 模型把**本应由系统提供**的输入（影像特征 / 病灶边界 / 图像本身）抛回给医生
（A.37 真机走查实测形态：影像信息没喂进 prompt，prompt 又把成分/回声/形态列为预期输入）。
后果与"静默失败"同族：医生读到的是「请提供病灶边界」——系统**看起来在工作**，实际把活
推回给了人，且没有任何标记。

本契约钉三件事：
1. **判红**：LLM 输出命中（强动作 + 影像/临床输入对象）→ ``needs_review=True`` + trace 留痕；
2. **不当结论**：若索要**真的进了医生可见正文**，正文换成可读交代（不含索要短语）；
3. **不误伤**：A.56 的可读降级文案（「…已转人工判读：请结合图像与病史自行分级」）与
   正常结论里的建议措辞（「建议复查；请结合临床」）**不得**被判成索要。
"""
from __future__ import annotations

import pytest

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.specialist.cardiac import CardiacSpecialistAgent
from app.agents.specialist.liver import LiverSpecialistAgent
from app.agents.specialist.solicitation import (
    HANDOFF_TEXT,
    detect_solicitation,
    is_system_handoff_text,
)
from app.agents.specialist.thyroid import ThyroidSpecialistAgent
from app.models.schemas import Organ
from tests.unit.agt.conftest import FakeInferenceEngine, FakeKnowledgeAgent, make_chunks

pytestmark = [pytest.mark.track_agt]


def _ctx(organ: Organ, query: str = "q") -> AgentContext:
    return AgentContext(request_id="r1", session_id="s1", organ=organ, user_query=query)


def _agent(cls, organ: str, text: str):
    inference = FakeInferenceEngine(text=text)
    rag = FakeKnowledgeAgent(chunks=make_chunks(5, organ=organ))
    return cls(inference_engine=inference, knowledge_agent=rag)


_DEGRADE_TEXTS = {
    "thyroid": "本例未能解析出 TI-RADS 分级（AI 输出未给出可判定的分级码），已转人工判读：请结合图像与病史自行分级。",
    "liver": "本例未能解析出 LI-RADS 分级（AI 输出未给出可判定的分级码），已转人工判读：请结合图像与病史自行分级。",
    "cardiac": "本例未能解析出左心室收缩功能分级（AI 输出未给出可判定的 LVEF 值或严重程度），已转人工判读：请结合图像与病史自行评估。",
}


class TestSolicitationDetector:
    @pytest.mark.parametrize(
        "text",
        [
            "请提供病灶边界，据此判断形态与纵横比。",
            "请描述图像中结节的回声与后方回声特征。",
            "请上传原图以便进一步分析。",
            "如需分级，麻烦提供影像数据。",
            "需要您提供病灶的具体位置。",
            "请重新提供该切面的声像图。",
        ],
    )
    def test_solicitation_is_detected(self, text):
        assert detect_solicitation(text) is not None

    @pytest.mark.parametrize(
        "text",
        [
            *_DEGRADE_TEXTS.values(),
            HANDOFF_TEXT,
            # 正常结论里的建议措辞：动作是"请结合/建议"，不是"要东西"
            "TR3，建议 6 个月后复查；请结合临床。",
            "LI-RADS 3，建议增强 MRI 进一步评估。",
            "LVEF 58%，未见明显节段性室壁运动异常。",
            # 系统占位文案
            "[thyroid] evidence_insufficient: no relevant knowledge retrieved; manual review required",
        ],
    )
    def test_handoff_and_normal_texts_are_exempt(self, text):
        """**显式豁免**（本轮要点）：转人工建议与正常结论不得被判成索要。"""
        assert detect_solicitation(text) is None

    def test_handoff_marker_is_defensive(self):
        """系统转人工文案带固定标记；即使把守卫挂到 content 上也不会误判。"""
        assert is_system_handoff_text(HANDOFF_TEXT) is True
        assert is_system_handoff_text("请提供病灶边界") is False
        for text in _DEGRADE_TEXTS.values():
            assert is_system_handoff_text(text) is True
            assert detect_solicitation(text) is None


class TestSpecialistSolicitationGuard:
    @pytest.mark.parametrize(
        ("cls", "organ"),
        [
            (ThyroidSpecialistAgent, "thyroid"),
            (LiverSpecialistAgent, "liver"),
            (CardiacSpecialistAgent, "cardiac"),
        ],
    )
    async def test_solicitation_entering_content_is_replaced(self, cls, organ):
        """索要进了正文（写在"理由"段）→ 正文换可读交代 + 转人工 + trace 留痕。"""
        text = {
            "thyroid": "分级: TR3 总分: 3 理由: 实性(3)+低回声(2)；请提供病灶边界以确认形态。",
            "liver": "分级: LR-3 理由: 动脉期等增强；请描述图像中的包膜与廓清特征。",
            "cardiac": "分级: mildly_reduced 理由: 室壁运动减弱；请提供病灶边界与切面信息。",
        }[organ]
        resp = await _agent(cls, organ, text).invoke(_ctx(Organ(organ)))

        assert resp.needs_review is True, "模型反向索要必须转人工，不得静默出结论"
        assert "请提供" not in resp.content and "请描述" not in resp.content, (
            f"{organ} 正文仍把索要当结论：{resp.content}"
        )
        assert resp.content == HANDOFF_TEXT
        assert any(t.startswith("solicitation_guard:") for t in resp.reasoning_trace)
        assert not is_system_handoff_text(text), "用例前提：模型原文不是系统文案"

    def test_solicitation_outside_content_keeps_conclusion(self):
        """守卫**方法契约**：索要没进正文 → 保留正文，只转人工 + 留痕。

        实测补充（本用例为什么直接调方法而不是走 invoke）：``_extract_reason`` 的窗口较宽，
        模型把索要写在**开头**时也会被并进正文 → 端到端路径上正文会被替换
        （见 ``test_solicitation_entering_content_is_replaced``）。所以"正文不含索要"
        这一支只能按方法契约验证：传一份正文不含索要的响应进去，断言正文原样保留。
        """
        agent = _agent(ThyroidSpecialistAgent, "thyroid", "分级: TR4 理由: 实性(3)+低回声(2)")
        resp = AgentResponse(
            agent_name="thyroid_specialist",
            organ=Organ.THYROID,
            content="[thyroid] TR4 — 实性(3)+低回声(2)+边界不清(1)",
            confidence=0.8,
            evidence_chunk_ids=[],
            reasoning_trace=[],
        )

        agent._apply_solicitation_guard(
            resp, "请提供病灶边界。分级: TR4 总分: 6 理由: 实性(3)+低回声(2)", _ctx(Organ.THYROID)
        )

        assert resp.needs_review is True
        assert "TR4" in resp.content, "有用的分级结论不该被一起丢掉"
        assert resp.content != HANDOFF_TEXT
        assert any(t.startswith("solicitation_guard:") for t in resp.reasoning_trace)

    async def test_degrade_path_is_not_flagged_as_solicitation(self):
        """A.56 可读降级（正文含「请结合图像与病史自行分级」）不得被判成反向索要。"""
        resp = await _agent(ThyroidSpecialistAgent, "thyroid", "本图无法判断").invoke(
            _ctx(Organ.THYROID)
        )

        assert resp.needs_review is True
        assert "已转人工判读" in resp.content, "系统降级文案信息量更大，应保留而不是被换掉"
        assert not any(t.startswith("solicitation_guard:") for t in resp.reasoning_trace)
        assert any(t.startswith("grading_unparseable") for t in resp.reasoning_trace)

    async def test_normal_conclusion_is_untouched(self):
        """正常结论（有分级、无索要）不得被守卫改动。"""
        text = "分级: TR3 总分: 3 理由: 实性(3)+低回声(2)；建议随访。"
        resp = await _agent(ThyroidSpecialistAgent, "thyroid", text).invoke(_ctx(Organ.THYROID))

        assert not any(t.startswith("solicitation_guard:") for t in resp.reasoning_trace)
        assert "TR3" in resp.content
        assert resp.content != HANDOFF_TEXT
