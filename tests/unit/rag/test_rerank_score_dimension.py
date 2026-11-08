"""A.168：rerank 分是**原始 logits**，不是 0~1 概率（真机实测确认）。

## 缺陷

`scripts/start_embedding_services.py::score()` 返回
`self.model(**batch).logits.view(-1)`，**无 sigmoid**。
但 `app/rag/service.py:47` 原注释写"cross-encoder，0~1"，
`min_score_threshold` 默认 0.5 按**绝对**判据卡在这个区间上。
测试夹具一直用 0.95/0.85 这类概率值 → **单测全绿、生产行为测不出**。

## 真机实测（bge-reranker-base，2026-11-08）

| query | document | 原始 logit | sigmoid |
|---|---|---|---|
| TI-RADS 4A 结节恶性风险与穿刺建议 | TI-RADS 4A 类恶性风险约 10%~50%，建议细针穿刺活检 | **+7.4339** | 0.9994 |
| 同上 | 甲状腺结节 TI-RADS 分级 | **+2.7113** | 0.9377 |
| 同上 | [影像参考] img_001；模态=B-mode… | **-6.3877** | 0.0017 |
| 同上 | 肝脏超声造影指南 | **-9.5951** | 0.0001 |
| 同上 | 患者应定期复查甲状腺功能 | **-4.1543** | 0.0155 |

范围 **-9.5951 ~ +7.4339**，3/5 为负分。
"""

from __future__ import annotations

import pytest

from app.rag.service import _rerank_floor_for_semantics

pytestmark = pytest.mark.track_rag


# 真机实测值（见模块 docstring 表格）
REAL_POSITIVE = 7.4339
REAL_NEGATIVE = -6.3877


def test_real_scores_fall_outside_probability_range():
    """钉住量纲事实：真实分数会超出 [0,1]，所以"0~1"这个描述是错的。"""
    assert REAL_NEGATIVE < 0.0
    assert REAL_POSITIVE > 1.0


def test_logit_semantics_passes_threshold_through_unchanged():
    """默认 logit 语义：原样返回，保证行为与修复前可复现。"""
    assert _rerank_floor_for_semantics(0.5, "logit") == 0.5


def test_probability_semantics_translates_half_to_zero():
    """probability 语义：0.5 → ln(1) = 0.0（对应"0 是对数几率分界"）。"""
    assert _rerank_floor_for_semantics(0.5, "probability") == pytest.approx(0.0)


def test_probability_semantics_equivalent_point():
    """原绝对判据 0.5（logit）等价于概率 sigmoid(0.5)=0.6225。"""
    import math

    p = math.exp(0.5) / (1 + math.exp(0.5))
    assert _rerank_floor_for_semantics(p, "probability") == pytest.approx(0.5, abs=1e-6)


def test_zero_or_negative_threshold_means_no_floor():
    """阈值 ≤0 = 不设下限。"""
    for t in (0.0, -1.0):
        assert _rerank_floor_for_semantics(t, "logit") == float("-inf")
        assert _rerank_floor_for_semantics(t, "probability") == float("-inf")


def test_probability_one_is_infinite():
    assert _rerank_floor_for_semantics(1.0, "probability") == float("inf")


def test_probability_semantics_keeps_weak_evidence_that_logit_drops():
    """核心行为差异：弱相关证据（logit 0~0.5）在 logit 语义下被丢，在 probability 下保留。

    这正是"临床最该保留的边缘证据"那一档。
    """
    weak = 0.3  # logit 0.3 → 概率 0.574，属弱相关

    logit_floor = _rerank_floor_for_semantics(0.5, "logit")
    prob_floor = _rerank_floor_for_semantics(0.5, "probability")

    assert weak < logit_floor, "logit 语义下 0.3 < 0.5，会被丢弃"
    assert weak >= prob_floor, "probability 语义下 0.3 >= 0.0，会被保留"


def test_negative_scores_are_dropped_under_both_semantics():
    """真正不相关的（负分）在两种语义下都该丢。"""
    for semantics in ("logit", "probability"):
        floor = _rerank_floor_for_semantics(0.5, semantics)
        assert REAL_NEGATIVE < floor
