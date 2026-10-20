"""A.70 守卫：引用覆盖率/正确率的三个纯函数必须可解释、可复算。

三条纪律：
* 覆盖率按**句**算，碎片句不算（避免"。"切出空句把分母灌大）；
* 引用**必须**能区分"chunk ID 形态"（可核对）与 "[n] 编号"（无法核对，单列，不混进正确率）；
* 证据 ID 抽不到时，正确率记 `None`（**不写 1.0**，同 A.69 的空桶纪律）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p46_under_test", _AUDIT / "p46_citation_coverage.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_split_sentences_drops_fragments():
    p46 = _load()
    s = p46.split_sentences("甲状腺结节 TR3。建议复查。好。")
    assert s == ["甲状腺结节 TR3", "建议复查"]  # "好" 太短被丢


def test_extract_chunk_id_and_numbering():
    p46 = _load()
    text = "结语。证据来源：dialogue_内科_2553#0000；另见 pdf_thyroid_x_0001#0000，以及 [1]。"
    cites = p46.extract_citations(text)
    assert "dialogue_内科_2553#0000" in cites
    assert "pdf_thyroid_x_0001#0000" in cites
    assert "[1]" in cites
    assert len(cites) == 3


def test_coverage_counts_cited_sentences():
    p46 = _load()
    out = p46.coverage_of(
        "甲状腺结节 TR3，证据来源：dialogue_内科_1#0000。建议六个月复查。边界尚清。"
    )
    assert out["sentences"] == 3
    assert out["sentences_with_citation"] == 1
    assert out["coverage"] == round(1 / 3, 4)


def test_coverage_is_none_for_empty_conclusion():
    p46 = _load()
    assert p46.coverage_of("")["coverage"] is None


def test_correctness_checks_chunk_ids_and_positional_numbering():
    p46 = _load()
    # 证据是**有序列表**：a#0000 命中；b#0000 不在证据里（编的）；[2] 在 1..2 范围内（按位置合法）
    out = p46.correctness_of(["a#0000", "b#0000", "[2]"], ["a#0000", "z#0000"])
    assert out["checked_chunk"] == 2
    assert out["checked_numbering"] == 1
    assert out["valid"] == 2  # a#0000 + [2]
    assert out["invalid"] == 1  # b#0000
    assert out["correctness"] == round(2 / 3, 4)


def test_numbering_out_of_range_is_invalid():
    p46 = _load()
    out = p46.correctness_of(["[9]"], ["a#0000", "b#0000"])
    assert out["checked_numbering"] == 1
    assert out["valid"] == 0
    assert out["invalid"] == 1
    assert out["correctness"] == 0.0


def test_correctness_is_none_when_no_evidence():
    p46 = _load()
    assert p46.correctness_of(["a#0000"], [])["correctness"] is None


def test_evidence_ids_prefers_context_ids_and_keeps_order():
    p46 = _load()
    assert p46.evidence_ids_of({"context_ids": ["x#0000", "y#0000"]}) == ["x#0000", "y#0000"]
    assert p46.evidence_ids_of({"context": [{"chunk_id": "y#0000"}]}) == ["y#0000"]
    assert p46.evidence_ids_of({"context": ["见 z#0000 段"]}) == ["z#0000"]


def test_scoring_parentheses_are_not_citations():
    """评分算式里的 `(3)`/`(0)` 是**分值**，不是引用（首版把它们当引用，算出两个假数）。"""
    p46 = _load()
    text = "理由: 实性/低回声 (3) + 边界光滑 (0) + 形态规则 (0) = 2 分 → TR2"
    assert p46.extract_citations(text) == []
    assert p46.coverage_of(text)["coverage"] == 0.0


def test_prompt_echo_is_detected():
    """模板回显：占位符 chunk_a1b2 / 患者PATIENT_001 / 多份 `# 输出` 块。"""
    p46 = _load()
    echo = "证据: chunk_a1b2, chunk_c3d4 患者PATIENT_001: 该结节为良性 # 输出 分级: TR5 # 输出 分级: TR4"
    assert p46.is_prompt_echo(echo) is True
    assert p46.is_prompt_echo("# 输出 分级: TR2 证据: kb_v1.3.0:pdf_x_0001#0000") is False
    assert p46.is_prompt_echo("甲状腺结节 TR3，建议复查。") is False


def test_degraded_answers_are_recognised():
    """降级答案不是真结论，必须单列（否则覆盖率分母被灌脏）。"""
    p46 = _load()
    assert p46.is_degraded("[thyroid] llm_unavailable: InferenceError; manual review required")
    assert p46.is_degraded("InferenceError")
    assert not p46.is_degraded("甲状腺结节 TR3，建议六个月复查。")
