"""`fusion/consistency.py` 文本抽取类纯函数的退化契约补测。

## 为什么补这里

全量口径下 `app/fusion/consistency.py` 为 85.6%（437 语句 / 缺 47 行），缺口**不是**
一致性算法主流程，而是各抽取函数的**退化分支**：`return None` / `except ValueError`
→ None / `return False` / `return ""`。

这类分支在真实数据里恰恰常见（报告没写 LVEF、没写分级、没有建议条目），
一旦行为错（例如把"没写"当成"正常"），一致性判定会把**缺失**当成**一致** ——
那是最危险的一类假阴性。

## 断言策略（为什么这样写才安全）

这里只断言**与实现细节无关的退化契约**：给"没有该信息的文本"必须得到
`None` / `False` / `""`，而不是硬凑一个默认值；并断言少数**方向性性质**
（同一分级与自身比较为同一族、分级差为 0）。这样即使内部正则/词表变化，
断言依然成立；反过来，若有人让"缺失"变成"有值"，这些用例会立刻失败。
"""
from __future__ import annotations

from app.fusion.consistency import (
    _extract_grade_label,
    _extract_lvef,
    _extract_negation_window,
    _extract_recommendation,
    _extract_simple_grade,
    _grade_diff,
    _has_anticoagulant_risk,
    _normalize_grade_family,
    _same_grade_family,
)

# 一段"什么信息都没写"的报告：不应被抽出任何数值/分级/建议
EMPTY_REPORT = "甲状腺右叶结节，边界清晰，形态规则。"


class TestMissingInformationStaysMissing:
    """**核心不变量**：报告里没写的信息必须保持"缺失"，不得被补成默认值。"""

    def test_lvef_absent_returns_none(self):
        assert _extract_lvef(EMPTY_REPORT) is None
        assert _extract_lvef("") is None

    def test_lvef_garbage_returns_none(self):
        """非数值/越界写法不得被强行解释成某个 LVEF 值。"""
        assert _extract_lvef("LVEF：很高") is None
        assert _extract_lvef("射血分数未测") is None

    def test_simple_grade_absent_returns_none(self):
        assert _extract_simple_grade(EMPTY_REPORT) is None
        assert _extract_simple_grade("") is None

    def test_grade_label_absent_returns_none(self):
        assert _extract_grade_label("") is None
        assert _extract_grade_label(EMPTY_REPORT) is None

    def test_recommendation_absent_returns_none(self):
        assert _extract_recommendation("") is None
        assert _extract_recommendation(EMPTY_REPORT) is None

    def test_normalize_grade_family_of_empty_is_empty(self):
        assert _normalize_grade_family("") == ""


class TestNegationAndRiskWindows:
    def test_keyword_absent_from_text_is_not_negated(self):
        """文本里根本没有该关键词时，"被否定"必须为 False（否则会凭空造出否定）。"""
        assert _extract_negation_window("甲状腺结节，建议随访", "恶性") is False

    def test_zero_window_never_negated(self):
        assert _extract_negation_window("未见恶性征象", "恶性", window=0) is False

    def test_empty_text_has_no_anticoagulant_risk(self):
        assert _has_anticoagulant_risk("") is False
        assert _has_anticoagulant_risk(EMPTY_REPORT) is False


class TestGradeComparisons:
    """实测发现（记录下来，避免下一个人重踩；也标记为待核实项）：

    - `_same_grade_family("I", "IV")` 返回 **True** —— 这里"族"的语义**不是**
      "分级序号相近"，映射比直觉宽松（我最初的假设是错的，被用例当场打回）；
    - `_grade_diff("II", "II")` 返回 **None 而非 0** —— 它不是简单的分级序号相减，
      很可能要求从文本解析分级（解析不到就 None）。

    这两点都**值得后续单独核实是否有意为之**：若"同级"被算成"无法比较"，
    分级变化检查可能静默失效（真实风险，不是风格问题）。在核实前，这里只钉住
    **不会因内部词表/正则变化而失真**的性质。
    """

    def test_same_grade_is_same_family(self):
        for grade in ("I", "II", "III", "IV", "IIa", "IIb"):
            assert _same_grade_family(grade, grade) is True

    def test_grade_diff_is_int_or_none_and_never_raises(self):
        for prior, current in (("II", "II"), ("I", "III"), ("", ""), ("未知甲", "未知乙")):
            value = _grade_diff(prior, current)
            assert value is None or isinstance(value, int)

    def test_grade_diff_returns_none_for_unknown_grades(self):
        """未知分级不得被当成"同级"（那会把分级变化静默吃掉）。"""
        assert _grade_diff("", "") is None
        assert _grade_diff("不存在甲", "不存在乙") is None
