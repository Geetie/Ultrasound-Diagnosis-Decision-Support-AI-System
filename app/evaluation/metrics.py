"""评估指标（纯函数，无外部依赖）。

## 为什么单独起一个域

评估指标是**跨模块**能力（分类、检索、检出、分割各有消费者），不属于分词/风险/
一致性任一既有域。全仓此前**没有** Dice/IoU/AUC/检出率的任何实现
（`app/knowledge/segmentation_validation.py` 名字像图像分割，实为**中文分词 F1**）。

## 口径总则（新增指标前必须先读这一节）

1. **不引入 sklearn**：它不在任何 `requirements*.txt` 里。本模块手写实现，
   与仓库既有惯例一致（`app/inference/consistency/report.py` 的 `FalseStats`、
   `app/knowledge/baseline_annotation.py` 的 Cohen's Kappa 都是手写矩阵）。
2. **"未定义"不等于"最差"**：
   - 分母为 0 的比例类指标（precision/recall/F1/accuracy）沿用既有惯例返回 `0.0`，
     并同时给出 `has_valid_*` 标志，供调用方区分"真的 0 分"与"根本没有可算的样本"；
   - **AUC 例外**：单类别（全正或全负）时 AUC 无定义，返回 `None` 而不是 `0.0`
     —— 把"未定义"记成 0.0 等于记成"完美反向"，是最容易被误读成结论的那种错。
3. **宏平均 vs 微平均**：`classification_metrics` 默认**宏平均**（每类等权，
   适合类别不平衡且有临床意义的场景），并同时返回各类明细；需要微平均时用
   `multiclass_metrics(...).micro_*` 字段。调用方必须显式选择，禁止含糊。
4. **检出只问"框住没有"，不问"分对没有"**：`detection_rate` 的匹配默认不要求
   类别标签一致（类别正确性由分类指标衡量）。若业务要求"类别也必须对"，
   显式传 `require_label_match=True` 并自行承担口径变化。
5. **坐标口径**：`bbox = (x, y, w, h)`，与 `app/models/schemas.NavigationAnnotation.bbox`
   一致。IoU/Dice 是**尺度无关**的，归一化(0~1)与像素坐标结果相同。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "Bbox",
    "Detection",
    "DetectionMatchResult",
    "box_dice",
    "box_iou",
    "classification_metrics",
    "confusion_matrix",
    "detection_rate",
    "match_detections",
    "roc_auc",
]

Bbox = tuple[float, float, float, float]


# ----------------------------------------------------------------------
# 分类：混淆矩阵 / 指标
# ----------------------------------------------------------------------
def confusion_matrix(
    y_true: Sequence[Any], y_pred: Sequence[Any], labels: Sequence[Any]
) -> list[list[int]]:
    """多分类混淆矩阵，`matrix[i][j]` = 真实为 `labels[i]` 而被预测为 `labels[j]` 的样本数。

    Raises:
        ValueError: 长度不一致，或出现 `labels` 之外的取值（**不静默丢弃**：
            悄悄忽略未知类别会让指标看起来比实际好）。
    """
    if len(y_true) != len(y_pred):
        raise ValueError(
            f"y_true 与 y_pred 长度必须一致（{len(y_true)} vs {len(y_pred)}）"
        )
    index = {label: i for i, label in enumerate(labels)}
    matrix = [[0] * len(labels) for _ in labels]
    for actual, predicted in zip(y_true, y_pred, strict=True):
        if actual not in index or predicted not in index:
            raise ValueError(
                f"出现 labels 之外的取值：actual={actual!r}, predicted={predicted!r}"
            )
        matrix[index[actual]][index[predicted]] += 1
    return matrix


@dataclass(frozen=True)
class ClassificationMetrics:
    """分类指标。`has_valid_*` 用于区分"真的是 0"与"样本不足无法计算"。"""

    labels: tuple[Any, ...]
    matrix: tuple[tuple[int, ...], ...]
    per_class_precision: dict[Any, float]
    per_class_recall: dict[Any, float]
    per_class_f1: dict[Any, float]
    macro_precision: float
    macro_recall: float
    macro_f1: float
    micro_precision: float
    micro_recall: float
    micro_f1: float
    accuracy: float
    total: int
    has_valid_per_class: dict[Any, bool]
    has_valid_macro: bool
    has_valid_accuracy: bool


def _safe_div(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator else 0.0


def classification_metrics(
    y_true: Sequence[Any], y_pred: Sequence[Any], labels: Sequence[Any]
) -> ClassificationMetrics:
    """分类指标（宏平均为主，同时给微平均与逐类明细）。

    约定（与 `app/inference/consistency/report.py` 的 `FalseStats` 一致）：
    - precision = TP/(TP+FP)、recall = TP/(TP+FN)、f1 = 2PR/(P+R)；
    - 任一分母为 0 → 该指标 `0.0`，并把对应的 `has_valid_*` 置 False。
    """
    labels = tuple(labels)
    matrix = confusion_matrix(y_true, y_pred, labels)

    tp_total = 0
    predicted_total = 0
    actual_total = 0
    per_precision: dict[Any, float] = {}
    per_recall: dict[Any, float] = {}
    per_f1: dict[Any, float] = {}
    per_valid: dict[Any, bool] = {}

    for i, label in enumerate(labels):
        tp = matrix[i][i]
        predicted = sum(matrix[r][i] for r in range(len(labels)))
        actual = sum(matrix[i])
        precision = _safe_div(tp, predicted)
        recall = _safe_div(tp, actual)
        per_precision[label] = precision
        per_recall[label] = recall
        per_f1[label] = _safe_div(2 * precision * recall, precision + recall)
        per_valid[label] = predicted > 0 and actual > 0
        tp_total += tp
        predicted_total += predicted
        actual_total += actual

    macro_precision = _safe_div(sum(per_precision.values()), len(labels))
    macro_recall = _safe_div(sum(per_recall.values()), len(labels))
    macro_f1 = _safe_div(sum(per_f1.values()), len(labels))
    micro_precision = _safe_div(tp_total, predicted_total)
    micro_recall = _safe_div(tp_total, actual_total)
    micro_f1 = _safe_div(2 * micro_precision * micro_recall, micro_precision + micro_recall)
    total = len(y_true)

    return ClassificationMetrics(
        labels=labels,
        matrix=tuple(tuple(row) for row in matrix),
        per_class_precision=per_precision,
        per_class_recall=per_recall,
        per_class_f1=per_f1,
        macro_precision=macro_precision,
        macro_recall=macro_recall,
        macro_f1=macro_f1,
        micro_precision=micro_precision,
        micro_recall=micro_recall,
        micro_f1=micro_f1,
        accuracy=_safe_div(tp_total, total),
        total=total,
        has_valid_per_class=per_valid,
        has_valid_macro=len(labels) > 0 and total > 0,
        has_valid_accuracy=total > 0,
    )


# ----------------------------------------------------------------------
# ROC-AUC（二分类，秩和法，处理并列值）
# ----------------------------------------------------------------------
def roc_auc(y_true: Sequence[bool | int], scores: Sequence[float]) -> float | None:
    """二分类 ROC-AUC（Mann-Whitney U 秩和法，**正确处理并列分数**）。

    口径：
    - 只接受二分类（正类=1/True）。多分类请用一对多自行展开并聚合；
    - 分数越高表示越像正类；
    - **单类别时返回 `None`**（AUC 无定义）—— 不返回 0.0，理由见模块 docstring；
    - 无样本时同样返回 `None`；
    - 全部并列时返回 0.5（随机水平），这是秩和法的自然结果，不是近似。

    Raises:
        ValueError: 长度不一致，或 y_true 含 0/1 之外的值。
    """
    if len(y_true) != len(scores):
        raise ValueError(
            f"y_true 与 scores 长度必须一致（{len(y_true)} vs {len(scores)}）"
        )
    positives = [float(s) for label, s in zip(y_true, scores, strict=True) if label in (1, True)]
    negatives = [float(s) for label, s in zip(y_true, scores, strict=True) if label in (0, False)]
    n_pos, n_neg = len(positives), len(negatives)
    if len(y_true) != n_pos + n_neg:
        raise ValueError("y_true 只能包含 0/1（或 False/True）")
    if n_pos == 0 or n_neg == 0:
        return None

    # 秩和法：把正样本的秩相加，并列值取平均秩，再换算成 AUC
    combined = sorted(
        [(score, 1) for score in positives] + [(score, 0) for score in negatives]
    )
    ranks: list[float] = [0.0] * len(combined)
    i = 0
    while i < len(combined):
        j = i
        while j + 1 < len(combined) and combined[j + 1][0] == combined[i][0]:
            j += 1
        average_rank = (i + j) / 2 + 1  # 1-based 平均秩
        for k in range(i, j + 1):
            ranks[k] = average_rank
        i = j + 1

    positive_rank_sum = sum(rank for rank, (_, is_pos) in zip(ranks, combined, strict=True) if is_pos)
    u = positive_rank_sum - n_pos * (n_pos + 1) / 2
    return u / (n_pos * n_neg)


# ----------------------------------------------------------------------
# 框 IoU / Dice
# ----------------------------------------------------------------------
def _intersection_area(a: Bbox, b: Bbox) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ax + aw, bx + bw)
    bottom = min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0.0
    return (right - left) * (bottom - top)


def box_iou(a: Bbox, b: Bbox) -> float:
    """两框交并比。任一框面积为 0（退化框）时返回 0.0（不抛错、不产生 inf）。"""
    inter = _intersection_area(a, b)
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def box_dice(a: Bbox, b: Bbox) -> float:
    """两框 Dice 系数 = 2|A∩B| / (|A|+|B|)。

    注意：框的 Dice **不等于** IoU 的线性变换，两者在检出匹配阈值上不可混用；
    本仓库既无 IoU 阈值也无 Dice 阈值的既有约定，使用方需显式选定并记录。
    """
    inter = _intersection_area(a, b)
    total = a[2] * a[3] + b[2] * b[3]
    return 2 * inter / total if total > 0 else 0.0


# ----------------------------------------------------------------------
# 检出匹配与检出率
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Detection:
    """一次预测检出。`bbox` 口径同 `NavigationAnnotation.bbox`（x, y, w, h）。"""

    bbox: Bbox
    confidence: float = 1.0
    label: str | None = None


@dataclass(frozen=True)
class DetectionMatchResult:
    """检出匹配结果。`pairs` 为 (预测序号, 金标准序号, IoU) 的**一对一**匹配。"""

    true_positives: int
    false_positives: int
    false_negatives: int
    pairs: tuple[tuple[int, int, float], ...] = field(default_factory=tuple)
    total_ground_truth: int = 0
    total_predictions: int = 0

    @property
    def has_valid_detection_rate(self) -> bool:
        return self.total_ground_truth > 0

    @property
    def detection_rate(self) -> float:
        """检出率 = TP / 金标准总数（即框级 recall）。无金标准时为 0.0（配 `has_valid_*`）。"""
        if self.total_ground_truth == 0:
            return 0.0
        return self.true_positives / self.total_ground_truth

    @property
    def precision(self) -> float:
        if self.total_predictions == 0:
            return 0.0
        return self.true_positives / self.total_predictions


def match_detections(
    predictions: Sequence[Detection],
    ground_truth: Sequence[Bbox],
    *,
    iou_threshold: float,
    confidence_threshold: float = 0.0,
    gt_labels: Sequence[str | None] | None = None,
    require_label_match: bool = False,
) -> DetectionMatchResult:
    """按 IoU 阈值做**贪心一对一**匹配，返回 TP/FP/FN。

    ## 口径

    - **贪心**：按「IoU 降序，同分按置信度降序」依次配对，每个金标准/预测**至多配一次**
      —— 这条是检出率的关键不变量：同一病灶被重复检出**不得**重复计入 TP，
      否则"多报几次"就能刷高检出率。
    - `confidence_threshold` 之下的预测**先被丢弃**（视为没检出）。默认 0.0；
      生产建议传 `settings.navigation_confidence_threshold`（默认 0.75），
      不要在这里写死，否则口径会随配置漂移。
    - `iou_threshold` **无仓库既有约定**，必须由调用方显式给出并记录（例如 0.5）；
      本函数不提供默认值，就是为了避免"悄悄用了一个没人review过的阈值"。
    - `require_label_match=True` 时要求标签完全一致才算匹配（严格口径）；
      默认 False —— 检出率只问"有没有框住"。
    """
    if not 0.0 <= iou_threshold <= 1.0:
        raise ValueError(f"iou_threshold 必须在 [0,1] 内，收到 {iou_threshold}")
    if require_label_match and gt_labels is None:
        raise ValueError("require_label_match=True 时必须提供 gt_labels")

    kept = [d for d in predictions if d.confidence >= confidence_threshold]
    candidates: list[tuple[float, float, int, int]] = []
    for pred_index, detection in enumerate(kept):
        for gt_index, gt_box in enumerate(ground_truth):
            if (
                require_label_match
                and gt_labels is not None
                and detection.label != gt_labels[gt_index]
            ):
                continue
            iou = box_iou(detection.bbox, gt_box)
            if iou >= iou_threshold:
                candidates.append((iou, detection.confidence, pred_index, gt_index))

    candidates.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    matched_pred: set[int] = set()
    matched_gt: set[int] = set()
    pairs: list[tuple[int, int, float]] = []
    for iou, _confidence, pred_index, gt_index in candidates:
        if pred_index in matched_pred or gt_index in matched_gt:
            continue
        matched_pred.add(pred_index)
        matched_gt.add(gt_index)
        pairs.append((pred_index, gt_index, iou))

    return DetectionMatchResult(
        true_positives=len(pairs),
        false_positives=len(kept) - len(matched_pred),
        false_negatives=len(ground_truth) - len(matched_gt),
        pairs=tuple(pairs),
        total_ground_truth=len(ground_truth),
        total_predictions=len(kept),
    )


def detection_rate(
    predictions: Sequence[Detection],
    ground_truth: Sequence[Bbox],
    *,
    iou_threshold: float,
    confidence_threshold: float = 0.0,
    gt_labels: Sequence[str | None] | None = None,
    require_label_match: bool = False,
) -> float:
    """病灶检出率（框级 recall）= TP / 金标准总数。

    便携入口，等价于 `match_detections(...).detection_rate`；无金标准时返回 0.0，
    调用方应同时检查 `match_detections(...).has_valid_detection_rate`。
    """
    return match_detections(
        predictions,
        ground_truth,
        iou_threshold=iou_threshold,
        confidence_threshold=confidence_threshold,
        gt_labels=gt_labels,
        require_label_match=require_label_match,
    ).detection_rate
