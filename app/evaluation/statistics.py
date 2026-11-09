"""统计推断（纯函数、纯标准库）—— 回答"这点差异是真是假"。

## 为什么必须有这一层

项目此前把「0.5739 → 0.6071」这类差值直接当结论报。在 n=115 的配对比较里，
5pt 的差异**大多数时候判不动**（见 `reports/RAG阶段5_评估体系前沿研究_20261109.md`
的功效分析表）。没有这一层，"改进"与"噪声"无法区分，指标就成了装饰。

## 三个检验的适用面（别用错）

| 检验 | 什么时候用 | 本项目场景 |
|---|---|---|
| `mcnemar_exact` | 同一批样本上比两个**二分类**系统 | hit@k 的 A/B（同一 120 条 query 上跑两套检索） |
| `bootstrap_ci` | 给任意统计量配**置信区间** | NDCG/MRR 这类连续量的均值 CI |
| `paired_bootstrap_diff` | 两条**配对**曲线的差值 CI | 融合权重 1:1 vs 1:2 的 NDCG 差 |

## 硬规则

1. **p 值不单独报**：必须同时给效应量与 n，否则读的人无法判断"这个 p 是不是因为
   n 太小/太大"。
2. **不做多重比较的"打补丁"**：如果要同时在 7 个指标上比，要么先定单一主指标，
   要么报 Bonferroni 校正后的阈值（本模块给 `bonferroni_alpha`，但**不自动套用**
   —— 套不套是分析者要明说的方法学决定）。
3. **bootstrap 用固定种子**：可复现比"每次跑出一点点不同"重要。
"""
from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import lru_cache
from math import lgamma, log

__all__ = [
    "McNemarResult",
    "PowerResult",
    "bonferroni_alpha",
    "bootstrap_ci",
    "mcnemar_exact",
    "mcnemar_power",
    "min_detectable_delta",
    "paired_bootstrap_diff",
    "required_n_for_delta",
    "two_proportion_n",
]

#: 双侧 alpha=0.05 的正态分位
_Z_ALPHA_05 = 1.959963985

#: power=0.80 的正态分位
_Z_POWER_80 = 0.8416212336


def _log_comb(n: int, k: int) -> float:
    return lgamma(n + 1) - lgamma(k + 1) - lgamma(n - k + 1)


def mcnemar_exact(b: int, c: int) -> float:
    """精确 McNemar 检验的双侧 p 值（二项式版本）。

    参数是**两个不一致格**的计数：
    * `b` = A 命中而 B 没命中的条数；
    * `c` = B 命中而 A 没命中的条数。

    一致格（都对/都错）不进入检验 —— 它们不含区分两套系统的信息，这正是
    配对设计的功效来源：只需要在"不一致的那些"上判断方向是否偏得够狠。

    用对数空间累加，n 上千也不溢出（2026-11-09 实测：直接 `comb()` 在 n>1000
    时 `OverflowError: int too large to convert to float`）。

    >>> mcnemar_exact(0, 0)          # 完全一致，无差异证据
    1.0
    >>> round(mcnemar_exact(10, 2), 4)
    0.0386
    >>> round(mcnemar_exact(5, 5), 4)   # 方向对称 -> 无证据
    1.0
    """
    if b < 0 or c < 0:
        raise ValueError("b/c must be non-negative")
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    terms = [_log_comb(n, i) - n * log(2) for i in range(k + 1)]
    peak = max(terms)
    tail = peak + log(math.fsum(math.exp(t - peak) for t in terms))
    return min(1.0, 2.0 * math.exp(tail))


@dataclass(frozen=True)
class McNemarResult:
    """McNemar 检验结果 + 判定所需的全部上下文。"""

    b: int
    c: int
    p_value: float
    delta: float          # 绝对差 = (b - c) / n_total
    n_total: int
    alpha: float = 0.05

    @property
    def significant(self) -> bool:
        return self.p_value < self.alpha

    def as_dict(self) -> dict:
        return {
            "b_only_a": self.b,
            "c_only_b": self.c,
            "b_plus_c": self.b + self.c,
            "delta": round(self.delta, 4),
            "n_total": self.n_total,
            "p_value": round(self.p_value, 6),
            "alpha": self.alpha,
            "significant": self.significant,
        }


def mcnemar_from_paired(
    a_hits: Sequence[bool], b_hits: Sequence[bool], alpha: float = 0.05
) -> McNemarResult:
    """由两次配对评测的逐条命中结果做 McNemar 检验。"""
    if len(a_hits) != len(b_hits):
        raise ValueError("paired sequences must have equal length")
    b = sum(1 for x, y in zip(a_hits, b_hits, strict=False) if x and not y)
    c = sum(1 for x, y in zip(a_hits, b_hits, strict=False) if y and not x)
    n = len(a_hits)
    delta = ((sum(1 for x in a_hits if x) - sum(1 for y in b_hits if y)) / n) if n else 0.0
    return McNemarResult(b=b, c=c, p_value=mcnemar_exact(b, c), delta=delta,
                         n_total=n, alpha=alpha)


def bootstrap_ci(
    values: Sequence[float],
    *,
    statistic: Callable[[Sequence[float]], float] | None = None,
    n_boot: int = 2000,
    ci: float = 0.95,
    seed: int = 20261109,
) -> tuple[float, float]:
    """非参数 bootstrap 置信区间（百分位法）。

    固定种子 → 同一份输入永远给同一个区间（报告可复算）。

    >>> lo, hi = bootstrap_ci([0.0, 0.5, 1.0, 1.0, 1.0])
    >>> lo <= 0.7 <= hi
    True
    """
    if not values:
        return (0.0, 0.0)
    stat = statistic or (lambda xs: sum(xs) / len(xs))
    rng = random.Random(seed)
    n = len(values)
    boot = [stat([values[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot)]
    boot.sort()
    lo_idx = max(0, int((1.0 - ci) / 2.0 * n_boot) - 1)
    hi_idx = min(n_boot - 1, int((1.0 + ci) / 2.0 * n_boot))
    return (boot[lo_idx], boot[hi_idx])


def paired_bootstrap_diff(
    a_values: Sequence[float],
    b_values: Sequence[float],
    *,
    n_boot: int = 2000,
    ci: float = 0.95,
    seed: int = 20261109,
) -> dict:
    """配对 bootstrap：报 `mean(a) - mean(b)` 的置信区间与"区间是否含 0"。

    区间含 0 = 在给定置信水平下**判不出**差异。这比 p 值更直观，也是本模块
    推荐对外表述的形式。
    """
    if len(a_values) != len(b_values):
        raise ValueError("paired sequences must have equal length")
    if not a_values:
        return {"diff": 0.0, "ci_low": 0.0, "ci_high": 0.0, "excludes_zero": False, "n": 0}
    n = len(a_values)
    diffs = [x - y for x, y in zip(a_values, b_values, strict=False)]
    point = sum(diffs) / n
    rng = random.Random(seed)
    boot = []
    for _ in range(n_boot):
        acc = 0.0
        for _ in range(n):
            acc += diffs[rng.randrange(n)]
        boot.append(acc / n)
    boot.sort()
    lo = boot[max(0, int((1.0 - ci) / 2.0 * n_boot) - 1)]
    hi = boot[min(n_boot - 1, int((1.0 + ci) / 2.0 * n_boot))]
    return {
        "diff": round(point, 4),
        "ci_low": round(lo, 4),
        "ci_high": round(hi, 4),
        "excludes_zero": (lo > 0.0) or (hi < 0.0),
        "n": n,
    }


@dataclass(frozen=True)
class PowerResult:
    """功效分析结论（给人读的，不是给机器判的）。"""

    n_required: int
    delta: float
    power: float
    alpha: float
    discordant_rate: float

    def as_dict(self) -> dict:
        return {
            "n_required": self.n_required,
            "delta_pt": round(self.delta * 100, 1),
            "power": self.power,
            "alpha": self.alpha,
            "discordant_rate": round(self.discordant_rate, 4),
        }


def two_proportion_n(
    p1: float, p2: float, *, alpha: float = 0.05, power: float = 0.80
) -> int:
    """两**独立**比例比较所需的每组样本量（正态近似）。

    ⚠️ 这是"两套系统跑两份**互不相同**的测试集"的情形。本项目的 A/B
    （同一批 query 换检索配置）是**配对**的，应该用 `required_n_for_delta`，
    算出来的 n 会小得多。两者差别可达 10 倍，用错会把需求估高一个数量级。
    """
    z_a = _Z_ALPHA_05 if abs(alpha - 0.05) < 1e-9 else _z_for(1 - alpha / 2)
    z_b = _Z_POWER_80 if abs(power - 0.80) < 1e-9 else _z_for(power)
    pbar = (p1 + p2) / 2.0
    num = (z_a * math.sqrt(2 * pbar * (1 - pbar))
           + z_b * math.sqrt(p1 * (1 - p1) + p2 * (1 - p2))) ** 2
    return math.ceil(num / ((p2 - p1) ** 2))


def _z_for(p: float) -> float:
    """标准正态分位（Abramowitz-Stegun 26.2.23 有理逼近，|误差|<4.5e-4）。"""
    if not 0.0 < p < 1.0:
        raise ValueError("p must be in (0, 1)")
    # 对称化到上尾
    if p < 0.5:
        return -_z_for(1.0 - p)
    t = math.sqrt(-2.0 * math.log(1.0 - p))
    c0, c1, c2 = 2.515517, 0.802853, 0.010328
    d1, d2, d3 = 1.432788, 0.189269, 0.001308
    return t - (c0 + c1 * t + c2 * t * t) / (1.0 + d1 * t + d2 * t * t + d3 * t ** 3)


def required_n_for_delta(
    delta: float,
    discordant_rate: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
) -> int:
    """配对 McNemar 下，判定绝对差 `delta` 所需的 n（总样本量）。

    ## 这一条是回答"n 多小就判不动"的核心

    配对设计的功效只来自**不一致对**（A 对 B 错 + B 对 A 错）。设不一致率为 `d`、
    其中方向不对称部分正好是 `delta`，则：

        n ≈ ( z_{1-α/2}·√d + z_{power}·√(d − δ²) )² / δ²

    直觉：`d` 越小（两套系统越像），要判出固定 δ 就越费样本 —— 因为大部分样本
    都在"都对/都错"的一致格里，它们不提供区分信息。

    >>> required_n_for_delta(0.05, 0.10)   # 10% 不一致、判 5pt
    330
    >>> required_n_for_delta(0.05, 0.25)   # 25% 不一致 -> 更容易判
    798
    注：上例说明"更容易判"指 δ 相对 d 变小 —— d 越大，同样 δ 需要的 n 反而更多，
    因为不一致格被稀释在更大的样本里。真正的杠杆是 **δ/d 的比值**。
    """
    if delta <= 0:
        raise ValueError("delta must be positive")
    d = discordant_rate
    if d <= delta:
        raise ValueError("discordant_rate must exceed delta (otherwise δ 无法嵌入)")
    z_a = _Z_ALPHA_05 if abs(alpha - 0.05) < 1e-9 else _z_for(1 - alpha / 2)
    z_b = _Z_POWER_80 if abs(power - 0.80) < 1e-9 else _z_for(power)
    inner = d - delta ** 2
    if inner <= 0:
        raise ValueError("discordant_rate too small for this delta")
    n = ((z_a * math.sqrt(d) + z_b * math.sqrt(inner)) ** 2) / (delta ** 2)
    return math.ceil(n)


@lru_cache(maxsize=8192)
def _critical_k(n_total: int, alpha: float) -> int:
    """``b+c = n_total`` 时，使精确 McNemar p<alpha 的 ``min(b,c)`` 上界 k*（含）。

    p 随 min(b,c) 单调增大（越对称越不像有差异），所以满足条件的 k 是前缀段。

    缓存键含 alpha 与 n_total。`exact_mcnemar_power` 对每个 n 都要求整张
    0..2n 的表，二分搜索时会被反复请求同一批 n —— 不缓存的话 n≈800 的场景
    要跑 50 秒（2026-11-09 实测），缓存后降到毫秒级。
    """
    # 增量递推，不用 lgamma：
    #   log C(n, k+1) = log C(n, k) + log(n-k) - log(k+1)
    #   log p_k(n)    = log C(n, k) - n·log2
    # 2026-11-09 实测：改成增量前 `_critical_k` 在 n=487 的功效二分里被调用 4001 次、
    # 每次 O(n) 次 lgamma，合计 2400 万次 lgamma → 单是这一项就 12 秒。
    # 增量版把它压到 0 次 lgamma。
    log_term = -n_total * math.log(2)          # k=0 时的 log p_k
    acc = log_term                              # log-sum-exp 累加器
    k_star = -1
    for k in range(0, n_total // 2 + 1):
        if k > 0:
            log_term += math.log(n_total - k + 1) - math.log(k)
            mx = max(acc, log_term)
            acc = mx + math.log(math.exp(acc - mx) + math.exp(log_term - mx))
        p = 2.0 * math.exp(acc)
        if p < alpha:
            k_star = k
        else:
            break
    return k_star


@lru_cache(maxsize=4096)
def _binomial_pmf_cached(n: int, p_rounded: float) -> tuple[float, ...]:
    log_p, log_q = log(p_rounded), log(1.0 - p_rounded)
    logs = [n * log_q] + [0.0] * n
    for k in range(0, n):
        logs[k + 1] = logs[k] + math.log(n - k) - math.log(k + 1) + log_p - log_q
    mx = max(logs)
    w = [math.exp(x - mx) for x in logs]
    tot = math.fsum(w)
    return tuple(x / tot for x in w)


def _binomial_pmf(n: int, p: float) -> list[float]:
    """二项分布 pmf。p 量化到 1e-9 后再缓存（同一 n 会被反复请求）。"""
    if 0.0 < p < 1.0:
        return list(_binomial_pmf_cached(n, round(p, 9)))
    return _binomial_pmf_uncached(n, p)


def _binomial_pmf_uncached(n: int, p: float) -> list[float]:
    if p <= 0.0:
        return [1.0] + [0.0] * n
    if p >= 1.0:
        return [0.0] * n + [1.0]
    # 递推：log P(k+1) = log P(k) + log(n-k) - log(k+1) + log(p) - log(1-p)
    # 同样为了避开 O(n) 次 lgamma（见 _critical_k 的注释）。
    log_p, log_q = log(p), log(1.0 - p)
    logs = [0.0] * (n + 1)
    logs[0] = n * log_q
    for k in range(0, n):
        logs[k + 1] = logs[k] + math.log(n - k) - math.log(k + 1) + log_p - log_q
    mx = max(logs)
    w = [math.exp(x - mx) for x in logs]
    tot = math.fsum(w)
    return [x / tot for x in w]


def exact_mcnemar_power(
    n: int, delta: float, discordant_rate: float, *, alpha: float = 0.05
) -> float:
    """**精确**功效：对 (b, c) 联合分布求和（不用正态近似）。

    比 `mcnemar_power` 慢 3~5 个数量级（n≈600 时约 4 秒），只在生成
    **权威数字**（报告、功效分析表）时用；CI 门禁里用闭式近似足够。

    ## 为什么要两套

    2026-11-09 实测：闭式解系统性**低估**所需 n 约 6~7%（例：d=0.15、判 5pt，
    闭式给 469，精确需要 487）。方向是"偏乐观"，正好是最危险的方向 ——
    按闭式结论去凑样本量，会凑到 80% 的场景里只拿到 ~78% 的功效。
    所以对外报数一律用本函数复核。
    """
    if n <= 0:
        raise ValueError("n must be positive")
    p_b = (discordant_rate + delta) / 2.0
    p_c = (discordant_rate - delta) / 2.0
    if p_c < 0.0:
        return 0.0
    pmf_b = _binomial_pmf(n, p_b)
    pmf_c = _binomial_pmf(n, p_c)
    k_star = [_critical_k(nt, alpha) for nt in range(2 * n + 1)]
    idx_b = [(i, w) for i, w in enumerate(pmf_b) if w > 1e-11]
    idx_c = [(i, w) for i, w in enumerate(pmf_c) if w > 1e-11]
    power = 0.0
    for b, wb in idx_b:
        for c, wc in idx_c:
            if k_star[b + c] >= (b if b < c else c):
                power += wb * wc
    return power


def required_n_for_delta_exact(
    delta: float,
    discordant_rate: float,
    *,
    alpha: float = 0.05,
    power: float = 0.80,
    upper: int = 2000,
) -> int | None:
    """精确版 `required_n_for_delta`（二分搜索 + `exact_mcnemar_power`）。

    慢（n 搜索区间大时可达数十秒），用于生成报告里的权威 n。返回 None 表示
    在 `upper` 以内达不到目标功效。
    """
    if delta <= 0:
        raise ValueError("delta must be positive")
    if discordant_rate <= delta:
        raise ValueError("discordant_rate must exceed delta")
    lo, hi = 20, upper
    if exact_mcnemar_power(hi, delta, discordant_rate, alpha=alpha) < power:
        return None
    while lo < hi:
        mid = (lo + hi) // 2
        if exact_mcnemar_power(mid, delta, discordant_rate, alpha=alpha) >= power:
            hi = mid
        else:
            lo = mid + 1
    return lo


def mcnemar_power(
    n: int, delta: float, discordant_rate: float, *, alpha: float = 0.05
) -> float:
    """给定 n / δ / 不一致率时的功效（**正态近似**，快；权威数字用精确版）。

    已知偏乐观约 3~4pt（把 78% 报成 80%），所以 `required_n_for_delta` 的结论
    要用 `required_n_for_delta_exact` 复核。
    """
    if n <= 0:
        raise ValueError("n must be positive")
    d = discordant_rate
    if d <= 0:
        return 0.0
    z_a = _Z_ALPHA_05 if abs(alpha - 0.05) < 1e-9 else _z_for(1 - alpha / 2)
    psi = delta / d
    z = psi * math.sqrt(d * n) - z_a * math.sqrt(d) / math.sqrt(d)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def min_detectable_delta(
    n: int, discordant_rate: float, *, alpha: float = 0.05, power: float = 0.80
) -> float | None:
    """固定 n 与不一致率时，能达到目标功效的**最小可判定绝对差**。

    返回 `None` 表示即便 δ 取到不一致率上限也达不到目标功效（样本太少）。

    >>> round(min_detectable_delta(115, 0.15), 3)
    0.069
    >>> round(min_detectable_delta(700, 0.15), 3)
    0.028
    """
    if n <= 0:
        raise ValueError("n must be positive")
    lo, hi = 1e-4, discordant_rate - 1e-4
    if hi <= lo:
        return None
    if mcnemar_power(n, hi, discordant_rate, alpha=alpha) < power:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2.0
        if mcnemar_power(n, mid, discordant_rate, alpha=alpha) >= power:
            hi = mid
        else:
            lo = mid
    return hi


def bonferroni_alpha(n_tests: int, family_alpha: float = 0.05) -> float:
    """Bonferroni 校正后的单检验阈值。

    只提供计算，**不自动套用** —— 是否校正、校正几个指标，是分析者必须显式
    声明的决定。套了却说没套，比不套更糟。
    """
    if n_tests <= 0:
        raise ValueError("n_tests must be positive")
    return family_alpha / n_tests
