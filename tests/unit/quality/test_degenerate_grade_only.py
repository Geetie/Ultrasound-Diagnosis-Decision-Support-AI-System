"""「只有分级码、没有正文」的退化结论检测（2026-11-08）。

## 缺陷

生产实测 **32.50% 的输入**产出了 `甲状腺 · TR1` / `TI-RADS 4a` 这种退化结论。

根因：II-Medical-8B 的 chat template 不读 `enable_thinking`，token 全花在 `<think>` 内，
`finish_reason=length`，引擎剥除后正文为空，只剩分级码。

**旧的 output_guard 不拦** —— 它既不空、也不是回显，于是静默落库，
让「分级一致率」把「模型没写正文」当成「模型不稳定」来度量。
"""

from __future__ import annotations

import pytest

from app.inference.output_guard import detect_degenerate_grade_only

pytestmark = pytest.mark.track_inference


@pytest.mark.parametrize(
    "text",
    [
        "甲状腺 · TR1",
        "TI-RADS 4a",
        "TR3",
        "甲状腺：5类",
        "LI-RADS 4",
        "肝脏·正常",
        "心脏 · 无异常",
        "  TI-RADS 4b  ",
    ],
)
def test_grade_only_is_detected(text):
    """纯分级码（可带器官前缀/分隔符）必须被识别为退化结论。"""
    assert detect_degenerate_grade_only(text) == "grade_only_no_body"


@pytest.mark.parametrize(
    "text",
    [
        "甲状腺 · TI-RADS 4a 类，结节 8mm，形态规则，边界清，建议 6 个月复查",
        "TI-RADS 3 类。右叶实性结节 6mm，纵横比小于 1，无微钙化。建议每年复查一次超声。",
        "肝内低回声灶 3cm，考虑血管瘤可能。建议增强 CT 进一步确认，3 个月后复查。",
        "本次检查未见明显异常，各项测值均在参考范围内，建议按常规体检随访。",
    ],
)
def test_normal_conclusion_is_not_flagged(text):
    """有实质正文的结论不能被误判（假阳性会让真结论被拒收）。"""
    assert detect_degenerate_grade_only(text) is None


def test_empty_text_is_not_this_defect():
    """完全空文本由 finish_reason == 'length_empty' 报，不在这里重复判。

    两个缺陷分开报，才能在日志里区分「模型没输出」与「模型只输出了分级码」。
    """
    assert detect_degenerate_grade_only("") is None
    assert detect_degenerate_grade_only("   ") is None


def test_none_is_safe():
    assert detect_degenerate_grade_only(None) is None


def test_threshold_is_configurable():
    """min_body_chars 可调，默认 6。

    为什么默认是 6 而不是 20：临床短建议（`建议复查`=4字、`建议穿刺`=4字）很常见，
    阈值 20 会把它们全误杀。6 字足以区分「有建议」与「只有码」。
    """
    # 整串只有分级码 → 命中
    assert detect_degenerate_grade_only("TI-RADS 3") == "grade_only_no_body"
    # 带 4 字建议 → 不命中（默认 6 之下仍算有正文？不，4<6 会命中）
    # 所以用 8 字建议验证
    assert detect_degenerate_grade_only("TI-RADS 3，建议半年后复查", min_body_chars=6) is None
    # 阈值调高 → 同一句被判退化
    assert detect_degenerate_grade_only("TI-RADS 3，建议复查", min_body_chars=20) == "grade_only_no_body"


def test_trailing_content_prevents_flagging():
    """有实质建议的短结论不算退化 —— 避免误杀（假阳性比漏报更糟）。

    这些是**临床上真实存在的极短结论**，它们有建议、有下一步，
    与「只有 TR1 三个字」是两回事。
    """
    for t in (
        "甲状腺 · TI-RADS 4a 类，建议半年后复查",  # 8 字建议
        "TR3，建议一年复查一次",                    # 8 字建议
        "4a 类，建议穿刺活检明确",                   # 8 字建议
    ):
        assert detect_degenerate_grade_only(t) is None, f"误判: {t!r}"


# ---------------------------------------------------------------------------
# 自造分档规则检测（2026-11-08）
#
# 生产实测：模型输出 `（注：0-2 分为 TR2）`，而 KB 与 prompt 对 `"0-2 分"`
# 各 0 命中 → 是模型自己编的规则。15 条候选全部来自 10/25~11/07 的新判读，
# 说明现网仍在犯。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "（注：0-2 分为 TR2）",
        "（注：0-2分为TR2，3-4分为TR3）",
        "结节2分，归TR3类",
        "3-4分对应TR4a",
        "总分 5 分是 TR5",
        "1分是4A类",
    ],
)
def test_self_made_grading_rule_detected(text):
    """数值→类别的映射，在知识库无出处时必须被判为自造规则。"""
    from app.inference.output_guard import detect_self_made_grading_rule

    assert detect_self_made_grading_rule(text) == "self_made_grading_rule"


@pytest.mark.parametrize(
    "text",
    [
        "TI-RADS 4a 类，建议复查",
        "结节 8mm，低回声，边界清，建议半年复查",
        "建议 6 个月后复查",
        "TI-RADS 4 类，2 分。建议穿刺",  # 有分值但没说"分→类"的映射
        "本次检查未见异常，按常规体检随访",
        # 2026-11-09 真机假阳性（P.216 实测）：评分算式里的 `2 分 = 5 分` 曾被
        # 当成"2 分对应 TR5" → 一条**正常算分**的结论被判"编造规则"。
        # 修法：映射必须是"带分级前缀"或"带「类」后缀"，裸数字不再算。
        "钙化 1 分（点状强回声）+ 低回声 2 分 + 形状不规则 2 分 = 5 分 → TR3",
        "实性(3) + 低回声(2) + 纵横比>1(3) = 8 分 → TR5",
        "理由: 低回声(2) + 边界清(0) = 2 分 → TR2（不怀疑恶性）",
    ],
)
def test_normal_text_not_flagged_as_self_made_rule(text):
    """不能误伤正常结论 —— 假阳性会让真结论被拒收。"""
    from app.inference.output_guard import detect_self_made_grading_rule

    assert detect_self_made_grading_rule(text) is None, f"误判: {text!r}"


def test_kb_has_rule_bypasses_detection():
    """知识库确实有这条规则时不该拒收。

    调用方必须**真的去查**知识库再传 ``kb_has_rule=True``，不能默认传 True。
    """
    from app.inference.output_guard import detect_self_made_grading_rule

    assert (
        detect_self_made_grading_rule("（注：0-2 分为 TR2）", kb_has_rule=True) is None
    )


def test_none_is_safe():
    from app.inference.output_guard import detect_self_made_grading_rule

    assert detect_self_made_grading_rule(None) is None
    assert detect_self_made_grading_rule("") is None
