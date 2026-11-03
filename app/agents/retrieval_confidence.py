"""检索置信度的**归一化与升档判据**（N4 / G4）。

## 缺陷（真机数据实证，2026-11-03）

`AdaptiveRAGRouter.assess` 里：
```python
is_low_confidence = retrieval_confidence < threshold   # threshold = organ_threshold
```
但两者**量纲完全不同**：
- `retrieval_confidence` 来自 `_retrieval_confidence()`：优先 cross-encoder 的
  `rerank_score`，其次原始检索分。真机实测取值域 **1.705 ~ 5.207**（logit 尺度）；
- `organ_threshold` 是**结论置信度**门槛（`response.confidence` ∈ 0~1，thyroid=0.85）。

于是 `5.207 < 0.85` 恒为 False → **低置信度升档从未触发过**。
这不是"阈值定多少"的问题，是拿错了尺子（与 A.88 A1 的 min_score 量纲错配同族）。

## 本模块职责

把原始检索分归一化到 0~1（`:func:`normalize_retrieval_confidence`），
再与一个**同量纲**的可配置阈值比较（`:func:`is_low_retrieval_confidence`）。
归一化用真机实测的锚点区间，不是拍脑袋的 0~1 映射：

- `CONF_FLOOR = 1.5`：实测最小值 1.705 之下视为"极差"（0 分）；
- `CONF_CEIL = 5.5`：实测最大值 5.207 之上视为"极好"（1 分）。

区间外做截断而非外推，避免异常分把判据拉飞。
"""

from __future__ import annotations

__all__ = [
    "CONF_FLOOR",
    "CONF_CEIL",
    "DEFAULT_LOW_CONFIDENCE_THRESHOLD",
    "normalize_retrieval_confidence",
    "is_low_retrieval_confidence",
]

#: 归一化下锚（真机实测最小值 1.705 之下 → 0.0）
CONF_FLOOR: float = 1.5
#: 归一化上锚（真机实测最大值 5.207 之上 → 1.0）
CONF_CEIL: float = 5.5

#: 默认升档阈值（归一化后的 0~1 尺度）。
#: 定标依据（n=12 真机数据：min 1.705 / P10 2.232 / P25 2.371 / P50 2.816）：
#: 归一化后最差一例 = (1.705-1.5)/(5.5-1.5) ≈ 0.051。
#: 取 0.10 → 只升档最差一档（约 1/12 ≈ 8%），其余不升；
#: 升档成本实测 +2.2 倍（CRAG 触发时 total 60.2s vs 常态 24.9~28.7s），
#: 因此阈值不能取高（P25≈0.22 会升档 25%，成本明显偏高）。
DEFAULT_LOW_CONFIDENCE_THRESHOLD: float = 0.10


def normalize_retrieval_confidence(raw: float | None) -> float | None:
    """把原始检索分归一化到 0~1；``None`` 透传（未测 ≠ 0 分）。

    纯函数：区间外截断（不外推），保证判据不会被异常分带飞。
    """
    if raw is None:
        return None
    if not isinstance(raw, (int, float)):
        return None
    value = float(raw)
    if value <= CONF_FLOOR:
        return 0.0
    if value >= CONF_CEIL:
        return 1.0
    return (value - CONF_FLOOR) / (CONF_CEIL - CONF_FLOOR)


def is_low_retrieval_confidence(
    raw: float | None,
    threshold: float = DEFAULT_LOW_CONFIDENCE_THRESHOLD,
) -> bool:
    """本次检索是否低置信到需要升档（同量纲比较）。

    Args:
        raw: 原始检索分（`rerank_score` 或 RRF 分），可为 ``None``（未测）。
        threshold: 归一化尺度上的阈值（默认见模块常量）。

    Returns:
        只有**真测到分数**且归一化后低于阈值才为 ``True``。
        ``raw is None`` → ``False``（未测不升档：拿不到分就去升档会把
        "没测"当成"很差"，与 N2/N4 的一贯口径相悖）。
    """
    normalized = normalize_retrieval_confidence(raw)
    if normalized is None:
        return False
    return normalized < float(threshold)
