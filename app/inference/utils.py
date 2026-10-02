import math


def percentile(values: list[float], pct: float) -> float:
    """计算百分位数（线性插值法）。

    使用线性插值法计算百分位数。函数内部会对输入列表排序，
    调用方无需预排序。对于小样本（<30个），线性插值法与
    numpy的最近秩法结果差异可能较大。

    Args:
        values: 数值列表（无需预排序）
        pct: 百分位数（0-100）

    Returns:
        百分位数值
    """
    if not values:
        return 0.0
    sorted_values = sorted(values)
    n = len(sorted_values)
    rank = pct / 100.0 * (n - 1)
    lower = int(math.floor(rank))
    upper = min(lower + 1, n - 1)
    frac = rank - lower
    return sorted_values[lower] * (1 - frac) + sorted_values[upper] * frac
