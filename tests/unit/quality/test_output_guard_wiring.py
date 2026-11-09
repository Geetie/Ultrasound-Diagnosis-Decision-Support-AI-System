"""守卫**接线**守卫（2026-11-09）：检测函数存在 ≠ 生产链路在用。

## 为什么这个文件必须有（本轮真机教训）

`detect_degenerate_grade_only` 与 `detect_self_made_grading_rule` 早在 2026-11-08
就写好了、还带着 16 + 11 例单测，但**没有任何生产调用方** ——
`app/agents/specialist/base.py` 的 import 里根本没有它们。
于是单测全绿，而生产照样把 `甲状腺 · TR1` 这种退化结论落库
（全库 797 条有正文的记录里 20 条，P.213 实测）。

这正是 A.91 记过的同一类事故：**「实现了但没接线」**。
所以本文件断言的是"**接上了**"，而不是"函数能识别"——
后者已经在 `test_degenerate_grade_only.py` 里测过。

## 断言分三层

1. **静态接线**：`base.py` 真的 import 了、`invoke` 路径里真的调了；
2. **行为**：喂一段退化/自造规则的 LLM 输出 → specialist 返回**降级响应**
   （`needs_review=True`、正文是可读中文、trace 里有原因码）；
3. **不误杀**：正常结论（含复述 prompt 自带的合法分档表）**不得**被拦。
"""

from __future__ import annotations

import asyncio
import inspect
import pathlib

import pytest

from app.agents.protocols import AgentContext, AgentResponse
from app.agents.specialist.base import RAGSpecialistAgent
from app.models.schemas import Organ

pytestmark = pytest.mark.track_inference

_BASE_PY = pathlib.Path(__file__).resolve().parents[3] / "app/agents/specialist/base.py"


# ---------------------------------------------------------------------------
# 1) 静态接线：import + 调用点
# ---------------------------------------------------------------------------


class TestStaticWiring:
    def test_both_detectors_are_imported(self):
        src = _BASE_PY.read_text(encoding="utf-8")
        assert "detect_degenerate_grade_only" in src, "退化结论检测没被 import（=没接线）"
        assert "detect_self_made_grading_rule" in src, "自造分档规则检测没被 import（=没接线）"

    def test_both_detectors_are_called_in_invoke(self):
        """必须出现在 `invoke` 的**方法体**里，不只是模块顶部 import。"""
        src = inspect.getsource(RAGSpecialistAgent.invoke)
        assert "detect_degenerate_grade_only(" in src, "退化检测在 invoke 里没有调用点"
        assert "detect_self_made_grading_rule(" in src, "自造规则检测在 invoke 里没有调用点"

    def test_source_text_is_actually_passed(self):
        """自造规则检测必须带**来源**（prompt+块），否则退化成"出现过就拦"。

        生产 prompt 里本来就有分档表（`TR2（2 分）`），不带来源会把
        "模型忠实复述 prompt" 判成"编造"（P.214 实测 40/45 条映射与 prompt 一致）。
        """
        src = inspect.getsource(RAGSpecialistAgent.invoke)
        assert "source_text=" in src, "调用 detect_self_made_grading_rule 时没传 source_text"


# ---------------------------------------------------------------------------
# 2) 行为：真的拦得住
# ---------------------------------------------------------------------------


class _StubChunks:
    """最小 KnowledgeChunk 替身（只需被 `_build_prompt` 读到的字段）。"""

    def __init__(self, chunk_id: str, content: str):
        self.chunk_id = chunk_id
        self.content = content
        self.organ = "thyroid"
        self.kb_version = "kb_v1.4.0"
        self.source_doc_id = chunk_id
        self.section_path = ""
        self.score = None
        self.rerank_score = None
        # 2026-11-09（同期改动）：prompt 会按 metadata 标"这块来自哪套语料"。
        # 替身必须给这个字段，否则 `_build_prompt` 走不到我们想测的守卫。
        self.metadata = {}

    def model_dump(self, *args, **kwargs):
        """pydantic 兼容（部分路径会用 model_dump 取字段）。"""
        return {k: v for k, v in vars(self).items()}


class _StubKnowledge:
    def __init__(self, chunks):
        self._chunks = chunks

    async def query(self, query, organ, top_k=5, profile=None, **kwargs):
        return list(self._chunks)

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class _StubEngine:
    """返回预设 LLM 文本的推理引擎替身（不联网）。"""

    def __init__(self, text: str, finish_reason: str = "stop"):
        self._text = text
        self._finish_reason = finish_reason

    async def generate(self, request):
        from app.inference.protocols import InferenceResponse, TokenUsage

        return InferenceResponse(
            text=self._text,
            model="stub",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            finish_reason=self._finish_reason,
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


PROMPT_TEMPLATE = """# 甲状腺判读

- **TR1（0 分）**：良性 — 随访即可
- **TR2（2 分）**：不怀疑恶性 — 随访
- **TR3（3 分）**：低度可疑恶性 — 随访 ≥ 3 年
- **TR4（4-6 分）**：中度可疑恶性 — FNA ≥ 1cm
- **TR5（≥ 7 分）**：高度可疑恶性 — FNA ≥ 1cm
"""


def _run_invoke(llm_text: str, finish_reason: str = "stop") -> AgentResponse:
    """把 LLM 文本喂进**生产 apply 流程**（真 invoke，不是调检测函数）。"""
    from app.agents.specialist.thyroid import ThyroidSpecialistAgent

    agent = ThyroidSpecialistAgent(
        inference_engine=_StubEngine(llm_text, finish_reason),
        knowledge_agent=_StubKnowledge([
            _StubChunks("kb_v1.4.0:t_0001#0000", "TI-RADS 3 类结节 6-12 个月随访。"),
        ]),
        prompt_template=PROMPT_TEMPLATE,
        rag_top_k=1,
        max_tokens=512,
    )
    ctx = AgentContext(
        request_id="guard-wiring-1",
        session_id="guard-wiring",
        organ=Organ.THYROID,
        user_query="甲状腺右叶低回声结节 8mm，边界清，无钙化。",
    )
    return asyncio.run(agent.invoke(ctx))


class TestDegenerateGradeOnlyFlagged:
    """`甲状腺 · TR1` 这类"只有分级码"必须在生产链路里**被标注 + 转人工**。

    ## 为什么是"标注"而不是"整条拒收"（2026-11-09 第一版教训）

    第一版把这类也按回显守卫那样整条拒收（正文换成降级文案、confidence=0）。
    结果是 **35 个既有用例变红**：`分级: LR-3` 这种"只有分级、没写理由"的输出
    在既有契约里是**正常输入**（prompt 要求"首行就是分级行"），整条拒收等于把
    "模型很简洁"判成"模型没产出"。

    医学语义上也不对：分级本身是模型的真实判读结果，抹掉它会让医生以为
    "系统没判出来"（事实是模型没说理由）—— 与 A.111 的纪律一致：
    **检测到问题 ≠ 丢掉结论**，正确处置是"如实标注 + 转人工复核"。
    """

    def test_grade_only_is_flagged_and_keeps_grade(self):
        resp = _run_invoke("甲状腺 · TR1")
        assert resp.needs_review is True, "退化结论没有转人工"
        assert any("grade_only_no_body_flagged" in t for t in resp.reasoning_trace), (
            f"trace 里没有退化原因码：{resp.reasoning_trace}"
        )
        # 分级必须还在（医生要看得到系统判到了什么）
        assert "TR1" in resp.content, f"分级被抹掉了：{resp.content!r}"
        # 但必须明确写清"没有依据"，不能让它看起来像一条完整结论
        assert "未给出分级依据" in resp.content, f"缺依据说明缺失：{resp.content!r}"

    def test_grade_only_does_not_drop_confidence_to_zero(self):
        """**不许**把置信度清零 —— 分级是真实判读，清零会让下游当"没产出"处理。"""
        resp = _run_invoke("甲状腺 · TR1")
        assert resp.confidence > 0.0, f"退化结论的置信度被清零了：{resp.confidence}"

    def test_normal_short_conclusion_is_not_flagged(self):
        """有实质建议的短结论**不得**被误标（假阳性比漏报更糟）。"""
        resp = _run_invoke("甲状腺 · TR3 — 低回声 1 分，建议 6 个月复查。")
        assert not any("grade_only_no_body" in t for t in (resp.reasoning_trace or [])), (
            f"正常短结论被误标：{resp.reasoning_trace}"
        )
        assert "未给出分级依据" not in resp.content

    def test_degraded_text_is_not_double_annotated(self):
        """已经是降级文案（自带"已转人工"）时**不许**再追加一句免责声明。"""
        resp = _run_invoke("分级: 无法判断")
        assert resp.content.count("转人工") <= 1, (
            f"降级文案被重复追加说明：{resp.content!r}"
        )


class TestSelfMadeRuleRejected:
    def test_contradicting_rule_is_rejected(self):
        """`0-2 分为 TR2` 与模板的 `TR1（0 分）` 冲突 → 必须拦。"""
        resp = _run_invoke(
            "甲状腺 · TR2 — 低回声(2) + 边界清(0) = 2 分 → TR2（注：0-2 分为 TR2）"
        )
        assert resp.needs_review is True
        assert any("self_made_grading_rule_rejected" in t for t in resp.reasoning_trace), (
            f"trace 里没有自造规则原因码：{resp.reasoning_trace}"
        )

    def test_restating_prompt_table_is_not_rejected(self):
        """复述模板自带的表（`4-6 分为 TR4`）**不是编造** —— 不许拦。

        这是本轮最重要的假阳性回归：把"守规矩"当"造假"会让所有忠实复述的结论
        全被拒收，比漏报更糟。
        """
        resp = _run_invoke("甲状腺 · TR4 — 低回声(2) + 边缘(2) = 4 分 → TR4（4-6 分为 TR4）")
        assert not any(
            "self_made_grading_rule" in t for t in (resp.reasoning_trace or [])
        ), f"复述模板规则被误判：{resp.reasoning_trace}"


# ---------------------------------------------------------------------------
# 3) 来源构造：失败要更保守，不是放行
# ---------------------------------------------------------------------------


class TestGradingRuleSource:
    def test_source_contains_prompt_and_chunk_text(self):
        from app.agents.specialist.thyroid import ThyroidSpecialistAgent

        agent = ThyroidSpecialistAgent(
            inference_engine=_StubEngine("分级: TR3"),
            knowledge_agent=_StubKnowledge([]),
            prompt_template=PROMPT_TEMPLATE,
            rag_top_k=1,
            max_tokens=64,
        )
        src = agent._grading_rule_source(
            "PROMPT_BODY **TR4（4-6 分）**", [_StubChunks("c1", "CHUNK_BODY 4-6 分为 TR4")]
        )
        assert "PROMPT_BODY" in src and "CHUNK_BODY" in src

    def test_source_build_failure_returns_empty_string(self):
        """拼来源失败 → 返回空串 = 严格模式（宁可报不可漏），不是放行。"""
        from app.agents.specialist.thyroid import ThyroidSpecialistAgent

        agent = ThyroidSpecialistAgent(
            inference_engine=_StubEngine("分级: TR3"),
            knowledge_agent=_StubKnowledge([]),
            prompt_template=PROMPT_TEMPLATE,
            rag_top_k=1,
            max_tokens=64,
        )

        class _Boom:
            @property
            def content(self):
                raise RuntimeError("boom")

        assert agent._grading_rule_source("p", [_Boom()]) == ""
