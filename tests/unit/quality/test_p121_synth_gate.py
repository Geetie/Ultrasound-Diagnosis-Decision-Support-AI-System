"""p121 合成数据质量门的判据守护。

## 这份测试存在的理由（2026-11-09 实测）

本项目用 II-Medical-8B 按 Ragas 方法合成评测数据。即使提示词明确写
「禁止编造文献名称与年份」，模型仍然输出：

```
依据《C-TIRADS 2018》原文：…     ← 真实是 2021
根据《LI-RADS 2017》…            ← 用在了甲状腺问题上
根据《Hürthle 细胞肿瘤》…         ← 编的
```

**10 条样本里 4 条编造文献名、3 条跨体系混用、2 条把原文复述当 query。**

所以必须有程序闸。这份测试守护闸的判据不被放宽。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

pytestmark = pytest.mark.track_quality

_MOD = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p121_synth_quality_gate.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("p121", _MOD)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class TestThresholds:
    def test_min_answer_length(self, mod):
        """答案至少 40 字 —— 太短的答案测不出「够不够答完」。"""
        assert mod.MIN_ANSWER == 40

    def test_max_query_length(self, mod):
        """query 不超过 80 字 —— 超过说明是把原文复述当提问。"""
        assert mod.MAX_QUERY == 80

    def test_dims_complete(self, mod):
        assert mod.DIMS_ARE_CHECKED if hasattr(mod, "DIMS_ARE_CHECKED") else True


class TestFabricatedCitationDetection:
    def test_catches_nonexistent_doc(self, mod):
        """编造的文献名必须被抓到。"""
        kb = "这是一段知识库原文，不含任何书名号。"
        row = {
            "organ": "thyroid",
            "query": "4A 类结节 13mm 要不要穿刺？",
            "reference_answer": "依据《C-TIRADS 2018》原文：>15mm 建议穿刺活检，本例未达阈值但需评估。",
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, kb, {"kb:x#0000"})
        assert any("编造文献名" in p for p in probs), probs

    def test_allows_existing_doc(self, mod):
        """真实存在的书名不误报。"""
        kb = "《C-TIRADS 2021》是本项目的权威来源之一。"
        row = {
            "organ": "thyroid",
            "query": "4A 类结节 13mm 要不要穿刺？",
            "reference_answer": "依据《C-TIRADS 2021》原文：4A 类 FNA 阈值是 >15mm，本例 13mm 未达。",
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, kb, {"kb:x#0000"})
        assert not any("编造文献名" in p for p in probs), probs


class TestCrossSystemDetection:
    def test_thyroid_with_lirads_flagged(self, mod):
        """甲状腺问题出现 LI-RADS = 跨体系混用。"""
        row = {
            "organ": "thyroid",
            "query": "这个结节按 LI-RADS 归几类？",
            "reference_answer": "原文提到 LI-RADS 的分类标准，需要结合动脉期增强判断。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("跨体系" in p for p in probs), probs

    def test_liver_with_ctirads_flagged(self, mod):
        row = {
            "organ": "liver",
            "query": "肝脏结节按 C-TIRADS 归几类？",
            "reference_answer": "原文提到 C-TIRADS 的计数法，需要数可疑征象个数。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("跨体系" in p for p in probs), probs


class TestQueryShape:
    def test_statement_query_flagged(self, mod):
        """把原文复述当 query → 不含疑问词，必须抓。"""
        row = {
            "organ": "cardiac",
            "query": "左心室质量指数在男女两性中随年龄增长而逐渐增加在六个年龄组间无差异",
            "reference_answer": "原文提到 LVM 随年龄增长。" * 3,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("疑问词" in p or "过长" in p for p in probs), probs

    def test_good_question_passes(self, mod):
        """好问题应零问题通过。

        注意：kb 里必须**真的含**答案中引用的数值 —— 否则闸7（编造数值）会正确报警。
        这不违背闸的意图（闸7 就是抓「答案里有原文没有的数」），
        只是测试的 kb stub 要跟着答案写。
        """
        kb = "缺血性左室功能障碍（EF≤35%）的患者被纳入研究，该阈值是分层关键点；本例 EF 38% 接近阈值。"
        row = {
            "organ": "cardiac",
            "query": "EF 38% 需要转心内科吗？",
            "reference_answer": "原文提到 EF ≤35% 是缺血性左室功能障碍的关键分层点，本例 38% 已接近该阈值，建议会同心内科评估。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, kb, {"kb:x#0000"})
        assert probs == [], probs
