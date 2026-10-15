"""A.66 WP1-② 守卫：自造难集的**规则化校验**与**性质标注**。

钉住三件事：
1. 长度/重叠/gold 唯一这三条规则必须真的能拒稿（否则造出来的"难集"会混入抄原句的题）；
2. 自造集必须**自带性质标注**（`synthetic: true` + `usage: expansion_ablation_only`）——
   它只许用于"扩边是否有用"的对照，不许流进正式指标；
3. 长度口径与 golden 构造侧**同源**（复用 `t1b_build_qa_golden` 的常量与问句抽取），
   不另立一套。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p41_under_test", _AUDIT / "p41_build_hard_query_set.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_lcs_ratio_bounds():
    p41 = _load()
    assert p41.longest_common_substring_ratio("甲状腺结节", "甲状腺结节") == 1.0
    assert p41.longest_common_substring_ratio("甲状腺结节", "完全无关的文本") < 0.5
    assert p41.longest_common_substring_ratio("", "abc") == 0.0
    assert p41.longest_common_substring_ratio("abc", "") == 0.0
    # 部分重叠：query 里 3/6 个字符连在一起出现在 text 中
    ratio = p41.longest_common_substring_ratio("低回声结节是什么", "低回声结节的判断标准")
    assert 0.0 < ratio <= 1.0


def test_check_query_rejects_each_rule():
    p41 = _load()
    ok, reason, _ = p41.check_query(
        "甲状腺结节需要穿刺吗", "dialogue_内科_1", ["指南：结节直径大于 1cm 建议穿刺。"]
    )
    assert ok is True and reason == ""

    ok2, reason2, _ = p41.check_query("太短", "d1", ["任意文本"])
    assert ok2 is False and reason2 == "length_out_of_range"

    ok3, reason3, _ = p41.check_query("这是一个足够长的临床问题", "", ["任意文本"])
    assert ok3 is False and reason3 == "missing_gold"

    ok4, reason4, _ = p41.check_query(
        "甲状腺结节需要穿刺吗", "dialogue_内科_1", ["x"],
        gold_doc_must_differ_from="dialogue_内科_1",
    )
    assert ok4 is False and reason4 == "gold_equals_source_doc"

    # 直接抄原句 → 重叠过高被拒
    copied = "结节直径大于1cm建议穿刺"
    ok5, reason5, overlap5 = p41.check_query(copied, "d1", ["指南：结节直径大于1cm建议穿刺。"])
    assert ok5 is False and reason5 == "overlap_too_high" and overlap5 > 0.6


def test_length_rule_comes_from_golden_builder():
    p41 = _load()
    from t1b_build_qa_golden import _MIN_QUERY_CHARS, _QUERY_MAX_CHARS

    assert p41._MIN_QUERY_CHARS == _MIN_QUERY_CHARS
    assert p41._QUERY_MAX_CHARS == _QUERY_MAX_CHARS
    src = (_AUDIT / "p41_build_hard_query_set.py").read_text(encoding="utf-8")
    assert "from t1b_build_qa_golden import" in src
    # 问句抽取不另写一份（KB 脱敏兜底那个是另一个函数，不叫 extract_question）
    assert "def extract_question(" not in src


def test_redacted_kb_content_fallback():
    """KB 内容已脱敏（`患者提问：` → `[REDACTED]：`）→ 兜底抽取必须能拿到问句。"""
    p41 = _load()
    text = "[REDACTED]：先天性心脏病怎么救治是好。 女36最近比较疲惫， 医生回答：室间隔小瘢痕"
    assert p41.extract_question_from_kb_content(text) == "先天性心脏病怎么救治是好"
    assert p41.extract_question_from_kb_content("正文没有脱敏前缀") is None
    assert p41.extract_question_from_kb_content("") is None


def test_dialogue_source_loader(tmp_path):
    """未脱敏来源（jsonl）→ source_doc_id → 问句 映射。"""
    import json

    p41 = _load()
    src = tmp_path / "dialogue_docs.jsonl"
    rows = [
        {"source_doc_id": "dialogue_内科_1",
         "content": "患者提问：甲状腺结节需要穿刺吗？\n医生回答：看大小与超声特征。"},
        {"source_doc_id": "dialogue_内科_2", "content": "没有标准问句格式"},
        {"source_doc_id": "dialogue_内科_3",
         "content": "患者提问：乳腺结节随访间隔多久？\n医生回答：一般 6 个月。"},
    ]
    src.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
    out = p41.load_dialogue_questions(src)
    assert out["dialogue_内科_1"] == "甲状腺结节需要穿刺吗？"
    assert out["dialogue_内科_3"] == "乳腺结节随访间隔多久？"
    assert "dialogue_内科_2" not in out
    assert p41.load_dialogue_questions(tmp_path / "missing.jsonl") == {}


def test_synthetic_marking_is_mandatory_in_output():
    src = (_AUDIT / "p41_build_hard_query_set.py").read_text(encoding="utf-8")
    assert '"synthetic": True' in src
    assert "expansion_ablation_only" in src
    assert "不并入正式指标" in src
