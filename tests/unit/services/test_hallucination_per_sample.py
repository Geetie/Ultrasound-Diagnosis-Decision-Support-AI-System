"""D-03 语义隔离：双采样分歧属 conflict，不属 hallucination。

真机实测（2026-11-07）：双采样两份给出不同分级（4 / 5），聚合后拼接文本喂给
`contradictory_grading` 策略 → 判 severe 幻觉 → 幻觉率被抬到 10%（设计阈值 ≤1%）。
但跨份分歧正是 G21 要抓的信号，应由 ConflictDetector 判 grading_conflict。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.inference.hallucination_detector import get_default_detector


def test_single_sample_contradiction_still_detected():
    """单份输出内部自相矛盾 → 仍是幻觉（检测器原语义不变）。"""
    det = get_default_detector()
    r = det.detect("甲状腺 TR4，另外该结节为 TR5，结论 TR3。", "thyroid")
    strategies = {i.strategy.value for i in (r.issues or [])}
    assert "contradictory_grading" in strategies


def test_two_clean_samples_have_no_contradiction():
    """两份各自干净、只是分级不同 → 逐份检测时都不应报矛盾。"""
    det = get_default_detector()
    a = det.detect("甲状腺 · TR4 — 边界清，形态规则。", "thyroid")
    b = det.detect("甲状腺 · TR5 — 边界不清，形态不规则。", "thyroid")
    for r in (a, b):
        strategies = {i.strategy.value for i in (r.issues or [])}
        assert "contradictory_grading" not in strategies


def test_concatenated_text_would_false_positive():
    """钉住缺陷形态：如果直接拿拼接文本检测，两份不同分级会被误判。"""
    det = get_default_detector()
    joined = "甲状腺 · TR4 — 边界清。\n\n甲状腺 · TR5 — 边界不清。"
    r = det.detect(joined, "thyroid")
    strategies = {i.strategy.value for i in (r.issues or [])}
    assert "contradictory_grading" in strategies, (
        "本用例固化'拼接会误判'这一事实，说明 pipeline 必须逐份检测"
    )
