"""评估指标模块（纯函数，口径见模块 docstring）。"""
from app.evaluation.metrics import (
    Bbox,
    ClassificationMetrics,
    Detection,
    DetectionMatchResult,
    box_dice,
    box_iou,
    classification_metrics,
    confusion_matrix,
    detection_rate,
    match_detections,
    roc_auc,
)

__all__ = [
    "Bbox",
    "ClassificationMetrics",
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
