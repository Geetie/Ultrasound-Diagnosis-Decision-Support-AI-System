"""A.169：人工撰写数据集的可信度审计守卫。

p79 查统计卫生（重复率/模板率/术语覆盖/自相矛盾），
p80 查"人工撰写"的可信度（思维多样性/句式变化/前缀重复）。
两者互补：p79 挡住脚本批量拼装，p80 挡住"一个人用一种思路写 100 条"。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

pytestmark = pytest.mark.track_quality

_SCRIPT = pathlib.Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p80_manual_dataset_audit.py"
_spec = importlib.util.spec_from_file_location("p80_audit", _SCRIPT)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _row(q, t="grading", n=1):
    return {"query": q, "query_type": t, "case_id": f"c{n}"}


def test_empty_rows_is_error():
    assert _mod.audit([])["verdict"] == "ERROR"


def test_template_generated_dataset_fails_prefix_check():
    """前 8 字重复 ≥5 次 = 有模板。"""
    rows = [_row(f"甲状腺超声检查：第 {i} 个不同内容的结节描述", n=i) for i in range(10)]
    r = _mod.audit(rows)
    check = next(c for c in r["checks"] if c["name"] == "prefix_repetition")
    assert check["passed"] is False


def test_diverse_manual_dataset_passes():
    """人工写的：类型分散 + 长短不一 + 开头各异。"""
    rows = [
        _row("8mm 低回声结节，边界清，形态规则，无钙化，TI-RADS 该判几类？", "grading", 1),
        _row("桥本甲状腺炎合并 4a 类结节，穿刺指征怎么把握呢", "indication", 2),
        _row("良性结节需要多久复查一次比较合适？", "followup", 3),
        _row("肝内 2cm 低回声灶伴 APHE 阳性、廓清阴性，LI-RADS 判几类", "grading", 4),
        _row("LVEF 45% 属于哪一级心功能不全，需要怎么处理", "severity", 5),
        _row("纵横比大于一但没看到微钙化，风险该怎么估", "risk", 6),
        _row("细针穿刺的禁忌症有哪些", "indication", 7),
        _row("甲状腺结节从 4a 升到 4c 意味着什么变化", "comparison", 8),
        _row("囊性结节内部出现实性成分，处理策略和纯囊性有何不同", "comparison", 9),
        _row("超声造影在肝结节鉴别中的价值体现在哪儿", "modality", 10),
        _row("儿童甲状腺结节的管理与成人有区别吗", "special_population", 11),
        _row("复查时发现结节增大 20%，按什么标准判断有意义", "followup", 12),
    ]
    r = _mod.audit(rows)
    scored = [c for c in r["checks"] if c.get("passed") is not None]
    assert all(c["passed"] for c in scored), [c for c in scored if not c["passed"]]


def test_type_concentration_detected():
    """90% 都是同一类 = 思维单一。"""
    rows = [_row(f"这是第{i}个不同的问法但有独立内容", "grading", i) for i in range(20)]
    rows += [_row(f"另一个类型第{i}条内容各不相同", "other", 100 + i) for i in range(2)]
    r = _mod.audit(rows)
    check = next(c for c in r["checks"] if c["name"] == "type_concentration")
    assert check["passed"] is False


def test_identical_lengths_detected():
    """所有条目长度一模一样 = 套模板（用**绝对跨度**判定）。"""
    # 构造 30 条长度完全相同的（只有中间一个字符不同）
    rows = [_row(f"描述第{i}个内容各异的甲状腺结节超声检查结果说明文字", "grading", i) for i in range(30)]
    # 长度必须完全一致才能测出跨度 0
    rows = [{"query": "甲" * 20 + str(i % 10), "query_type": "grading", "case_id": str(i)} for i in range(30)]
    r = _mod.audit(rows)
    check = next(c for c in r["checks"] if c["name"] == "length_span")
    assert check["passed"] is False, f"跨度 {check['value']} 应被判不合格"


def test_natural_length_variation_passes():
    """临床提问天然长度在 20~50 字之间波动，CV 低但跨度够 —— 这是合格的。

    2026-11-08 教训：首版用变异系数 ≥0.25 判定，把真人逐条手写的 120 条中文检索集
    判成 FAIL（实测 CV=0.18），读原文后发现那批数据完全合格。
    """
    rows = [
        _row("为什么指南说不用回声均匀程度来判断良恶性？", "grading", 1),          # 21 字
        _row("甲状腺结节 C-TIRADS 4 类里，实性、低回声的 4A/4B/4C 恶性率各是多少？", "grading", 2),  # 45 字
        _row("多灶 4a 类实性结节，最大一个 12mm 且紧贴被膜，是不是该穿？", "indication", 3),
        _row("随访中的实性结节，长到多少才算增大需要重新评估？", "followup", 4),
    ] * 8
    r = _mod.audit(rows)
    check = next(c for c in r["checks"] if c["name"] == "length_span")
    assert check["passed"] is True
    # 变异系数仍然低（说明为什么不能用它判定）
    assert "变异系数" in check["detail"]


def test_small_dataset_skips_distribution_checks():
    """小样本时不做分布分析（但前缀检查照做）。"""
    rows = [_row("短问题？", "t", i) for i in range(3)]
    r = _mod.audit(rows)
    length_check = next(c for c in r["checks"] if c["name"] == "length_span")
    assert length_check["passed"] is None


def test_missing_type_field_is_skipped_not_failed():
    rows = [{"query": f"不同内容第{i}条问题描述", "case_id": str(i)} for i in range(10)]
    r = _mod.audit(rows)
    type_check = next(c for c in r["checks"] if c["name"] == "type_concentration")
    assert type_check["passed"] is None  # 跳过，不算失败
