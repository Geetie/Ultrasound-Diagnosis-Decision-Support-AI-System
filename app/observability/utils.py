"""OBS可观测性工具函数 — 统一百分位计算等公共算法。"""

__all__ = ["calculate_percentile"]



def calculate_percentile(data: list[float], percentile: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    n = len(sorted_data)
    rank = (percentile / 100.0) * (n - 1)
    lower_idx = int(rank)
    upper_idx = min(lower_idx + 1, n - 1)
    fraction = rank - lower_idx
    return sorted_data[lower_idx] + fraction * (sorted_data[upper_idx] - sorted_data[lower_idx])
