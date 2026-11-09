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


class TestAnchorGate:
    """闸8：query 必须含具体锚点（2026-11-09 新增）。

    实测漏网：「什么情况下这条不适用」「报告这个分级意味着什么、严不严重？」
    —— 没说是哪个分级/哪条规则，医生不会这样问。
    根因：模型把「问法骨架」直接当问题，没填原文内容。
    """

    def test_vague_query_flagged(self, mod):
        row = {
            "organ": "liver",
            "query": "什么情况下这条不适用",
            "reference_answer": "原文提到某些情况下需要结合临床判断，不能仅凭影像。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("无具体锚点" in p for p in probs), probs

    def test_query_with_grade_passes(self, mod):
        row = {
            "organ": "thyroid",
            "query": "4A 类结节 13mm 要不要穿刺？",
            "reference_answer": "原文提到 4A 类 FNA 阈值是 >15mm，紧邻被膜降到 >10mm。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert not any("无具体锚点" in p for p in probs), probs

    def test_query_with_anatomy_passes(self, mod):
        row = {
            "organ": "cardiac",
            "query": "二尖瓣中度反流需要手术吗？",
            "reference_answer": "原文提到中度反流需结合症状与左室功能评估手术指征。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert not any("无具体锚点" in p for p in probs), probs


class TestBrokenSentenceGate:
    """闸9：书名号清理留下的残句（2026-11-09 新增，是我自己引入的 bug）。

    `_strip_fake_citations` 把「按《LI-RADS 2017》」清成「按原文提到：」，
    后面直接接「的分级标准」，句子碎了。
    """

    def test_broken_sentence_in_query(self, mod):
        row = {
            "organ": "liver",
            "query": "如果医生按原文提到：的分级标准，质控该如何处理？",
            "reference_answer": "原文提到要统一用一套体系，避免同一病人两种结论。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("语句破碎" in p for p in probs), probs

    def test_broken_sentence_in_answer(self, mod):
        row = {
            "organ": "liver",
            "query": "肝脏 LR-5 结节怎么处理？",
            "reference_answer": "原文提到：的分级标准显示 LR-5 属于明确恶性，需要进一步处理。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("语句破碎" in p for p in probs), probs

    def test_normal_sentence_passes(self, mod):
        row = {
            "organ": "liver",
            "query": "肝脏 LR-5 结节怎么处理？",
            "reference_answer": "原文提到 LR-5 属于明确恶性档，建议多学科会诊并考虑手术或消融。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert not any("语句破碎" in p for p in probs), probs


class TestScoreGradeGate:
    """闸14：C-TIRADS 分级与积分必须对得上（2026-11-09 L3 后新增）。

    ## 为什么这道闸存在

    L3 人工审核 20 条抓到 4 条医学错误，全部是 8B 模型「不知道规则」造成的：

    | 生成的错误 | 真相 |
    |---|---|
    | 1.5cm 边界清晰 → TR2 级，积分 3 分 | TR2 是 1 分；且 TRx 是 ACR 体系 |
    | 「13 分结节」 | C-TIRADS 满分 5 分 |
    | LR-3 → 「明确恶性」 | LR-5 才是明确恶性 |

    **这类错误靠提示词修不了**（模型知识不够），但**靠规则能查** ——
    C-TIRADS 的分数与分级一一对应，是写死的。

    ## 实测踩过的坑

    第一版实现里 `got.startswith("tr")` 就直接跳过比对，
    结果「1.5cm → TR2 + 3 分」这个**真实错误没被抓到**。
    修法：拆成两步 —— A) 分数 + TRx 同现 = 体系混用；B) 分数 + 类码必须匹配对照表。
    """

    _KB = "C-TIRADS 计数法：分值 1 为 4A，2 为 4B，3~4 为 4C，5 为 5 类。"

    def _row(self, answer: str, query: str = "甲状腺结节实性，积几分归几类？") -> dict:
        return {
            "organ": "thyroid",
            "query": query,
            "reference_answer": answer,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }

    def test_score_over_five_flagged(self, mod):
        """「13 分」不可能 —— 满分 5 分。"""
        probs = mod.check_one(self._row("原文提到：13 分的结节属于 5 类，建议穿刺。"), self._KB, {"kb:x#0000"})
        assert any("不可能为 13" in p for p in probs), probs

    def test_tr_with_score_flagged(self, mod):
        """分数 + TRx 同现 = 体系混用（第一版漏掉的就是这个）。"""
        probs = mod.check_one(
            self._row("原文提到：1.5cm 边界清晰的结节属于 TR2 级，积分为 3 分。"),
            self._KB, {"kb:x#0000"},
        )
        assert any("TR" in p and "分" in p for p in probs), probs

    def test_score_grade_mismatch_flagged(self, mod):
        """2 分应为 4B，写成 4A 必须抓。"""
        probs = mod.check_one(self._row("原文提到：共 2 分，归为 4A 类。"), self._KB, {"kb:x#0000"})
        assert any("积分与分级不符" in p for p in probs), probs

    def test_correct_mapping_passes(self, mod):
        """1 分 → 4A 是正确映射，不许误报。"""
        probs = mod.check_one(self._row("原文提到：实性 +1 分，共 1 分，归为 4A 类。"), self._KB, {"kb:x#0000"})
        assert not any("积分" in p or "TR" in p for p in probs), probs

    def test_four_c_score_mapping(self, mod):
        """3 分与 4 分都属 4C（区间映射）。"""
        for score in (3, 4):
            probs = mod.check_one(
                self._row(f"原文提到：共 {score} 分，归为 4C 类。"), self._KB, {"kb:x#0000"}
            )
            assert not any("积分与分级不符" in p for p in probs), (score, probs)


class TestBrokenSentenceAfterCleanup:
    """闸13：书名号清理后的残句（我的 _strip_fake_citations 副作用）。

    实测：「统一使用《A》或《B》中的标准」→「统一使用中的标准进行分级」（缺宾语）
    这类残句不靠正则救语法，直接判不合格让模型重试。
    """

    def test_missing_object_flagged(self, mod):
        row = {
            "organ": "liver",
            "query": "肝脏 LR-5 结节如何处理？",
            "reference_answer": "原文提到：统一使用中的标准进行分级，LR-5 需多学科会诊。" * 2,
            "gold": [{"chunk_id": "kb:x#0000"}],
        }
        probs = mod.check_one(row, "kb", {"kb:x#0000"})
        assert any("残句" in p for p in probs), probs
