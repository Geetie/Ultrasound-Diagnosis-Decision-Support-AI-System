"""A.69 / R17 守卫：忠实度「A 口径」的分桶规则与空桶语义。

两条纪律：
* **分桶规则必须可解释**（不引第二个模型去判；命中影像词优先于建议词）；
* **空桶不得写 1.0**（"没东西可查"不等于"全部有据"）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p44_under_test", _AUDIT / "p44_faithfulness_caliber_a.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_image_findings_are_bucketed_as_image():
    p44 = _load()
    assert p44.classify_statement("右乳低回声结节，边界不清，形态不规则") == "image"
    assert p44.classify_statement("病灶大小约 12×8 mm，内部回声不均") == "image"


def test_advice_is_bucketed_as_advice():
    p44 = _load()
    assert p44.classify_statement("建议 3 个月后复查超声") == "advice"
    assert p44.classify_statement("推荐行细针穿刺活检") == "advice"


def test_guideline_knowledge_stays_knowledge():
    p44 = _load()
    assert p44.classify_statement("TI-RADS 4 类恶性风险约为 5% 至 80%") == "knowledge"
    assert p44.classify_statement("甲状腺癌最常见的病理类型是乳头状癌") == "knowledge"


def test_image_wins_when_statement_has_both_image_and_advice():
    """「结节伴钙化，建议穿刺」是**描述本次影像 + 建议**，不能算知识断言。"""
    p44 = _load()
    assert p44.classify_statement("结节伴微钙化，建议穿刺") == "image"


def test_aggregate_counts_and_rates():
    p44 = _load()
    out = p44.aggregate([("knowledge", 1), ("knowledge", 0), ("image", 0), ("advice", 1)])
    assert out["knowledge"] == {"n": 2, "supported": 1, "faith": 0.5}
    assert out["image"]["faith"] == 0.0
    assert out["advice"]["faith"] == 1.0
    assert out["all"]["n"] == 4
    assert out["all"]["faith"] == 0.5


def test_empty_bucket_is_none_not_one():
    """空桶必须是 None：把「没有断言」写成 1.0 会把分数虚高（官方总分口径才写 1.0）。"""
    p44 = _load()
    out = p44.aggregate([])
    assert out["knowledge"]["faith"] is None
    assert out["all"]["faith"] is None


def test_context_list_is_joined_like_judge_script():
    """评测集 context 是列表 → 必须 join 成字符串（首版没 join，判官看到列表字面量 → 全 0）。"""
    p44 = _load()
    assert p44._context_text({"context": ["段一", "段二"]}) == "段一\n\n段二"
    assert p44._context_text({"context": "已是字符串"}) == "已是字符串"
    assert p44._context_text({"context": ["", "  "]}) == ""
    assert p44._context_text({}) == ""


def test_english_image_statements_are_bucketed_as_image():
    """实测：判官会把中文结论拆成英文断言 → 分桶必须认英文影像词。"""
    p44 = _load()
    assert p44.classify_statement("The right lobe has a low echo nodule.") == "image"
    assert p44.classify_statement("The boundary of the nodule is clear.") == "image"
    assert p44.classify_statement("No obvious calcification is observed.") == "image"


def test_statement_prompt_enforces_chinese_without_rewriting_official_prompt():
    """语言约束是**包装**，不是改造官方 prompt：官方 instruction 必须原样保留。"""
    p44 = _load()
    zh = p44._statement_prompt("甲状腺结节如何处理", "建议复查", "zh")
    auto = p44._statement_prompt("甲状腺结节如何处理", "建议复查", "auto")
    assert "中文" in zh and "中文" not in auto
    assert zh.endswith("/no_think") and auto.endswith("/no_think")
    # 去掉语言提示后应与官方渲染逐字一致
    stripped = zh.replace("用与答案相同的语言（中文）写出每条断言，不要翻译成英文。\n", "")
    assert stripped == auto


def test_report_carries_both_calibers_and_rules():
    src = (_AUDIT / "p44_faithfulness_caliber_a.py").read_text(encoding="utf-8")
    assert "faithfulness_original_caliber" in src
    assert "faithfulness_caliber_a" in src
    assert "classification_rules" in src
    # 复用官方断言/NLI，不自造第二把尺子
    assert "_render_statement_prompt" in src and "_render_nli_prompt" in src
    assert "def _render_statement_prompt" not in src
