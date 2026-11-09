"""p128 B2→Ragas 口径升级的逻辑守护。

## 为什么这条路对（15 轮合成失败后的结论）

合成的根本障碍是 **II-Medical-8B 不知道医学事实**：
「APHE 分 6 级」（实际是二值）、「4 类以上恶性可能逐渐减小」（方向反了）。

**但 B2 里的 `evidence_quote` 已经解决了这个问题** ——
它是人工从 KB 逐字摘录的，且已过 p117 三判据（487 条 0 问题）。

所以 `reference_answer` 不生成，只拼接。这份测试守护三件事：
1. 拼接不改变原文内容（只清排版）
2. metadata 的推导是客观的（能算的算，算不出的标 unassigned）
3. **绝不伪造 persona**
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.track_quality

_MOD = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p128_b2_upgrade.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("p128", _MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestQuoteCleaning:
    def test_strips_sup_tags(self, mod):
        assert mod._clean_quote("4A<sup>低度可疑恶性</sup>") == "4A低度可疑恶性"

    def test_strips_generic_tags(self, mod):
        assert mod._clean_quote("<u>中华超声影像学杂志</u>") == "中华超声影像学杂志"

    def test_keeps_content(self, mod):
        """只清排版，不删内容 —— 每一个字都要保留。"""
        src = "实性、 微钙化、 极低回声"
        assert mod._clean_quote(src).replace(" ", "") == src.replace(" ", "")


class TestReferenceAnswer:
    def test_concatenates_quotes(self, mod):
        gold = [
            {"evidence_quote": "实性、微钙化是可疑恶性超声特征，需要计数"},
            {"evidence_quote": "分值 1 对应 4A 类，恶性率 2%~10%"},
        ]
        ans, probs = mod.build_reference_answer(gold)
        assert "实性、微钙化" in ans
        assert "分值 1 对应 4A 类" in ans
        assert ans.count("原文提到：") == 2
        assert not probs, probs

    def test_empty_gold(self, mod):
        ans, probs = mod.build_reference_answer([])
        assert ans == ""
        assert any("没有可用" in p for p in probs), probs

    def test_too_short_quote_dropped(self, mod):
        """<8 字的引文直接丢弃（太短没有辨识度）。

        注意：这是 p128 的**输入侧**过滤，与 p117 的 ≥15 字是两回事 ——
        p128 只需要能拼出可读答案；p117 才管 gold 标注质量。
        """
        ans, probs = mod.build_reference_answer([{"evidence_quote": "短句七个字"}])
        assert ans == ""
        assert any("没有可用" in p for p in probs), probs

    def test_short_concatenation_flagged(self, mod):
        """拼接后仍 <40 字 → 需要从 chunk 补上下文（不是凑字数）。"""
        ans, probs = mod.build_reference_answer(
            [{"evidence_quote": "这条引文只有十几个字左右"}]
        )
        assert ans  # 有内容
        assert any("过短" in p for p in probs), probs


class TestMetadataInference:
    def test_persona_never_fabricated(self, mod):
        """**最关键的一条**：原数据无 persona，必须标 unassigned，不许编。"""
        meta = mod.infer_metadata({"query": "4A 类结节 13mm 要不要穿刺？", "gold": [{}]})
        assert meta["persona"] == "unassigned"
        assert "不伪造" in meta["persona_note"]

    def test_length_by_char_count(self, mod):
        short = mod.infer_metadata({"query": "短问题只有十来个字呢", "gold": [{}]})
        long_q = mod.infer_metadata({"query": "这是一个非常长的问题，" * 5, "gold": [{}]})
        assert short["length"] == "short"
        assert long_q["length"] == "long"

    def test_hop_by_gold_count(self, mod):
        single = mod.infer_metadata({"query": "任意问题呢", "gold": [{}]})
        multi = mod.infer_metadata({"query": "任意问题呢", "gold": [{}, {}]})
        assert single["hop"] == "single"
        assert multi["hop"] == "multi"
        # 必须注明这是代理指标
        assert "代理" in single["hop_note"]

    def test_specificity_by_anchor(self, mod):
        spec = mod.infer_metadata({"query": "4A 类结节 13mm 要不要穿刺？", "gold": [{}]})
        abst = mod.infer_metadata({"query": "这条规则的临床意义是什么？", "gold": [{}]})
        assert spec["specificity"] == "specific"
        assert abst["specificity"] == "abstract"

    def test_source_and_version(self, mod):
        meta = mod.infer_metadata({"query": "任意", "gold": [{}]})
        assert meta["source"] == "manual_b2"
        assert meta["dataset_version"] == mod.DATASET_VERSION
