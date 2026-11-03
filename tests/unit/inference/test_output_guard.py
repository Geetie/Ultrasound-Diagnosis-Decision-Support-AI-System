"""A.70 守卫：模型输出复述提示词示例的识别规则。

真机教训：评测集 98.7%（155/169）的「结论」是模板照抄 —— `chunk_a1b2`/`PATIENT_001`/
重复 `# 输出` 块。识别规则必须能**在解析前**拦住它，且**不许误伤正常结论**。
"""

from __future__ import annotations

from app.inference.output_guard import (
    detect_patient_token_leak,
    detect_prompt_echo,
    has_real_evidence_id,
)


def test_placeholder_chunk_id_is_detected():
    text = "证据: chunk_a1b2, chunk_c3d4 鉴别诊断: 1. 乳头状癌"
    assert detect_prompt_echo(text) == "placeholder_chunk_id"


def test_patient_token_alone_is_leak_not_echo():
    """A.77 更正：`PATIENT_001` 单独出现是**语料脱敏标记外泄**，不是模板回显。

    真机：KB 里 22 个块含 `PATIENT_`（对话语料脱敏用它顶替患者称呼），
    模型在患者解释段落抄了出来；三份 prompt 里没有这个串。
    首版把它当回显 → 660 条已删记录里 415 条是误判。
    """
    text = "患者PATIENT_001: 该结节为良性，无需进一步检查。"
    assert detect_prompt_echo(text) is None
    assert detect_patient_token_leak(text) is True


def test_patient_token_with_echo_signals_is_echo():
    """PATIENT_ 与确凿回显特征（`# 输出` / `chunk_`）同时出现时才计入回显。"""
    text = "# 输出 分级: TR2 患者PATIENT_001: 该结节为良性"
    assert detect_prompt_echo(text) == "placeholder_patient_id"


def test_patient_token_leak_helper_is_precise():
    assert detect_patient_token_leak("见 PATIENT_007 医生") is True
    assert detect_patient_token_leak("甲状腺结节 TR3") is False
    assert detect_patient_token_leak("") is False


def test_repeated_output_header_with_placeholder_ids_is_detected():
    """重复标题 + 占位证据 ID = 确凿回显（A.150 之后的判据）。

    变更背景（真机 G21 实测）：Qwen2.5-7B 在含 "# 输出示例" 的 prompt 下，
    **即使结论分级/理由齐全且无占位 ID**，也会把 "# 输出" 当格式标记输出
    7~15 次 → 旧规则 headers>=2 即拒，把整批真采样吃了（4/4 例全误拒）。
    现在：重复标题**且**含占位 chunk/PATIENT 才判回显；
    纯标题重复交下游分级解析决定成败。
    """
    text = "# 输出 分级: TR2 证据: chunk_a1b2 # 输出 分级: TR5 证据: chunk_c3d4"
    assert detect_prompt_echo(text) == "placeholder_chunk_id"


def test_repeated_header_without_echo_features_is_not_blocked():
    """真机形态：标题重复 10 次但结论齐全、无占位 ID → 不判回显。"""
    text = "# 输出\n分级: TR3\n理由: 边界清，形态规则。\n" * 10
    assert detect_prompt_echo(text) is None


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
