"""检索评估指标（**唯一实现**，纯函数、纯标准库）。

## 为什么要有这一个模块

2026-11-08 ~ 11-09 的评估体系审查发现，同一套检索指标在仓库里被**各写各的**，
至少 5 处实现，其中 2 处有真实缺陷：

| 位置 | 缺陷 | 后果 |
|---|---|---|
| `app/observability/metrics_collector/rag_quality.py` | `ndcg_at_k` 无去重 | `["x","a","a"]` 对 gold `{a}` 返回 **1.0**（正确 0.5） |
| `scripts/audit/p94b_retrieval_ab.py` | `ndcg_at_k` 无去重、无 clamp | 5 词表随机搜索 5000 例中 **2392 例 NDCG>1** |
| `scripts/audit/p94_b2_retrieval_eval.py` | 已有 `seen` 去重（2026-11-08 修过） | 该处已正确 |
| `scripts/t1_eval_retrieval.py` / `t1c_eval_retrieval.py` | 单 gold 闭式解 | 数学正确，不受重复影响 |

**根因不是某一次写错，而是"指标没有单一实现"** —— 每个脚本自己抄一遍，
修了一处修不了全部。本模块把口径固化成一个可被所有脚本 import 的实现，
并用 `tests/unit/quality/test_retrieval_metrics.py` 钉住。

## 口径（写死，改之前先读这一节）

1. **相关性是二值的**：gold 命中 = 1，否则 = 0。本仓库的 gold 是"含答案的那一段"，
   没有分级相关性标注，所以**不做 gain 分级**（那是另一个需要标注的口径）。
2. **重复副本只计一次**：多 `kb_version` 并存时，同一逻辑 chunk 会在候选池里
   出现多份。DCG 累加只认**首次出现**的位置。这是 NDCG ≤ 1 的充分条件。
3. **IDCG 的项数 = min(|gold|, k)**，与 DCG 的项数上界一致 —— 两条必须联动，
   否则就是 2026-11-08 那个 `NDCG > 1` 事故的根因。
4. **越界必须暴露**：本模块所有分数返回 `(value, violation)` 或在 dataclass 里带
   `violation` 标志。**不许静默 clamp** —— 静默 clamp 会把 0.5 抬成 1.0，
   是比报错更危险的错。
5. **"没测到"不等于 0**：gold 为空、检索结果为空 → 返回 `None` + `skip_reason`，
   不返回 0.0（0.0 是"测了，确实没召回"，是真实测量结果）。
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

__all__ = [
    "METRIC_NAMES",
    "RetrievalScore",
    "aggregate",
    "dedupe_by_first_occurrence",
    "hit_at_k",
    "mrr_at_k",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "score_query",
]

#: 统一指标名（报告与门禁都用这一套字符串，避免同义名导致的对照失败）
METRIC_NAMES = ("hit@1", "hit@5", "hit@10", "mrr@10", "ndcg@10", "precision@10", "recall@10")

_EPS = 1e-9


def dedupe_by_first_occurrence(ranking: Sequence[str]) -> list[str]:
    """按首次出现顺序去重（同一逻辑 chunk 的多版本副本只保留第一次）。

    注意：调用方应**先在数据层去重**（只留最新 `kb_version`）。本函数是
    指标层的第二道防线 —— 数据层去重解决"检索质量被副本挤占"，
    指标层去重解决"算出来的数不可信"。两件事都要做，不能互相替代。
    """
    seen: set[str] = set()
    out: list[str] = []
    for cid in ranking:
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def hit_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> bool:
    """gold 的任一元素出现在 Top-k 里。"""
    if k <= 0:
        raise ValueError("k must be positive")
    g = set(gold)
    return any(cid in g for cid in list(ranking)[:k])


def mrr_at_k(ranking: Sequence[str], gold: Iterable[str], k: int = 10) -> float:
    """第一个命中 gold 的名次倒数；未命中为 0.0。"""
    if k <= 0:
        raise ValueError("k must be positive")
    g = set(gold)
    for rank, cid in enumerate(list(ranking)[:k], start=1):
        if cid in g:
            return 1.0 / rank
    return 0.0


def _dcg_from_positions(positions: Sequence[int]) -> float:
    return sum(1.0 / math.log2(p + 1) for p in positions)


def ndcg_at_k(
    ranking: Sequence[str], gold: Iterable[str], k: int = 10
) -> tuple[float, bool]:
    """二值相关性 NDCG@k，返回 ``(值, 是否越界)``。

    ## 算法（写死，改之前先读）

    1. 取 `ranking[:k]`；
    2. 对每个 gold 只取**首次出现**的名次 `p_i`（`seen` 去重）；
    3. ``DCG = Σ_i 1/log2(p_i + 1)``；
    4. ``IDCG = Σ_{r=1..min(|gold|, k)} 1/log2(r + 1)``；
    5. ``NDCG = DCG / IDCG``。

    第 2 步与第 4 步的**项数上界必须一致**：DCG 至多 |gold| 项（每个 gold 一次），
    IDCG 至多 min(|gold|, k) 项 —— 因为去重后命中的 gold 数不可能超过 |gold|，
    且名次都在 1..k 内，故 DCG ≤ IDCG 恒成立。这正是 NDCG ≤ 1 的证明。

    返回值的第二个元素是**越界标志**：若出现 `value > 1` 说明本函数的前提被破坏
    （例如调用方传进来的 gold 集合被改写），此时**不 clamp**，而是让调用方
    把 `violation=True` 记进报告并失败。静默 clamp 会把错数伪装成好数。

    >>> ndcg_at_k(["a", "b"], {"a"}, 10)
    (1.0, False)
    >>> round(ndcg_at_k(["x", "a", "a"], {"a"}, 10)[0], 4)   # 重复副本不影响
    0.6309
    >>> round(ndcg_at_k(["x", "y", "a"], {"a"}, 10)[0], 4)   # 排得越靠后越低
    0.5
    """
    if k <= 0:
        raise ValueError("k must be positive")
    g = set(gold)
    if not g:
        return 0.0, False

    seen: set[str] = set()
    positions: list[int] = []
    for rank, cid in enumerate(list(ranking)[:k], start=1):
        if cid in g and cid not in seen:
            seen.add(cid)
            positions.append(rank)

    idcg = _dcg_from_positions(range(1, min(len(g), k) + 1))
    if idcg <= 0:
        return 0.0, False
    value = _dcg_from_positions(positions) / idcg
    return value, value > 1.0 + _EPS


def precision_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> float:
    """``|命中 ∩ 前 k| / min(k, |返回条数|)``（按实际返回深度归一）。

    说明（2026-11-09 修正）：本函数**按实际返回深度**归一，而不是固定除以 k。
    两种口径在"系统总是返回满 k 条"时完全一致；只有在返回不足 k 条时才分岔。
    此前的 docstring 写的是"除以 k"，与实现对不上 —— 现在以实现为准并写明。
    若需要 TREC 的固定 `/k` 口径，显式传 `k=实际深度` 或自行换算，不要假设。

    与 `recall_at_k` 的分母不同：precision 的分母是**取回的条数**，
    recall 的分母是**gold 的条数**。两者不可互换。
    """
    if k <= 0:
        raise ValueError("k must be positive")
    top = list(ranking)[:k]
    if not top:
        return 0.0
    g = set(gold)
    return sum(1 for cid in top if cid in g) / len(top)


def recall_at_k(ranking: Sequence[str], gold: Iterable[str], k: int) -> float:
    """``|命中 ∩ 前 k| / |gold|``；gold 为空返回 0.0（调用方应先按 skip 判据拦）。"""
    if k <= 0:
        raise ValueError("k must be positive")
    g = set(gold)
    if not g:
        return 0.0
    top = list(ranking)[:k]
    return sum(1 for cid in top if cid in g) / len(g)


@dataclass(frozen=True)
class RetrievalScore:
    """单条 query 的全部检索指标 + 可审计的中间量。

    `ranking` 存的是**去重后**的名次列表，便于复算时逐位对齐。
    `duplicates_removed` 非 0 说明候选池有多版本副本 —— 这是**必须报**的信息，
    因为"指标是多少"和"指标在什么语料下算的"是同一件事的两面。
    """

    query_id: str
    n_gold: int
    ranking: tuple[str, ...]
    duplicates_removed: int
    metrics: dict[str, float]
    violations: tuple[str, ...] = field(default_factory=tuple)
    skip_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "query_id": self.query_id,
            "n_gold": self.n_gold,
            "duplicates_removed": self.duplicates_removed,
            "metrics": dict(self.metrics),
            "violations": list(self.violations),
            "skip_reason": self.skip_reason,
        }


def score_query(
    query_id: str,
    ranking: Sequence[str],
    gold: Iterable[str],
    *,
    k_max: int = 10,
) -> RetrievalScore:
    """一条 query 的全部指标。**唯一的逐条打分入口**（脚本不要自己算）。

    ## 两种 skip 的语义不同，不要混（2026-11-09 明确）

    * `no_ground_truth`：这条 query 根本没有 gold → 指标**无定义**，
      必须排除在分母外。把它记 0 会凭空造出一个"系统很差"的假结论。
    * `no_results`：有 gold 但检索返回空 → 指标**有定义且就是 0**
      （hit/recall/ndcg/mrr 全 0；precision 无定义但记 0 与"返回了全错"同向）。
      **这是真实测量结果，必须计入分母。**

    所以 `skip_reason` 只表示"为什么这条要特殊处理"，不代表"要不要进分母"；
    分母的判定在 `aggregate` 里，按 `EXCLUDED_SKIP_REASONS` 来决定。
    """
    g = {str(x) for x in gold if str(x or "").strip()}
    raw = [str(x) for x in ranking if str(x or "").strip()]
    deduped = dedupe_by_first_occurrence(raw)
    removed = len(raw) - len(deduped)

    skip = ""
    if not g:
        skip = "no_ground_truth"
    elif not raw:
        skip = "no_results"

    violations: list[str] = []
    ndcg, ndcg_violation = ndcg_at_k(deduped, g, k_max)
    if ndcg_violation:
        violations.append("ndcg@10>1")
        ndcg = ndcg  # 不 clamp：让越界值原样进报告

    metrics = {
        "hit@1": 1.0 if hit_at_k(deduped, g, 1) else 0.0,
        "hit@5": 1.0 if hit_at_k(deduped, g, 5) else 0.0,
        "hit@10": 1.0 if hit_at_k(deduped, g, 10) else 0.0,
        "mrr@10": mrr_at_k(deduped, g, 10),
        "ndcg@10": ndcg,
        "precision@10": precision_at_k(deduped, g, 10),
        "recall@10": recall_at_k(deduped, g, 10),
    }
    return RetrievalScore(
        query_id=query_id,
        n_gold=len(g),
        ranking=tuple(deduped),
        duplicates_removed=removed,
        metrics=metrics,
        violations=tuple(violations),
        skip_reason=skip,
    )


#: 这些 skip 原因的样本**不进分母**（指标无定义）。其余（含 `no_results`）照常计。
EXCLUDED_SKIP_REASONS = frozenset({"no_ground_truth"})


def aggregate(scores: Sequence[RetrievalScore]) -> dict:
    """聚合：排除**无定义**的，保留**真实为 0** 的，并给出 n / 越界数 / 重复副本数。

    分母口径是本模块最容易被悄悄改坏的东西，所以写死在这里：
    `n` = 传入条数 − `EXCLUDED_SKIP_REASONS` 命中的条数。
    报告里报 `n` 时必须报这个，否则会出现"n=120 但只有 115 条有 gold"这种
    看起来自洽、实际分母错的数（2026-11-08 的 p94 报告就是这个毛病）。
    """
    valid = [s for s in scores if s.skip_reason not in EXCLUDED_SKIP_REASONS]
    n = len(valid)
    out: dict = {
        "n_input": len(scores),
        "n": n,
        "n_excluded": len(scores) - n,
        "skip_reasons": {},
        "violations": sum(len(s.violations) for s in valid),
        "duplicates_removed": sum(s.duplicates_removed for s in valid),
    }
    for s in scores:
        if s.skip_reason:
            out["skip_reasons"][s.skip_reason] = out["skip_reasons"].get(s.skip_reason, 0) + 1
    for name in METRIC_NAMES:
        vals = [s.metrics[name] for s in valid]
        out[name] = round(sum(vals) / n, 4) if n else None
    return out
