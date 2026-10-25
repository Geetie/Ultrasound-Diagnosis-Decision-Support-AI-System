"""A.90 守卫：`患者PATIENT_00N` 语料标记清洗——**判据两条独立证据、幂等、可还原**。

## 为什么这份守卫必须存在（A.77 的血泪）

2026-10-21 我曾把"答案里出现 `PATIENT_001`"当成"模板回显铁证"，
**误删 415 条真结论** —— 它们有分级、有鉴别诊断，只是把语料脱敏标记
抄进了「患者解释」段落（prompt 里根本没有该串，KB 里倒有 22 块）。
详见 16 号附录 A.77。

所以清洗脚本的判据必须：
1. **形态证据**：占位符真的被**当人名用**（`患者PATIENT_001` / `PATIENT_010医生`），
   而不是"文中任意位置出现 PATIENT_"；
2. **排除模板回显**：同时出现 `chunk_xxxx` 这类模板假 ID 的，属于 A.70 的
   模板回显（处置是**拒收整条**，不是清洗）→ 本脚本**必须跳过**，不许动。

## 这份守卫钉什么

* 只清"人名位置"的占位符，**不动**真实证据引用与分级/置信度；
* `chunk_a1b2` 同时出现 → 判 `skip_template_echo`（反证：去掉这条排除即红）；
* 无 `PATIENT_` 的干净文本 → `skip_no_token`，一个字都不改；
* `clean_text` **幂等**（清完再清，结果不变）—— 防止重复运行改坏；
* `clean_text` 不改掉分级行（`分级: TR4` 必须原样保留）。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_quality]

_SCRIPT = (
    Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p63_patient_token_cleanup.py"
)


def _load():
    """按路径加载探针脚本（它在 scripts/ 下，不是包）。"""
    spec = importlib.util.spec_from_file_location("p63_cleanup", _SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["p63_cleanup"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def p63():
    return _load()


class TestCleanText:
    def test_patient_prefix_with_colon(self, p63):
        got, hits = p63.clean_text("患者PATIENT_001: 解释: 请复查")
        # 清洗后是「患者：解释」——原串里的半角冒号被统一成中文冒号
        assert got.startswith("患者：")
        assert "PATIENT_" not in got
        assert hits and hits[0]["kind"] == "patient_prefix_with_colon"

    def test_token_as_title_prefix(self, p63):
        got, hits = p63.clean_text("安抚: PATIENT_010医生建议活检")
        assert "PATIENT_" not in got
        assert "医生建议活检" in got
        assert any(h["kind"] == "token_as_title_prefix" for h in hits)

    def test_full_chinese_colon_variant(self, p63):
        got, _ = p63.clean_text("患者PATIENT_003：解释: x")
        assert got.startswith("患者：")

    def test_keeps_grading_line(self, p63):
        src = "甲状腺 · TR4 — 分级: TR4 总分: 4 置信度: 0.9 患者PATIENT_001: 解释: x"
        got, _ = p63.clean_text(src)
        assert "分级: TR4" in got
        assert "置信度: 0.9" in got
        assert "PATIENT_" not in got

    def test_idempotent(self, p63):
        src = "患者PATIENT_001: 解释: x 安抚: PATIENT_010医生建议"
        once, _ = p63.clean_text(src)
        twice, hits2 = p63.clean_text(once)
        assert once == twice, "清洗不幂等 → 重复运行会改坏正文"
        assert hits2 == []

    def test_clean_text_noop_on_clean_input(self, p63):
        src = "甲状腺 · TR2 分级: TR2 置信度: 0.85"
        got, hits = p63.clean_text(src)
        assert got == src
        assert hits == []


class TestClassifyTwoIndependentEvidences:
    """**核心**：必须"形态 + 排除模板回显"两条都过才清（A.77 误删 415 条的根因）。"""

    def test_human_like_token_is_cleanable(self, p63):
        v = p63.classify("分级: TR3 证据: [1], [2] 患者PATIENT_001: 解释: x")
        assert v["action"] == "clean"
        assert v["human_like"] >= 1
        assert v["template_chunk"] == 0

    def test_template_echo_must_be_skipped(self, p63):
        """反证腿：把"排除模板回显"这条删掉 → 本用例红（会去清洗该拒收的脏数据）。"""
        v = p63.classify("患者PATIENT_001: 解释: x 证据: chunk_a1b2")
        assert v["action"] == "skip_template_echo"
        assert v["template_chunk"] >= 1

    def test_no_token_is_skipped(self, p63):
        v = p63.classify("甲状腺 · TR2 分级: TR2 置信度: 0.85")
        assert v["action"] == "skip_no_token"

    def test_real_evidence_not_confused_with_token(self, p63):
        """真实证据引用（kb_v1.3.0:…#0000）不能被当占位符。"""
        v = p63.classify("分级: TR4 证据: kb_v1.3.0:pdf_x_0001#0000")
        assert v["action"] == "skip_no_token"
        assert v["real_evidence"] >= 1


class TestScriptSafetyGates:
    """硬规则 14：默认 dry-run；--apply 必须先有备份。"""

    def test_default_is_dry_run(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        assert 'ap.add_argument("--apply", action="store_true"' in src
        assert "[dry-run] 未修改数据库" in src

    def test_apply_requires_existing_backup(self):
        src = _SCRIPT.read_text(encoding="utf-8")
        assert "is_file()" in src, "apply 前没有校验备份文件存在"
        assert "[FATAL] --apply 要求先写备份" in src

    def test_backup_contains_full_findings(self):
        """备份必须含**完整 findings**（可完整还原），不是只存正文。"""
        src = _SCRIPT.read_text(encoding="utf-8")
        assert '"findings": r["findings"]' in src

    def test_apply_marks_cleaned_records(self):
        """清洗后要留标记（可审计哪些被改过）。"""
        src = _SCRIPT.read_text(encoding="utf-8")
        assert 'fr["patient_token_cleaned"] = True' in src
        assert 'fr["patient_token_clean_hits"]' in src
