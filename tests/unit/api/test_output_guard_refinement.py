"""A.150：`# 输出` 重复 ≠ 回显（真机 G21 实测 4 例全被误拒）。"""

from __future__ import annotations

import pytest

from app.inference.output_guard import detect_prompt_echo

pytestmark = pytest.mark.track_api


def _valid_conclusion(repeats: int) -> str:
    body = "# 输出\n分级: TR3\n理由: 边界清，形态规则。\n"
    return body * repeats


def test_valid_conclusion_with_repeated_header_not_rejected():
    """分级/理由齐全、无占位 ID —— 即使 # 输出 出现多次也不算回显。"""
    assert detect_prompt_echo(_valid_conclusion(10)) is None


def test_placeholder_chunk_still_rejected():
    assert detect_prompt_echo("证据: chunk_a1b2\n分级: TR3") == "placeholder_chunk_id"


def test_placeholder_patient_with_echo_features_still_rejected():
    text = "# 输出\n分级: TR3\n患者PATIENT_001: 解释: x"
    assert detect_prompt_echo(text) == "placeholder_patient_id"


def test_pure_repetition_with_placeholders_rejected():
    text = "# 输出\n分级: TR3\nchunk_a1b2\n" * 5
    assert detect_prompt_echo(text) is not None
