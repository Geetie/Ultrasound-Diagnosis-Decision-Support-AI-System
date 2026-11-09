"""检索指标口径守卫 —— 钉住"算法本身是对的"。

## 为什么这些用例必须存在

本项目的评估实现**已经错过四次**：
1. NDCG > 1（DCG 按命中次数累加、IDCG 按 `min(|gold|,k)` 算）
2. CACTUS 97.43% 测试集与训练集共享视频（泄漏）
3. 探测脚本把评分算式 `(3)/(0)` 当引用（字符串错）
4. p94 修完后 **p94b 漏修**（没全仓搜同类实现）

第 4 条说明：只修当前这一处没用，必须**钉住口径本身**。
所以这里的用例分三类：

* **手算对照**（`test_*_hand_computed`）：输入极小、答案能手算，证明"算对了"。
* **性质检验**（`test_*_property`）：随机输入下必须始终成立的不变量
  （NDCG ∈ [0,1]、重复副本不改变首次命中位置的得分、IDCG≥DCG）。
* **跨实现一致**（`test_matches_production_rag_quality` /
  `test_matches_p94b_audit_script`）：仓库里每个算 NDCG 的地方，
  在同一输入下必须给出**同一个数**。这是防止"修一处漏一处"的机制。
"""
from __future__ import annotations

import ast
import math
import pathlib
import random

import pytest

from app.evaluation.retrieval_metrics import (
    METRIC_NAMES,
    RetrievalScore,
    aggregate,
    dedupe_by_first_occurrence,
    hit_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    score_query,
)

_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _load_function(file_rel: str, func_name: str) -> object:
    """按 AST 抽一个纯函数出来执行（避开模块级 structlog / asyncpg 依赖）。

    这样守的是**磁盘上的真实源码**，不是复制粘贴的副本 —— 只有真源码才算数。
    """
    src = (_ROOT / file_rel).read_text(encoding="utf-8")
    tree = ast.parse(src)
    ns: dict = {"math": math}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == func_name:
            exec(compile(ast.Module(body=[node], type_ignores=[]), file_rel, "exec"), ns)
            return ns[func_name]
    raise AssertionError(f"{file_rel} 里找不到函数 {func_name}")


# --------------------------------------------------------------------- 手算对照
class TestHandComputed:
    """输入小到能心算，答案写在用例里。"""

    def test_single_gold_at_rank1(self):
        assert ndcg_at_k(["a", "b", "c"], {"a"}, 10) == (1.0, False)

    def test_single_gold_at_rank2(self):
        # DCG = 1/log2(3) = 0.63093, IDCG = 1/log2(2) = 1.0
        value, violation = ndcg_at_k(["x", "a"], {"a"}, 10)
        assert value == pytest.approx(1.0 / math.log2(3))
        assert violation is False

    def test_single_gold_at_rank3(self):
        # DCG = 1/log2(4) = 0.5
        value, _ = ndcg_at_k(["x", "y", "a"], {"a"}, 10)
        assert value == pytest.approx(0.5)

    def test_two_golds_at_rank1_and_2(self):
        # DCG = 1/log2(2) + 1/log2(3); IDCG = 1/log2(2) + 1/log2(3) -> 1.0
        value, _ = ndcg_at_k(["a", "b"], {"a", "b"}, 10)
        assert value == pytest.approx(1.0)

    def test_two_golds_only_one_found(self):
        # DCG = 1/log2(2); IDCG = 1/log2(2) + 1/log2(3)
        value, _ = ndcg_at_k(["a", "x"], {"a", "b"}, 10)
        assert value == pytest.approx(1.0 / (1.0 + 1.0 / math.log2(3)))

    def test_no_gold_found(self):
        assert ndcg_at_k(["x", "y"], {"a"}, 10) == (0.0, False)

    def test_hit_and_mrr(self):
        assert hit_at_k(["x", "a"], {"a"}, 1) is False
        assert hit_at_k(["x", "a"], {"a"}, 2) is True
        assert mrr_at_k(["x", "a"], {"a"}, 10) == pytest.approx(0.5)
        assert mrr_at_k(["x", "y"], {"a"}, 10) == 0.0

    def test_precision_recall_denominators_differ(self):
        # 取回 4 条，gold 3 个，命中 2 个
        ranked, gold = ["a", "b", "x", "y"], {"a", "b", "c"}
        assert precision_at_k(ranked, gold, 10) == pytest.approx(2 / 4)
        assert recall_at_k(ranked, gold, 10) == pytest.approx(2 / 3)


# --------------------------------------------------------------------- 重复副本
class TestDuplicateCopies:
    """多 kb_version 副本：DCG 只认首次出现位置。这是 NDCG>1 事故的直接防线。"""

    def test_repeated_gold_does_not_inflate(self):
        # 修复前的 bug：1.0。正确值 0.6309（首次出现在 rank2）
        value, violation = ndcg_at_k(["x", "a", "a", "a"], {"a"}, 10)
        assert value == pytest.approx(1.0 / math.log2(3))
        assert violation is False

    def test_gold_at_rank1_repeated_is_still_one(self):
        assert ndcg_at_k(["a", "a", "a"], {"a"}, 10) == (1.0, False)

    def test_duplicates_never_exceed_one(self):
        """2026-11-08 事故的最小复现：gold 1 个、ranking 里出现 3 次。"""
        value, violation = ndcg_at_k(["a", "a", "a", "b"], {"a"}, 10)
        assert value <= 1.0
        assert violation is False

    def test_dedupe_helper_preserves_first_order(self):
        assert dedupe_by_first_occurrence(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]

    def test_score_query_reports_duplicates_removed(self):
        s = score_query("q1", ["a", "a", "b", "a"], {"a"})
        assert s.duplicates_removed == 2
        assert s.ranking == ("a", "b")
        assert s.metrics["ndcg@10"] == 1.0


# --------------------------------------------------------------------- 性质检验
class TestProperties:
    """随机输入下必须恒成立的不变量。"""

    def test_ndcg_in_unit_interval(self):
        rng = random.Random(20261109)
        docs = [f"d{i}" for i in range(6)]
        for _ in range(2000):
            ranking = [rng.choice(docs) for _ in range(rng.randint(0, 12))]
            gold = set(rng.sample(docs, rng.randint(0, 4)))
            value, violation = ndcg_at_k(ranking, gold, 10)
            assert 0.0 <= value <= 1.0, (ranking, gold, value)
            assert violation is False, (ranking, gold, value)

    def test_ndcg_never_exceeds_ideal(self):
        """DCG ≤ IDCG 是 NDCG ≤ 1 的证明前提，单独钉住。"""
        rng = random.Random(7)
        docs = [f"d{i}" for i in range(5)]
        for _ in range(1000):
            ranking = [rng.choice(docs) for _ in range(10)]
            gold = set(rng.sample(docs, rng.randint(1, 3)))
            seen: set[str] = set()
            dcg = 0.0
            for rank, cid in enumerate(ranking, start=1):
                if cid in gold and cid not in seen:
                    seen.add(cid)
                    dcg += 1.0 / math.log2(rank + 1)
            idcg = sum(1.0 / math.log2(r + 1) for r in range(1, min(len(gold), 10) + 1))
            assert dcg <= idcg + 1e-12

    def test_repeat_never_raises_score_above_no_repeat(self):
        """往排列尾部追加**已在 Top-k 内出现过**的 gold 副本，不得抬高分数。

        注意用例构造：必须保证 gold 在原排列里已经出现过。若原排列里根本没有
        gold（分数 0），追加一个副本会把 gold 带进来 —— 那不是"重复抬高"，
        那是"新召回了一个"，分数理应上升。这个区别正是本用例要显式区分的。
        """
        rng = random.Random(13)
        docs = ["a", "b", "c", "d"]
        checked = 0
        for _ in range(500):
            base = [rng.choice(docs) for _ in range(3)]
            gold = {"a"}
            if "a" not in base:
                continue           # 原排列没命中，追加副本应改变结果，跳过
            checked += 1
            padded = base + ["a"] * rng.randint(1, 3)
            v_base, _ = ndcg_at_k(base, gold, 10)
            v_pad, _ = ndcg_at_k(padded, gold, 10)
            assert v_pad == pytest.approx(v_base)
        assert checked > 50, "随机样本里应有一定比例含 gold，否则用例没真正覆盖"

    def test_appending_new_gold_legitimately_raises_recall(self):
        """反证腿：原排列没命中时，追加 gold 副本**应该**把分数抬起来。

        这条防的是"把去重实现成'忽略所有重复'"的过度修正 ——
        gold 第一次出现必须被计入，哪怕它是追加进来的。
        """
        assert ndcg_at_k(["x", "y"], {"a"}, 10)[0] == 0.0
        assert ndcg_at_k(["x", "y", "a"], {"a"}, 10)[0] > 0.0

    def test_monotone_in_rank(self):
        """gold 排得越靠后，NDCG 越低。"""
        values = [ndcg_at_k(["x"] * pos + ["a"], {"a"}, 10)[0] for pos in range(0, 5)]
        assert values == sorted(values, reverse=True)

    def test_aggregate_excludes_skipped_from_denominator(self):
        """n 必须报"实际参与计算的条数"，且区分「无定义」与「真实为 0」。"""
        scores = [
            score_query("q1", ["a"], {"a"}),
            score_query("q2", ["x"], {"a"}),
            score_query("q3", ["a"], set()),          # no_ground_truth -> 无定义，排除
            score_query("q4", [], {"a"}),             # no_results -> 真实 0，计入
        ]
        agg = aggregate(scores)
        assert agg["n_input"] == 4
        assert agg["n"] == 3                       # 只排除 q3
        assert agg["n_excluded"] == 1
        assert agg["skip_reasons"] == {"no_ground_truth": 1, "no_results": 1}
        # aggregate 的均值收敛到 4 位小数（报告口径），故容差取 1e-4
        assert agg["hit@1"] == pytest.approx(1 / 3, abs=1e-4)

    def test_metric_names_are_stable(self):
        """报告/门禁都按这些字符串取数，改了会静默对不上。"""
        s = score_query("q", ["a"], {"a"})
        assert set(METRIC_NAMES) == set(s.metrics)

    def test_negative_k_rejected(self):
        for fn in (ndcg_at_k, precision_at_k, recall_at_k, lambda r, g, k: mrr_at_k(r, g, k)):
            with pytest.raises(ValueError):
                fn(["a"], {"a"}, 0)


# --------------------------------------------------------------------- 跨实现一致
class TestCrossImplementationConsistency:
    """同一输入下，仓库里每个算 NDCG 的地方必须给同一个数。

    这一组是"修一处漏一处"的机制防线：新增一处 NDCG 实现时，
    把它的加载方式加进 `_IMPLEMENTATIONS` 即可自动纳入对拍。
    """

    #: (显示名, 文件, 函数名, 是否为 (value, violation) 二元组)
    _IMPLEMENTATIONS = (
        ("rag_quality(生产)", "app/observability/metrics_collector/rag_quality.py", "ndcg_at_k", False),
        ("p94b(审计A/B)", "scripts/audit/p94b_retrieval_ab.py", "ndcg_at_k", False),
        ("p94(审计B2)", "scripts/audit/p94_b2_retrieval_eval.py", "ndcg_at_k", False),
    )

    @staticmethod
    def _call(fn, ranking, gold, k, returns_tuple):
        out = fn(ranking, gold, k)
        return out[0] if returns_tuple else out

    @pytest.mark.parametrize("label,path,func,is_tuple", list(_IMPLEMENTATIONS))
    def test_matches_canonical(self, label, path, func, is_tuple):
        target = _load_function(path, func)
        cases = [
            (["a"], {"a"}),
            (["x", "a"], {"a"}),
            (["x", "y", "a"], {"a"}),
            (["x", "a", "a"], {"a"}),          # 重复副本：这是四次事故的现场
            (["a", "b"], {"a", "b"}),
            (["a", "x"], {"a", "b"}),
            (["x", "y"], {"a"}),
            (["a", "a", "a"], {"a"}),
        ]
        for ranking, gold in cases:
            got = self._call(target, ranking, gold, 10, is_tuple)
            want, _ = ndcg_at_k(ranking, gold, 10)
            assert got == pytest.approx(want, abs=1e-9), (
                f"{label} 与标准实现在 {ranking}/{gold} 上不一致：{got} != {want}"
            )

    @pytest.mark.parametrize("label,path,func,is_tuple", list(_IMPLEMENTATIONS))
    def test_never_exceeds_one_random(self, label, path, func, is_tuple):
        """随机输入下任何实现都不得超 1（2026-11-08 事故的通用防线）。"""
        target = _load_function(path, func)
        rng = random.Random(1013)
        docs = [f"d{i}" for i in range(5)]
        for _ in range(1500):
            ranking = [rng.choice(docs) for _ in range(10)]
            gold = set(rng.sample(docs, rng.randint(1, 3)))
            got = self._call(target, ranking, gold, 10, is_tuple)
            assert got <= 1.0 + 1e-9, f"{label} 在 {ranking}/{gold} 上给出 {got} > 1"


# --------------------------------------------------------------------- 数据结构
def test_retrieval_score_is_immutable_and_serialisable():
    s = score_query("q", ["a"], {"a"})
    assert isinstance(s, RetrievalScore)
    d = s.as_dict()
    assert d["query_id"] == "q" and d["violations"] == []
    # frozen dataclass 抛的是 FrozenInstanceError（dataclasses 的子类）；
    # 用具体类型而不是裸 Exception —— 裸 Exception 连拼写错误都会"通过"。
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        s.query_id = "other"


def test_skip_reason_vocabulary():
    """skip_reason 是枚举值的字符串，聚合层按它分组。"""
    assert score_query("q", [], {"a"}).skip_reason == "no_results"
    assert score_query("q", ["a"], set()).skip_reason == "no_ground_truth"
    assert score_query("q", ["a"], {"a"}).skip_reason == ""
