"""Unit tests for app.rag.query_expansion（纯函数，无 IO）。

覆盖：
- 原文不动：扩展是**追加**，原查询一字不改
- 空输入 / 无命中：原样返回（不返回空串、不带尾随空格）
- 各条映射真的会触发（TI-RADS / C-TIRADS / LI-RADS / LVEF / FNA / CEUS /
  VRR / 2D-SWE / APHE / washout / A4C / PSMV / SC）
- **边界不误伤**：`SC` 不吃 `scHCC`、`LRM` 不吃 `LR-M` 规则、
  `TRA` 不吃 `TR1-5` 规则、`RIF` 不吃 `RI` 规则
- 去重：原文已有的词不重复追加
- 上限：单条查询扩展词不超过 MAX_TOTAL_TERMS
- `enabled=False` 旁路（A/B 的 off 组）
"""
from __future__ import annotations

import pytest

from app.rag.query_expansion import (
    MAX_TOTAL_TERMS,
    expand_query,
    matched_terms,
)

pytestmark = [pytest.mark.track_rag]


# ------------------------------------------------------------------ 基本契约
def test_empty_input_returned_as_is() -> None:
    assert expand_query("") == ""


def test_whitespace_input_returned_as_is() -> None:
    assert expand_query("   ") == "   "


def test_no_match_returns_original_exactly() -> None:
    query = "普通囊性结节需要处理吗"
    assert expand_query(query) == query


def test_expansion_is_append_only_original_never_modified() -> None:
    query = "TI-RADS 4A 类结节要不要穿刺？"
    out = expand_query(query)
    assert out.startswith(query)
    assert out != query


def test_disabled_returns_original() -> None:
    query = "LVEF 三档区间"
    assert expand_query(query, enabled=False) == query


def test_no_match_returns_no_trailing_space() -> None:
    assert not expand_query("普通结节").endswith(" ")


# ------------------------------------------------------------------ 每条映射
@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("TI-RADS 5 类怎么报", "甲状腺影像报告和数据系统"),
        ("C-TIRADS 计数法怎么用", "甲状腺影像报告和数据系统"),
        ("LI-RADS 的 LR-5 判定", "肝脏影像报告和数据系统"),
        ("LVEF 轻度减低区间", "左心室射血分数"),
        # 注意：查询里已经有「左心室射血分数」「射血分数」时，这两个词会被
        # 去重规则跳过（不重复追加），只剩 `LV EF` 是新增 —— 见
        # test_substring_of_original_is_deduped。
        ("左心室射血分数正常值", "LV EF"),
        ("FNA 什么情况下做", "细针穿刺"),
        ("CEUS 强化模式", "超声造影"),
        ("热消融后 VRR 多少算成功", "体积缩小率"),
        ("2D-SWE 测出 9.5 kPa", "二维剪切波弹性成像"),
        ("APHE 加 washout", "动脉期高增强"),
        ("washout 的判断标准", "廓清"),
        ("A4C 切面怎么打", "心尖四腔心切面"),
        ("PSMV 切面测什么", "胸骨旁短轴切面"),
        ("SC 切面评右心", "剑突下切面"),
        ("CDFI 血流信号有用吗", "彩色多普勒血流成像"),
    ],
)
def test_rule_fires(query: str, expected: str) -> None:
    assert expected in matched_terms(query)


# ------------------------------------------------------------------ 边界安全
@pytest.mark.parametrize(
    ("query", "must_not_appear"),
    [
        # scHCC 里含 `SC`：ASCII 边界必须挡住
        ("1cm 以下亚厘米肝癌（scHCC）", "剑突下"),
        # TRA 含 `TR`：TR1-5 规则不得命中
        ("TRA 无关词", "分级"),
        # RIF 含 `RI`：RI 规则必须只在独立词上命中
        ("RIF 无关", "阻力指数"),
        # LRM / LR5 不带连字符：不展开（放宽会误吃字母串）
        ("LRM 无关", "LR-M"),
        ("LR5 无关", "LR-5"),
        # SCHC
        ("SCHC 无关", "剑突下"),
    ],
)
def test_boundary_does_not_over_match(query: str, must_not_appear: str) -> None:
    assert must_not_appear not in matched_terms(query)


def test_lr_with_dash_still_matches() -> None:
    assert "LR-5 明确 HCC" in matched_terms("LR-5 判定")


def test_lr_with_spaces_around_dash_still_matches() -> None:
    assert "LR-5 明确 HCC" in matched_terms("LR - 5 判定")


# ------------------------------------------------------------------ 去重与上限
def test_substring_of_original_is_deduped() -> None:
    """原文已含的扩展词（含被原文包含的情况）不再追加。"""
    # 「左心室射血分数」里已包含「射血分数」→ 不再追加「射血分数」
    assert "射血分数" not in matched_terms("左心室射血分数正常值")
    assert matched_terms("左心室射血分数正常值") == ["LV EF"]


def test_existing_term_not_appended_twice() -> None:
    query = "甲状腺影像报告和数据系统 TI-RADS 4 类"
    out = expand_query(query)
    assert out.count("甲状腺影像报告和数据系统") == 1


def test_no_duplicate_terms() -> None:
    terms = matched_terms("TI-RADS、C-TIRADS、4A 类、4B 类、4C 类")
    assert len(terms) == len(set(terms))


def test_total_terms_capped() -> None:
    query = "TI-RADS C-TIRADS LI-RADS LR-5 LR-M APHE washout LVEF FNA CEUS VRR 2D-SWE A4C PSMV SC CDFI RI"
    assert len(matched_terms(query)) <= MAX_TOTAL_TERMS


# ------------------------------------------------------------------ B2 实测抽样
def test_b2_query_with_no_abbreviation_is_untouched() -> None:
    """纯中文、无缩写的 B2 查询不应被扩展（避免给 40 条以外的查询引入噪声）。"""
    query = "边缘模糊但其余征象都阴性、形态规则也不垂直位，这个结节要加几分？"
    assert expand_query(query) == query


def test_b2_thyroid_c_tirads_query_expands() -> None:
    query = "结节实性、低回声、纵横比>1、内部点状强回声，按 C-TIRADS 计数法应积几分、归几类？"
    out = expand_query(query)
    assert "甲状腺影像报告和数据系统" in out
    assert out.startswith(query)
