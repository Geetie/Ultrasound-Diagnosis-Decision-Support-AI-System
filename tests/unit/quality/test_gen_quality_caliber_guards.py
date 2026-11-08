"""第 4 组守卫：生成质量三项指标的**口径纯函数**不许漂。

钉住四件事（每条都对应本轮真机实测踩到的坑）：

1. `p100.classify` —— 一条判读记录的分类口径（empty / degraded / echo / usable）
   必须与生产同一把尺子（`output_guard.detect_prompt_echo` + `p49.degraded_kind`）。
2. `p101.judge` —— 干净样本的 8 条准入判据：**不认 recommendations 兜底**、
   拦 PHI、拦过短、拦无实质内容。
3. `p108` 的硬事实抽取与分档规则判定：
   * `2 分 → TR4` 必须判为**违反** prompt 分档表（真机 12 例都是这类）；
   * `（注：0-2 分为 TR2）` 这类**自造规则**必须被抽出来（真机 3 例）；
   * 总分/置信度**不得**被当成硬事实。
4. `p107.tier_of_input` —— 分档只看**输入侧征象词**，不看模型输出
   （否则"高风险档一致率低"就是输出自证）。

另外钉住 `p109` 的断言分桶（derived / selfref / pure）与滑窗片段，
因为 4.3 的分层结论全靠它。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, _AUDIT / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- 4.1 分类口径


def test_p100_classify_uses_production_ruler():
    p100 = _load("_p100_under_test", "p100_gen_quality_survey.py")
    # 空结论
    assert p100.classify(None)[0] == "empty"
    assert p100.classify({"fusion_result": {"content": "   "}})[0] == "empty"
    # 降级串（生产 p49 的口径）
    assert p100.classify({"fusion_result": {"content": "llm_unavailable: InferenceError"}})[0] == "degraded"
    # 模板回显（生产 output_guard 的口径）
    echo = {"fusion_result": {"content": "证据: chunk_a1b2；分级: TR2"}}
    assert p100.classify(echo)[0] == "echo"
    # 正常结论
    ok = {"fusion_result": {"content": "甲状腺 · TR2 — 低回声(2) = 2 分 → TR2"}}
    assert p100.classify(ok) == ("usable", "ok")
    # ⚠️ A.77 教训：`PATIENT_001` 单独出现**不算**回显（语料脱敏标记外泄 ≠ 模板照抄）
    leak = {"fusion_result": {"content": "甲状腺 · TR2 — 患者PATIENT_001 建议复查"}}
    assert p100.classify(leak)[0] == "usable"


def test_p100_classify_rejects_degraded_flag():
    """`fusion_result.degraded=True` 即便有正文也不是结论。"""
    p100 = _load("_p100_under_test2", "p100_gen_quality_survey.py")
    payload = {"fusion_result": {"content": "判读失败，请人工复核" * 3, "degraded": True}}
    assert p100.classify(payload)[0] == "degraded"


# --------------------------------------------------------------- 4.1 准入判据


def test_p101_judge_rejects_non_official_field():
    """R1：`recommendations` 兜底**不是**正式结论，不许进样本。"""
    p101 = _load("_p101_under_test", "p101_build_clean_gen_set.py")
    findings = {"recommendations": "甲状腺 · TR2 — " + "低回声边界清" * 10}
    ok, why, _ = p101.judge(findings, "thyroid")
    assert not ok and why == "not_official_field"


def test_p101_judge_blocks_phi():
    """R7：手机号 / 身份证 / PATIENT_ 标记必须拒。"""
    p101 = _load("_p101_under_test2", "p101_build_clean_gen_set.py")
    body = "甲状腺 · TR2 — 理由: 低回声(2) + 边界清(0) = 2 分 → TR2；" + "随访" * 20
    for phi in ("联系电话 13812345678", "身份证 110101199003072316", "患者PATIENT_001"):
        findings = {"fusion_result": {"content": f"{body} {phi}"}}
        ok, why, _ = p101.judge(findings, "thyroid")
        assert not ok, phi
        assert why.startswith("phi:"), (phi, why)


def test_p101_judge_requires_substance():
    """R5：既解析不出分级、也没有处置建议 → 不算可用样本。

    正文要够长（否则先被 R2 `too_short` 拦下，测不到 R5）。
    """
    p101 = _load("_p101_under_test3", "p101_build_clean_gen_set.py")
    body = "本次检查已完成，影像所见与临床资料已归档，各项结果请结合临床进一步判断。" * 2
    findings = {"fusion_result": {"content": body}}
    ok, why, _ = p101.judge(findings, "thyroid")
    assert not ok and why == "no_substance", (ok, why, len(body))


def test_p101_judge_accepts_assertive_sample():
    """R1–R7 全过：正式字段 + 有分级 + 有理由 + 无 PHI + 够长。"""
    p101 = _load("_p101_under_test4", "p101_build_clean_gen_set.py")
    body = (
        "甲状腺 · TR2 — 分级: TR2 总分: 2 置信度: 0.85 "
        "理由: 低回声(2) + 边界清(0) + 形态规则(0) + 无钙化(0) = 2 分 → TR2。"
        "鉴别诊断: 结节性甲状腺肿 | 0.70 | 支持: 边界清晰 | 排除: 无钙化。"
    )
    findings = {"fusion_result": {"content": body}}
    ok, why, text = p101.judge(findings, "thyroid")
    assert ok and why == "has_grade" and text, (ok, why, len(body))


# --------------------------------------------------------------- 4.2 幻觉口径


def test_p108_extract_claims_finds_hard_facts():
    p108 = _load("_p108_under_test", "p108_hallucination_faithfulness.py")
    claims = p108.extract_claims("EF 60%，风险约 10-50%，≥1cm 建议 FNA")
    kinds = {c["kind"] for c in claims}
    assert "pct" in kinds and "threshold" in kinds


def test_p108_extract_claims_skips_derived_fields():
    """`总分`/`置信度` 是系统派生，不是硬事实。"""
    p108 = _load("_p108_under_test2", "p108_hallucination_faithfulness.py")
    claims = p108.extract_claims("总分: 2 置信度: 0.95")
    assert all(c["kind"] not in ("pct", "score_map") for c in claims)


def test_p108_fabricated_rule_is_detected():
    """真机 3 例：`（注：0-2 分为 TR2）` —— KB 与 prompt 都没有这条规则。"""
    p108 = _load("_p108_under_test3", "p108_hallucination_faithfulness.py")
    text = "甲状腺 · TR2 — 低回声(2) + 边界清(0) = 2 分 → TR2（注：0-2 分为 TR2）"
    claims = p108.extract_claims(text)
    assert any("0-2 分为 TR2" in c["raw"] for c in claims)


def test_p108_score_map_bin_rule():
    """分档表判定：落在区间内 = 不算错；落在区间外 = 算错。"""
    p108 = _load("_p108_under_test4", "p108_hallucination_faithfulness.py")
    tpl_dir = Path(__file__).resolve().parents[3] / "app" / "agents" / "prompts"
    bins = p108.parse_grade_bins((tpl_dir / "thyroid.md").read_text(encoding="utf-8"))
    assert bins, "prompt 里必须能解析出分档表"
    # prompt 口径：TR2=2 分 / TR3=3 分 / TR4=4-6 分 / TR5=≥7 分
    assert p108.score_map_violates({"raw": "4 分 → TR4"}, bins) is False
    assert p108.score_map_violates({"raw": "2 分 → TR2"}, bins) is False
    assert p108.score_map_violates({"raw": "7 分 → TR5"}, bins) is False
    # 真机 12 例：全部是"分数落在该级区间外"
    assert p108.score_map_violates({"raw": "2 分 → TR4"}, bins) is True
    assert p108.score_map_violates({"raw": "1 分 → TR2"}, bins) is True
    assert p108.score_map_violates({"raw": "8 分 → TR2"}, bins) is True
    # 无区间可解析 → 不判（None，不许猜）
    assert p108.score_map_violates({"raw": "4 分 → TR9"}, bins) is None


def test_p108_claim_supported_normalizes():
    p108 = _load("_p108_under_test5", "p108_hallucination_faithfulness.py")
    assert p108.claim_supported("55%", ["正常 EF ≥ 55% 属于正常"])
    assert not p108.claim_supported("83%", ["正常 EF ≥ 55% 属于正常"])


# --------------------------------------------------------------- 4.4 分档口径


def test_p107_tier_uses_input_not_output():
    """分档只看输入侧征象词：多写一句"TR5"不能把常规档变成高风险档。"""
    p107 = _load("_p107_under_test", "p107_grading_consistency.py")
    assert p107.tier_of_input("甲状腺右叶低回声结节，边界清。", "thyroid") == "regular"
    assert (
        p107.tier_of_input("左叶混合回声结节，边界欠清，形态不规则，内见点状强回声。", "thyroid")
        == "high_risk"
    )
    # 心脏：有显式 EF% → high_risk
    assert p107.tier_of_input("LVEF 45%，二尖瓣轻度反流", "cardiac") == "high_risk"


# --------------------------------------------------------------- 4.3 断言分层


def test_p109_sub_bucket_rules():
    p109 = _load("_p109_under_test", "p109_faith_caliber_b.py")
    assert p109.sub_bucket("总评分为 2 分") == "derived"
    assert p109.sub_bucket("总评分 4 分属于 TR4 等级") == "derived"
    assert p109.sub_bucket("鉴别诊断的第一项是乳头状癌") == "selfref"
    assert p109.sub_bucket("TR4 的评分范围是 4 到 6 分") in ("derived",)
    assert p109.sub_bucket("二尖瓣存在轻度反流") == "pure"


def test_p109_fragments_and_norm():
    p109 = _load("_p109_under_test2", "p109_faith_caliber_b.py")
    # 全角数字/百分号 → 半角；≥/≤ 统一成 >=/<=；空白与常见标点删掉
    assert p109.norm("６０％") == "60"
    assert p109.norm("≥ 55%") == ">=55"  # 百分号被 _STRIP 去掉（与检索侧同法）
    assert p109.norm(">= 55") == ">=55"
    assert p109.norm("EF：60%，边界　清晰。") == "EF60边界清晰"  # 大小写不归一（子串比对不敏感）
    # 短句（<6 字）不成片段；≥6 字整句成片段；长句切 6/8/10/12 字滑窗
    assert p109.fragments("短句") == set()
    assert "二尖瓣存在轻度反流" in p109.fragments("二尖瓣存在轻度反流")
    long = "左室射血分数大于等于55属于正常范围"
    frags = p109.fragments(p109.norm(long))
    assert frags and any(f in p109.norm(long) for f in frags)
