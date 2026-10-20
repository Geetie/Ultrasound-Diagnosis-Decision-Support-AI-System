"""A.70 守卫：模型输出复述提示词示例的识别规则。

真机教训：评测集 98.7%（155/169）的「结论」是模板照抄 —— `chunk_a1b2`/`PATIENT_001`/
重复 `# 输出` 块。识别规则必须能**在解析前**拦住它，且**不许误伤正常结论**。
"""

from __future__ import annotations

from app.inference.output_guard import detect_prompt_echo, has_real_evidence_id


def test_placeholder_chunk_id_is_detected():
    text = "证据: chunk_a1b2, chunk_c3d4 鉴别诊断: 1. 乳头状癌"
    assert detect_prompt_echo(text) == "placeholder_chunk_id"


def test_placeholder_patient_id_is_detected():
    text = "患者PATIENT_001: 该结节为良性，无需进一步检查。"
    assert detect_prompt_echo(text) == "placeholder_patient_id"


def test_repeated_output_header_is_detected():
    text = "# 输出 分级: TR2 总分: 2 … # 输出 分级: TR5 总分: 14 …"
    assert detect_prompt_echo(text) == "repeated_output_header"


def test_single_output_header_alone_is_not_blocked():
    """只出现一次 `# 输出` 不足以判定照抄（可能只是模型的排版习惯）。"""
    text = "# 输出 分级: TR3 总分: 6 理由: 实性结节"
    assert detect_prompt_echo(text) is None


def test_normal_conclusion_is_not_blocked():
    text = (
        "甲状腺：右叶低回声结节，边界清，形态规则，无钙化。分级 TR3，"
        "建议六个月复查；证据：kb_v1.3.0:pdf_thyroid_ctirads_2021_0003#0000。"
    )
    assert detect_prompt_echo(text) is None


def test_empty_text_is_not_blocked():
    assert detect_prompt_echo("") is None
    assert detect_prompt_echo(None) is None  # type: ignore[arg-type]


def test_real_evidence_id_helper_is_exact_match():
    ids = ["kb_v1.3.0:pdf_x_0001#0000"]
    assert has_real_evidence_id("见 kb_v1.3.0:pdf_x_0001#0000 段", ids) is True
    assert has_real_evidence_id("见 chunk_a1b2 段", ids) is False
    assert has_real_evidence_id("任何文本", []) is False
