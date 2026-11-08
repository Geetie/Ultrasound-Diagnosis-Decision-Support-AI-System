"""B3：患者解释的**可读性**检查（不需要医生，判据来自 CDC Clear Communication Index）。

## 为什么这块不该等医生（用户指出的关键区分）

| 问题 | 需要谁 |
|---|---|
| 这段话说得**对不对**（医学正确性） | 医生 |
| 这段话说得**清不清楚** | **不需要医生**，有公认标准 |

科普写作有研究验证过的判据：CDC Clear Communication Index 20 项
（One Main Message / Common words / Active voice / Call to Action /
Explain the nature of the risk / What We Know and Don't Know），
基于 Plain Writing Act 2010 与 National Action Plan to Improve Health Literacy。
https://www.cdc.gov/ccindex/tool/index.html

这些都能离线确定性计算——不需要模型、不需要医生、不花钱。
"""

from __future__ import annotations

import pytest

from app.knowledge.explanation_readability import (
    MAX_SENTENCE_CHARS,
    check_readability,
)

pytestmark = pytest.mark.track_knowledge

#: 反面样本：把一整段专业报告塞给患者
BAD = (
    "甲状腺右叶低回声结节，边界欠清，形态不规则，纵横比大于一，内见微钙化，"
    "CDFI示血流信号丰富，TI-RADS 4b 类，恶性风险约 10-50%，需结合临床进一步评估，"
    "必要时行细针穿刺活检，另需注意避免剧烈运动并定期复查甲状腺功能及颈部淋巴结超声。"
)

#: 正面样本：讲清楚、给下一步
GOOD = (
    "您这次检查发现右边甲状腺有个小结节，大小约 8 毫米。"
    "从目前看，多数情况下是良性的，不用太担心。"
    "建议您 6 个月后再来复查一次超声，看看它有没有变化。"
)


def test_empty_text_is_not_evaluated():
    """空文本 = 未评估，不是通过。"""
    r = check_readability(None)
    assert r.evaluated is False
    assert r.passed is False
    assert check_readability("   ").evaluated is False


def test_bad_example_fails_with_multiple_codes():
    r = check_readability(BAD)
    codes = set(r.codes())
    assert r.passed is False
    # 至少抓到这几类结构问题
    assert "too_many_ideas" in codes
    assert "long_sentence" in codes
    assert "jargon_density" in codes
    assert "passive_or_vague" in codes
    assert "risk_not_explained" in codes


def test_good_example_passes():
    r = check_readability(GOOD)
    assert r.passed is True, f"不该报错: {[(i.code, i.detail) for i in r.issues]}"
    assert r.metrics["avg_sentence_chars"] <= MAX_SENTENCE_CHARS


def test_long_sentence_detected():
    long_one = "您" + "结节" * 30 + "。"
    r = check_readability(long_one)
    assert "long_sentence" in r.codes()


def test_no_next_step_detected():
    r = check_readability("您这个结节看起来是良性的，不用太担心。")
    assert "no_next_step" in r.codes()


def test_next_step_present_does_not_trigger():
    r = check_readability("您这个结节看起来是良性的。建议您半年后来复查。")
    assert "no_next_step" not in r.codes()


def test_uncertainty_conflict_detected():
    """既说「可能」又说「一定」= 口径矛盾。"""
    r = check_readability("这个结节可能是良性的，但一定是良性的，您半年后复查即可。")
    assert "uncertainty_hidden" in r.codes()


def test_scare_language_detected():
    r = check_readability("这个结节再不管就会癌变，建议您尽快来复查。")
    assert "scare_language" in r.codes()


def test_jargon_explained_does_not_trigger_density():
    """术语给了通俗解释时，不算「未解释术语」。"""
    explained = (
        "您这个结节在超声上叫「低回声」，也就是看起来比周围组织暗一些。"
        "这种多数是良性的。建议您半年后复查一次。"
    )
    r = check_readability(explained)
    assert "jargon_density" not in r.codes()


def test_missing_second_person_detected():
    r = check_readability("该结节考虑良性，建议半年复查超声。")
    assert "passive_or_vague" in r.codes()


def test_metrics_are_reported():
    r = check_readability(GOOD)
    assert r.metrics["chars"] > 0
    assert r.metrics["sentences"] >= 2
    assert "jargon_per_100" in r.metrics


def test_passed_only_means_structurally_clear():
    """边界声明：结构清楚 ≠ 医学正确（守卫自身也要守住这个语义）。"""
    # 一句结构完美但内容胡说八道的话，结构检查会放过它
    nonsense = "您这个结节是良性的。建议您半年后复查一次超声。"
    assert check_readability(nonsense).passed is True
    # 这说明本模块只管结构，不管医学正确性 —— 后者仍需医生


# ---------------------------------------------------------------------------
# A.170：用 B3 黄金集实测发现的六个缺陷（每条都是真机复现过才修的）
# ---------------------------------------------------------------------------


def test_scare_language_catches_real_scare_phrasing():
    """缺陷 1：首版 7 个词只抓到 1/7。真实恐吓不说"致命"，说"就晚了""要有心理准备"。"""
    for t in [
        "这个结节要是不管，以后就晚了。",
        "您要有心理准备。",
        "再拖下去后果不堪设想。",
        "这个就是癌。",
    ]:
        assert "scare_language" in check_readability(t).codes(), t


def test_negated_next_step_is_detected():
    """缺陷 2：『放心吧，不用复查』含"复查"但语义相反，首版判为"有下一步"。"""
    r = check_readability("您这个结节看着是良性的，放心吧，不用复查。")
    assert "no_next_step" in r.codes()


def test_colloquial_followup_counts_as_next_step():
    """缺陷 3：『明年再查一次』『一年查一次就行』是真实安排，首版不认。"""
    for t in [
        "您这次是正常的。建议您按单位体检安排，明年再查一次。",
        "您这个结节是良性的，可以放心，一年查一次就行。",
    ]:
        assert "no_next_step" not in check_readability(t).codes(), t


def test_bare_physical_exam_word_is_not_next_step():
    """反例：『这次体检发现结节』里的体检是既往事件，不能算下一步。"""
    r = check_readability("您这次体检发现甲状腺有个小结节。放心，大部分是良性的。")
    assert "no_next_step" in r.codes()


def test_jargon_explanation_does_not_globally_whitelist():
    """缺陷 4（结构性）：全文出现一次"意思是"不该让**所有**术语都算解释过。"""
    r = check_readability(
        "您这个 TI-RADS 4b、边界欠清、纵横比>1、微钙化，这里说的意思是：请复查。"
    )
    assert "jargon_density" in r.codes()


def test_greeting_before_conclusion_is_not_missing_main_message():
    """缺陷 5：先打招呼再给结论是中文医患沟通常态，不该判"无主信息"。"""
    r = check_readability("您好，这次检查发现您甲状腺有个小结节，多数是良性的。建议半年复查。")
    assert "no_main_message" not in r.codes()


def test_single_long_sentence_without_commas_still_detected():
    """长句兜底：60 字零逗号仍判长句（这条保证修复没有把原判据打瘫）。"""
    r = check_readability("您" + "结节" * 30 + "。")
    assert "long_sentence" in r.codes()


def test_golden_set_accuracy_does_not_regress():
    """回归门：B3 黄金集（50 条人工标注）整体准确率不得低于 0.85。

    baseline 0.640 → 修补后 0.880。这条钉住"不能改回去"。
    黄金集在 data/eval/v2/（人工逐条撰写，非脚本生成）。
    """
    import json
    import pathlib

    p = pathlib.Path(__file__).resolve().parents[3] / "data" / "eval" / "v2" / "patient_explanation_golden_v1.jsonl"
    if not p.exists():
        pytest.skip("黄金集不在本机（CI 环境）")
    rows = [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    correct = sum(
        1 for r in rows
        if (not check_readability(r["text"]).passed) == (r["label"] == "fail")
    )
    acc = correct / len(rows)
    assert acc >= 0.85, f"黄金集准确率退步：{acc:.3f} < 0.85"
