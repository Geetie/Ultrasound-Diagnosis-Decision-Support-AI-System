"""RAG 三指标（precision / recall / nDCG）的**生产者**（A.88 T4-⑤）。

## 修的真问题（架构真相 §3.5 / §3.3）

`retrieval_precision` / `retrieval_recall` / `retrieval_ndcg` 在规格里存在，
但仓库里**既没有指标定义，也没有任何写入点** —— 看板上永远读不到这三个数。

## 诚实边界（本模块最重要的一段）

这三个指标都**必须有 ground truth** 才算得出来（"召回了几条相关"需要先知道
哪些是相关的）。生产检索请求**没有** ground truth：

* 医生的一次判读不会附带"标准答案 chunk 列表"；
* 拿"检索到的东西"去当自己的 ground truth 是**循环证明**，会得出恒为 1.0
  的假指标（这正是 A.63 反复强调的"仪器自证"陷阱）。

所以本模块的口径是：

1. **有 ground truth 才写值**：``evaluate_retrieval_quality`` 接收
   ``relevant_chunk_ids``（调用方给的），算完才 ``set_retrieval_quality``；
2. **没有就写 ``not_measured``**：计数器 +1，label 里带**具体原因**
   （``no_ground_truth`` / ``no_results`` / ``ground_truth_not_in_result``），
   **绝不** ``set(0.0)`` —— 那会让"召回率为 0"与"从没测过"混为一谈；
3. **不编造**：无法计算时返回 ``None``，不返回"看起来合理"的 0.5 / 1.0。

## 数据来源（真实调用点）

检索侧本来就在记 ``rank`` 与命中（``ScoredChunk`` 顺序即 rank）。本模块只在
**有 ground truth 的调用**上跑：

* 评测脚本（``scripts/audit/p15_retrieval_parity.py`` 这类）拿到 golden 后可调；
* 离线一致性回放（有 expectation 的场景）。

生产在线检索**不调用** ``evaluate_retrieval_quality``（没有 ground truth），
但会调用 ``report_retrieval_quality_unavailable`` —— 于是"生产者存在且在生产
路径上被调用"是可验证的，同时看板能明确显示"这项指标当前无数据"。
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import structlog

logger = structlog.get_logger("app.observability.rag_quality")

#: 指标名（与 ``definitions.py`` 的 ``retrieval_quality`` label 一致）。
METRIC_PRECISION = "precision"
METRIC_RECALL = "recall"
METRIC_NDCG = "ndcg"
ALL_METRICS: tuple[str, ...] = (METRIC_PRECISION, METRIC_RECALL, METRIC_NDCG)

#: 未测原因码（每个分支自己的原因，不聚合默认值 —— AGENTS.md 硬规则 8）。
REASON_NO_GROUND_TRUTH = "no_ground_truth"
REASON_NO_RESULTS = "no_results"
REASON_GT_NOT_IN_RESULT = "ground_truth_not_in_result"


@dataclass(frozen=True)
class RetrievalQuality:
    """一次检索质量评估的结果（``None`` = 该指标**没测到**，不是 0）。"""

    precision: float | None
    recall: float | None
    ndcg: float | None
    ranked_ids: tuple[str, ...]
    relevant_ids: frozenset[str]
    skip_reason: str = ""

    @property
    def measured(self) -> bool:
        return not self.skip_reason and any(
            value is not None for value in (self.precision, self.recall, self.ndcg)
        )

    def as_dict(self) -> dict[str, float | None]:
        return {
            METRIC_PRECISION: self.precision,
            METRIC_RECALL: self.recall,
            METRIC_NDCG: self.ndcg,
        }


def precision_at_k(ranked: Sequence[str], relevant: Sequence[str] | set[str], k: int) -> float:
    """``|命中 ∩ 前 k| / k``。纯函数（守卫钉住）。"""
    if k <= 0:
        raise ValueError("k must be positive")
    top = list(ranked)[:k]
    if not top:
        return 0.0
    rel = set(relevant)
    return sum(1 for cid in top if cid in rel) / len(top)


def recall_at_k(ranked: Sequence[str], relevant: Sequence[str] | set[str], k: int) -> float:
    """``|命中 ∩ 前 k| / |相关|``；相关集为空时返回 ``0.0``（调用方应先用 skip 判据）。"""
    if k <= 0:
        raise ValueError("k must be positive")
    rel = set(relevant)
    if not rel:
        return 0.0
    top = list(ranked)[:k]
    return sum(1 for cid in top if cid in rel) / len(rel)


def ndcg_at_k(ranked: Sequence[str], relevant: Sequence[str] | set[str], k: int) -> float:
    """二值相关性的 nDCG@k（gain = 1 命中 / 0 未命中，log2 折损）。

    相关集为空时返回 ``0.0``（同样由调用方的 skip 判据先拦住）。
    """
    if k <= 0:
        raise ValueError("k must be positive")
    rel = set(relevant)
    if not rel:
        return 0.0
    top = list(ranked)[:k]
    dcg = 0.0
    for index, cid in enumerate(top, start=1):
        if cid in rel:
            dcg += 1.0 / math.log2(index + 1)
    ideal_hits = min(len(rel), k)
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    if idcg <= 0:
        return 0.0
    return min(dcg / idcg, 1.0)


def evaluate(
    ranked_chunk_ids: Sequence[str],
    relevant_chunk_ids: Sequence[str] | set[str] | None,
    *,
    k: int | None = None,
) -> RetrievalQuality:
    """算三指标（**有 ground truth 才算**）。纯函数（守卫钉住）。

    与"没测到"的区别：
    * ``relevant_chunk_ids`` 为 None（或空）→ ``skip_reason=no_ground_truth``，
      三个指标都是 ``None``；
    * 检索结果为空 → ``skip_reason=no_results``；
    * ground truth 一条都不在结果里（且相关集非空）→ 指标**算得出来**（会是 0），
      但同时把 ``skip_reason`` 之外的诊断信息交给调用方 —— 见 ``notes`` 段注释。
      这里**不**把它当"没测到"：召回率 0 是**真实测量结果**，必须如实报。
    """
    ranked = [str(cid) for cid in ranked_chunk_ids if str(cid or "").strip()]
    top_k = k if k is not None else len(ranked)

    if not relevant_chunk_ids:
        return RetrievalQuality(
            precision=None,
            recall=None,
            ndcg=None,
            ranked_ids=tuple(ranked),
            relevant_ids=frozenset(),
            skip_reason=REASON_NO_GROUND_TRUTH,
        )
    if not ranked:
        return RetrievalQuality(
            precision=None,
            recall=None,
            ndcg=None,
            ranked_ids=(),
            relevant_ids=frozenset(str(c) for c in relevant_chunk_ids),
            skip_reason=REASON_NO_RESULTS,
        )

    relevant = frozenset(str(c) for c in relevant_chunk_ids if str(c or "").strip())
    return RetrievalQuality(
        precision=precision_at_k(ranked, relevant, top_k),
        recall=recall_at_k(ranked, relevant, top_k),
        ndcg=ndcg_at_k(ranked, relevant, top_k),
        ranked_ids=tuple(ranked),
        relevant_ids=relevant,
        skip_reason="",
    )


def _collector():
    """取指标收集器（导入失败不影响调用方 —— 观测不许阻断检索/评测）。"""
    from app.observability.metrics_collector import get_metrics_collector

    return get_metrics_collector()


def publish(quality: RetrievalQuality) -> dict[str, float | None]:
    """把评估结果写进 Prometheus（**没测到就写 not_measured，不写 0**）。

    返回本次实际写入的 ``{metric: value|None}``，便于测试与探针读取。

    失败只记 warning（观测链路故障不许影响评测/检索）。
    """
    values = quality.as_dict()
    try:
        collector = _collector()
        if quality.skip_reason:
            for metric in ALL_METRICS:
                collector.record_retrieval_quality_not_measured(
                    metric, quality.skip_reason
                )
            logger.info(
                "retrieval_quality_not_measured",
                reason=quality.skip_reason,
                ranked=len(quality.ranked_ids),
                relevant=len(quality.relevant_ids),
            )
            return values
        for metric, value in values.items():
            if value is None:
                collector.record_retrieval_quality_not_measured(
                    metric, REASON_NO_GROUND_TRUTH
                )
                continue
            collector.set_retrieval_quality(metric, float(value))
            collector.record_retrieval_quality_measured(metric)
        logger.info(
            "retrieval_quality_measured",
            precision=round(values[METRIC_PRECISION], 6) if values[METRIC_PRECISION] is not None else None,
            recall=round(values[METRIC_RECALL], 6) if values[METRIC_RECALL] is not None else None,
            ndcg=round(values[METRIC_NDCG], 6) if values[METRIC_NDCG] is not None else None,
            k=len(quality.ranked_ids),
            relevant=len(quality.relevant_ids),
        )
    except Exception as exc:
        logger.warning(
            "retrieval_quality_publish_failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return values


def evaluate_and_publish(
    ranked_chunk_ids: Sequence[str],
    relevant_chunk_ids: Sequence[str] | set[str] | None,
    *,
    k: int | None = None,
) -> dict[str, float | None]:
    """评估 + 写入一步到位（供评测脚本与离线回放调用）。"""
    return publish(evaluate(ranked_chunk_ids, relevant_chunk_ids, k=k))


def report_unavailable(reason: str = REASON_NO_GROUND_TRUTH) -> None:
    """生产在线路径调用：说明"这次检索无法计算质量指标"及其**具体原因**。

    为什么要有它：生产检索没有 ground truth，但"没测"本身必须被记录 ——
    否则看板上三个指标会是**从来没出现过**（连 label 都不存在），运维无法区分
    "没数据"与"指标没接线"。
    """
    quality = RetrievalQuality(
        precision=None,
        recall=None,
        ndcg=None,
        ranked_ids=(),
        relevant_ids=frozenset(),
        skip_reason=reason,
    )
    publish(quality)


__all__ = [
    "ALL_METRICS",
    "METRIC_NDCG",
    "METRIC_PRECISION",
    "METRIC_RECALL",
    "REASON_GT_NOT_IN_RESULT",
    "REASON_NO_GROUND_TRUTH",
    "REASON_NO_RESULTS",
    "RetrievalQuality",
    "evaluate",
    "evaluate_and_publish",
    "ndcg_at_k",
    "precision_at_k",
    "publish",
    "recall_at_k",
    "report_unavailable",
]
