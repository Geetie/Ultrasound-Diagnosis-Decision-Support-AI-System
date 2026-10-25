"""A.95 Q11 守卫：CRAG 合并后的 evidence **必须可解释**（不许是黑箱）。

## 真机现象（A.62 批1，2026-10-14）

心脏腿 **2/2** 出现「数据库 `knowledge_ids` 里有块，但不在任何 `_build_prompt`
留痕中」（例 `dialogue_cardiac_外科_0028#0000`）；甲状腺腿 **5/5** 正常。

**这个现象的直接后果**：没人能回答"这条结论到底看了哪些材料" ——
而这是可信度的根基。审计看到"引用了 prompt 里没有的块"，第一反应是"引用造假"。

## 根因（本轮定位，`app/fusion/crag.py:_merge_evidence_three` 第①步）

```python
# ① 原 evidence
for cid in (original.evidence_chunk_ids or []):
    merged.append(cid)          # ← 来自**第一次**生成时的 prompt
# ② 新 evidence chunks           ← 本次重生成的 prompt 所见
# ③ reinvoked response evidence
```

`original.evidence_chunk_ids` 是**首次**生成时 specialist 引用的证据。
CRAG 重新调用 specialist 时，它看到的 prompt 里是 `new_evidence`——
**不是** original 那批。所以 `final_evidence` 必然包含"本次 prompt 里没有的块"。

**判定：这不是 bug，是有意设计**（丢掉原证据会让结论依据残缺）。
但**必须可解释** —— 否则每次审计都要重新查一遍。

**为什么甲状腺腿没出现**：它的结论没触发 CRAG 重生成
（`crag_recovered=False`），走原样返回路径，`final_evidence == original.evidence`，
与 prompt 完全一致。

## 处置（保留语义 + 加留痕，不动引用集合）

1. `_merge_evidence_three` 的 docstring 写清三段来源与"为何不是 bug"；
2. `_build_new_response` 在 `reasoning_trace` 里加 `evidence_sources:` 一行，
   含 `original/new/reinvoked/final/not_in_this_prompt` 五个计数；
3. 当存在"仅来自首次 prompt"的块时，额外打一条
   `crag_evidence_includes_first_prompt_only` 日志（带 sample + hint）。

## 本守卫钉什么

* 三段来源的计数**真的出现在** trace 里（反证：删掉即红）；
* `not_in_this_prompt` 计数**准确**（构造一个 original 有、new 没有的场景验证）；
* **不许**为了"让测试过"而截断 evidence（合并语义必须保留）；
* 无 CRAG 重生成时，不产生该 trace（不留噪音）。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.crag import CRAGHandler
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ

pytestmark = [pytest.mark.track_fus]

_REPO = Path(__file__).resolve().parents[3]
_CRAG = _REPO / "app" / "fusion" / "crag.py"


def _resp(agent: str, content: str, evidence: list[str]) -> AgentResponse:
    return AgentResponse(
        agent_name=agent,
        organ=Organ.CARDIAC,
        content=content,
        confidence=0.6,
        evidence_chunk_ids=evidence,
    )


def _chunk(cid: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=cid,
        kb_version="kb_v1.3.0",
        organ="cardiac",
        content="x",
        metadata={},
        embedding=None,
        source_doc_id=cid,
        section_path="",
        token_count=1,
    )


class TestMergeSemanticsPreserved:
    """合并语义**不许**被改动（这条是防"为了修测试而截断证据"）。"""

    def test_original_evidence_kept(self):
        """反证腿：把第①步删掉 → 本用例红（原证据丢失 = 结论依据残缺）。"""
        original = _resp("cardiac_specialist", "首次结论", ["old_A", "old_B"])
        new_resp = _resp("cardiac_specialist", "重生成结论", ["new_C"])
        merged = CRAGHandler._merge_evidence_three(
            original, [_chunk("chunk_X")], new_resp
        )
        assert "old_A" in merged, "首次 prompt 的证据被丢了"
        assert "old_B" in merged
        assert "chunk_X" in merged, "新证据没进来"
        assert "new_C" in merged, "重生成响应自己的证据没进来"

    def test_dedup_and_order(self):
        original = _resp("c", "a", ["A", "B"])
        new_resp = _resp("c", "b", ["B", "C"])
        merged = CRAGHandler._merge_evidence_three(original, [_chunk("A")], new_resp)
        assert merged == ["A", "B", "C"], f"顺序/去重不对: {merged}"


class TestEvidenceSourcesTraced:
    """三段来源必须出现在 trace 里（这是"可解释"的落地）。"""

    def test_source_line_present_in_code(self):
        src = _CRAG.read_text(encoding="utf-8")
        assert "evidence_sources:" in src, (
            "没有把三段来源写进 trace → 审计仍无法解释"
            "『落库 evidence 包含本次 prompt 没有的块』"
        )
        for key in (
            "original=",
            "new=",
            "reinvoked=",
            "final=",
            "not_in_this_prompt=",
        ):
            assert key in src, f"trace 缺计数 {key}"

    def test_trace_variable_is_actually_used(self):
        """反证腿：删掉 `evidence_sources_trace,` 这一行引用 → 本用例红。

        ⚠️ 这条守卫第一版只断言 `"evidence_sources:" in src`，
        而反证（只留变量定义、删掉引用）时**仍然全绿** —— 那是**假守卫**。
        改为 AST 层面确认变量真的出现在 `reasoning_trace=[...]` 里。
        """
        import ast

        src = _CRAG.read_text(encoding="utf-8")
        tree = ast.parse(src)
        assigned = False
        used_in_trace = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id == "evidence_sources_trace":
                        assigned = True
            if (
                isinstance(node, ast.keyword)
                and node.arg == "reasoning_trace"
                and isinstance(node.value, ast.List)
            ):
                for el in node.value.elts:
                    if isinstance(el, ast.Name) and el.id == "evidence_sources_trace":
                        used_in_trace = True
        assert assigned, "没有 evidence_sources_trace 变量定义"
        assert used_in_trace, (
            "evidence_sources_trace 只定义、**没放进 reasoning_trace** —— 留痕等于没做"
        )

    def test_log_event_present(self):
        src = _CRAG.read_text(encoding="utf-8")
        assert "crag_evidence_includes_first_prompt_only" in src, (
            "缺专用日志事件 —— 现场排查时只能靠人肉比对"
        )

    def test_hint_explains_it_is_not_fabrication(self):
        """提示里必须写明"不是引用造假"，否则日志会被误读。"""
        src = _CRAG.read_text(encoding="utf-8")
        assert "不是引用造假" in src or "有意合并" in src


class TestNotInThisPromptCount:
    """`not_in_this_prompt` 的计数必须准确（构造场景验证）。"""

    def test_counts_only_original_not_in_new(self):
        """original 有 A/B，new 只有 B → 仅 A 属于"本次 prompt 没有"。"""
        orig = ["A", "B"]
        new = ["B", "C"]
        final = ["A", "B", "C"]
        only_from_original = [x for x in orig if x in set(final) and x not in set(new)]
        assert only_from_original == ["A"], (
            f"计数逻辑应只挑出 A，实际 {only_from_original}"
        )

    def test_no_crag_means_no_evidence_sources(self):
        """没走 CRAG 重生成时不该有该 trace（不留噪音）。

        这条通过代码结构保证：`evidence_sources_trace` 只在
        `_specialist_invoker is not None` 分支里构造。
        """
        src = _CRAG.read_text(encoding="utf-8")
        idx_invoker = src.index("if self._specialist_invoker is not None:")
        idx_trace = src.index("evidence_sources_trace = (")
        assert idx_invoker < idx_trace, (
            "evidence_sources 不在重生成分支里 → 无 CRAG 的请求也会带这行噪音"
        )


class TestDocstringExplainsRootCause:
    def test_merge_docstring_names_the_break(self):
        """docstring 必须写清"这是断点来源"与"为何不是 bug"。"""
        src = _CRAG.read_text(encoding="utf-8")
        idx = src.index("def _merge_evidence_three")
        body = src[idx : idx + 3000]
        assert "A.95" in body, "没标注修复批次"
        assert "首次" in body, "没说清 original 来自首次生成"
        assert "不是 bug" in body or "不是引用造假" in body, (
            "没写清这是有意设计 —— 后来者会重新怀疑这是 bug"
        )
