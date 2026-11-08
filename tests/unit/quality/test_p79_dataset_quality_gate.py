"""A.166：评测数据集质量闸守卫。

背景：2026-11-08 全量核查 14 个自造测试集，发现无一能用于验收——
`consistency_test_cases` 156/780 重复 + BND 组同句自相矛盾、
`double_engine_eval_set` 86% 来自 3 个模板、`kb_qa_golden_v1` 含影像术语仅 11.8%。
这些集被用了半年、报出 98.53% / 79.56%，却从没人检查过它们本身能不能测。
本守卫把"能不能测"钉成可执行判据。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.track_quality

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p79_dataset_quality_gate.py"
_spec = importlib.util.spec_from_file_location("p79_gate", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_duplicate_detection_catches_exact_repeats():
    rate, samples = _mod.check_duplicates(["A 结节", "A 结节", "B 结节"])
    assert rate == pytest.approx(1 / 3)
    assert samples


def test_duplicate_rate_zero_for_unique_texts():
    rate, _ = _mod.check_duplicates(["甲", "乙", "丙"])
    assert rate == 0.0


def test_template_pollution_catches_shared_prefix():
    """前 18 字相同 ≥3 次即算模板污染。"""
    texts = ["自造演练数据：右叶低回声结节，边界清 " + str(i) for i in range(5)]
    rate, top = _mod.check_template_pollution(texts)
    assert rate == 1.0
    assert top and top[0][1] == 5


def test_template_pollution_zero_for_diverse_texts():
    texts = [
        "甲状腺右叶见低回声结节边界清晰",
        "肝左叶高回声灶考虑血管瘤可能",
        "心脏LVEF正常左室舒张功能减低",
        "右叶囊性结节后方回声增强透声好",
    ]
    rate, _ = _mod.check_template_pollution(texts)
    assert rate == 0.0


def test_imaging_coverage_separates_medical_from_patient_questions():
    """影像术语覆盖：临床描述应满分，患者问诊应接近 0（这正是 kb_qa_golden_v1 的缺陷）。"""
    clinical = ["甲状腺右叶低回声结节，边界清", "肝内见高回声灶，考虑血管瘤"]
    assert _mod.check_imaging_coverage(clinical) == 1.0

    patient = ["哪个医院治疗甲状腺结节病比较好", "看甲状腺瘤费用是多少钱"]
    assert _mod.check_imaging_coverage(patient) < 0.5


def test_contradiction_detection_catches_same_sentence_conflict():
    """一致性测试集 BND 组的真实缺陷：同句既说清晰又说欠清晰。"""
    texts = ["左叶见 2.1×1.8cm 边界清晰，边界欠清晰，形态规则"]
    found = _mod.check_contradictions(texts)
    assert len(found) == 1
    assert "边界清晰" in found[0]["pair"]


def test_contradiction_clean_text_passes():
    assert _mod.check_contradictions(["右叶低回声结节，边界清晰，形态规则"]) == []


def test_full_gate_fails_known_bad_dataset(tmp_path):
    """用 consistency_test_cases 的真实缺陷形态构造，必须 FAIL。"""
    p = tmp_path / "bad.jsonl"
    lines = []
    for _ in range(5):
        lines.append('{"prompt": "甲状腺超声检查：边界清晰，边界欠清晰，形态规则"}')
    p.write_text("\n".join(lines), encoding="utf-8")

    report = _mod.run_gate(p, "prompt")
    assert report["verdict"] == "FAIL"
    failed = {c["name"] for c in report["checks"] if not c["passed"]}
    assert "duplicate_rate" in failed
    assert "self_contradiction_count" in failed


def test_full_gate_passes_clean_dataset(tmp_path):
    p = tmp_path / "good.jsonl"
    lines = [
        '{"clinical_text": "甲状腺右叶8×6mm低回声结节，边界清晰，形态规则"}',
        '{"clinical_text": "肝左叶32×28mm高回声灶，边界清晰，考虑血管瘤"}',
        '{"clinical_text": "心脏超声LVEF 62%，左室舒张功能减低"}',
        '{"clinical_text": "右叶囊性结节6×5mm，后方回声增强"}',
        '{"clinical_text": "左叶实性低回声10×8mm，纵横比>1，微钙化"}',
    ]
    p.write_text("\n".join(lines), encoding="utf-8")

    report = _mod.run_gate(p, "clinical_text")
    assert report["verdict"] == "PASS", [c for c in report["checks"] if not c["passed"]]


def test_gate_errors_when_no_text_extracted(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text('{"foo": "bar"}', encoding="utf-8")
    report = _mod.run_gate(p, None)
    assert report["verdict"] == "ERROR"
