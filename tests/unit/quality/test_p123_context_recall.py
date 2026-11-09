"""p123 context_recall/context_precision 的口径守护。

## 为什么需要这份测试

`hit@k` 只回答「找到答案所在的块没有」；`context_recall` 回答「找到的内容够不够答完」。
**两者会背离**，实测：

| 指标 | 值 |
|---|---|
| hit@10 | 0.7872 |
| **context_recall** | **0.3763** |
| context_precision | 0.2506 |

找到位置 78.7%，但内容只覆盖 37.6% —— **只测 hit@k 会以为检索还行。**

## 首轮实现的一个真实 bug（本测试守护它不再出现）

第一版把「中文实词二元组」当关键单元。知识库原文是**中英混排**（LI-RADS 原文就是英文），
从中文连接词抽出的「即使/是小」在英文原文里当然找不到 → **每句判未覆盖**：

```
context_recall    = 0.1194
context_precision = 0.9324   ← 两数互相矛盾
```

修法：去掉中文二元组，改用「数值+单位 / 分级码 / **英文术语**」作为跨语言锚点。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.track_quality

_MOD = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p123_context_recall.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("p123", _MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestKeyUnits:
    def test_extracts_numbers(self, mod):
        units = mod._key_units("FNA 阈值 >15 mm，恶性率 2%~10%")
        assert any("15" in u for u in units), units

    def test_extracts_english_terms(self, mod):
        """英文术语是跨语言锚点 —— 首轮漏了它导致严重低估。"""
        units = mod._key_units("CEUS LR-M criteria include rim APHE and early washout")
        lowered = [u.lower() for u in units]
        assert any("aphe" in u for u in lowered), lowered
        assert any("washout" in u for u in lowered), lowered

    def test_no_chinese_bigrams(self, mod):
        """中文二元组会制造大量噪声（首轮 bug），不该再出现。"""
        units = mod._key_units("即使是小结节")
        assert "即使" not in units, units
        assert "是小" not in units, units


class TestSentenceSplit:
    def test_keeps_terminator_semantics(self, mod):
        sents = mod._sentences("第一句话在这里。第二句话也在这里。")
        assert len(sents) == 2

    def test_short_fragment_dropped(self, mod):
        """<6 字的碎片不算一句（噪声）。"""
        sents = mod._sentences("好。这是一句完整的话。")
        assert all(len(s) >= 6 for s in sents), sents


class TestRecall:
    def test_full_coverage(self, mod):
        """答案每个要素都在上下文里 → recall 应为 1.0。"""
        ref = "4A 类结节的 FNA 阈值是 >15 mm。"
        ctx = ["C-TIRADS 2021：4A 类结节的 FNA 阈值是 >15 mm。"]
        assert mod.compute_context_recall(ref, ctx) == 1.0

    def test_no_coverage(self, mod):
        """上下文完全无关 → recall 应为 0。"""
        ref = "CEUS LR-M 需要 rim APHE 与早期 washout。"
        ctx = ["肝脏超声检查的体位与准备要求。"]
        assert mod.compute_context_recall(ref, ctx) == 0.0

    def test_empty_context(self, mod):
        assert mod.compute_context_recall("有答案的句子在这里。", []) == 0.0

    def test_empty_reference(self, mod):
        assert mod.compute_context_recall("", ["有内容"]) == 0.0


class TestPrecision:
    def test_all_useful(self, mod):
        ref = "4A 类结节 FNA 阈值 >15 mm。"
        ctx = ["4A 类结节 FNA 阈值 >15 mm。"]
        assert mod.compute_context_precision(ctx, ref) == 1.0

    def test_none_useful(self, mod):
        ref = "4A 类结节 FNA 阈值 >15 mm。"
        ctx = ["肝脏检查的体位要求。", "心脏超声的探头选择。"]
        assert mod.compute_context_precision(ctx, ref) == 0.0

    def test_half_useful(self, mod):
        ref = "4A 类结节 FNA 阈值 >15 mm。"
        ctx = ["4A 类结节 FNA 阈值 >15 mm。", "肝脏检查的体位要求。"]
        assert mod.compute_context_precision(ctx, ref) == 0.5

    def test_empty_contexts(self, mod):
        assert mod.compute_context_precision([], "任意答案。") == 0.0
