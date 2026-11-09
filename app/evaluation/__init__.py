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
    # --- RAG 检索指标与统计（2026-11-09）---
    "METRIC_NAMES",
    # --- 原有 ---
    "Bbox",
    "ClassificationMetrics",
    "Detection",
    "DetectionMatchResult",
    "RetrievalScore",
    "bootstrap_ci",
    "box_dice",
    "box_iou",
    "classification_metrics",
    "confusion_matrix",
    "dedupe_by_first_occurrence",
    "detection_rate",
    "exact_mcnemar_power",
    "hit_at_k",
    "match_detections",
    "mcnemar_exact",
    "mcnemar_from_paired",
    "min_detectable_delta",
    "mrr_at_k",
    "ndcg_at_k",
    "paired_bootstrap_diff",
    "precision_at_k",
    "recall_at_k",
    "required_n_for_delta",
    "required_n_for_delta_exact",
    "roc_auc",
    "score_query",
    "two_proportion_n",
]

# RAG 检索指标（2026-11-09 新增）：全仓**唯一**的 hit/MRR/NDCG 实现。
# 加进包导出是为了让脚本 import 到同一份，而不是各自抄一份 ——
# 抄一份就修不全（p94 修了 p94b 漏修，见 reports/RAG阶段5_评估体系前沿研究_20261109.md）。
from app.evaluation.retrieval_metrics import (
    METRIC_NAMES,
    RetrievalScore,
    dedupe_by_first_occurrence,
    hit_at_k,
    mrr_at_k,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    score_query,
)
from app.evaluation.statistics import (
    bootstrap_ci,
    exact_mcnemar_power,
    mcnemar_exact,
    mcnemar_from_paired,
    min_detectable_delta,
    paired_bootstrap_diff,
    required_n_for_delta,
    required_n_for_delta_exact,
    two_proportion_n,
)
