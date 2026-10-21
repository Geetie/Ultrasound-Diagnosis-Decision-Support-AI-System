"""A.77 守卫：备份复查必须按"确凿证据"分类，而不是按单一标记。

我上一轮的错误就是"单一标记即定罪"（`PATIENT_001` → 回显）。这里钉住三件事：
1. `chunk_a1b2` / 重复 `# 输出` 才算**确凿回显**；
2. `chunk_1` 这类假编号单独算 `fake_citation`（另一个缺陷，不该被删）；
3. `PATIENT_001` 单独出现只算 `patient_token_leak`（语料脱敏标记外泄），**不算回显**。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = (
    Path(__file__).resolve().parents[3]
    / "scripts"
    / "audit"
    / "p58_reclassify_deleted_backups.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_p58_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_placeholder_chunk_is_template_echo():
    p58 = _load()
    assert "template_echo" in p58.classify("分级: TR2 证据: chunk_a1b2, chunk_c3d4")


def test_repeated_output_header_is_template_echo():
    p58 = _load()
    assert "template_echo" in p58.classify("# 输出 TR2 … # 输出 TR5 …")


def test_fake_numbered_chunk_is_not_echo():
    p58 = _load()
    tags = p58.classify("分级: LR-3 证据: chunk_001, chunk_002")
    assert tags == ["fake_citation"]
    assert "template_echo" not in tags


def test_patient_token_alone_is_leak_not_echo():
    p58 = _load()
    tags = p58.classify("患者PATIENT_001: 解释: 建议复查")
    assert tags == ["patient_token_leak"]


def test_clean_conclusion_has_no_defect():
    p58 = _load()
    assert p58.classify("甲状腺 · TR3 — 分级: TR3 理由: 实性低回声 证据: [1], [2]") == ["clean"]


def test_conclusion_of_matches_pipeline_shape():
    p58 = _load()
    assert p58.conclusion_of({"fusion_result": {"content": "正文"}}) == "正文"
    assert p58.conclusion_of({"recommendations": "建议"}) == "建议"
    assert p58.conclusion_of(None) == ""
