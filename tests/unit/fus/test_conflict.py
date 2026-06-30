"""Unit tests for app.fusion.conflict (T-GA-FUS-01 冲突检测).

覆盖 4 类冲突各 5 个用例（共 20 个用例），并验证空输入 / 单响应 / 复合
冲突场景。验收要求：冲突检测准确率 100%。

测试维度：
- GRADING_CONFLICT：同器官不同分级（thyroid TR3 vs TR4 / liver LR-3 vs
  LR-5 / cardiac LVEF 40% vs 25%）。
- CONFIDENCE_CONFLICT：多个响应 confidence 差距 > 0.4。
- RECOMMENDATION_CONFLICT：活检 vs 随访 / 立即手术 vs 观察。
- EVIDENCE_CONFLICT：占位（Phase 0A 简化；实际不触发，验证 false-positive
  不影响）。

边界：
- 空输入 / 单响应 → 无冲突；
- 多类复合冲突 → resolution_suggestion 选择正确。
"""
from __future__ import annotations

from typing import List

import pytest

pytestmark = [pytest.mark.track_fus]

from app.agents.protocols import AgentResponse
from app.fusion.conflict import ConflictDetector
from app.fusion.protocols import ConflictResolution, ConflictType
from app.models.schemas import Organ
from tests.unit.fus.conftest import make_response


# ======================================================================
# GRADING_CONFLICT — 5 用例
# ======================================================================


def test_grading_conflict_thyroid_tr3_vs_tr4(conflict_detector):
    """甲状腺同器官 TR3 vs TR4 → GRADING_CONFLICT。"""
    responses = [
        make_response(agent_name="thy_a", organ=Organ.THYROID, content="TI-RADS 3"),
        make_response(agent_name="thy_b", organ=Organ.THYROID, content="TR4"),
    ]
    report = conflict_detector.detect(responses)
    assert report.has_conflict is True
    assert ConflictType.GRADING_CONFLICT in report.conflict_types
    assert len(report.conflicting_responses) == 2


def test_grading_conflict_liver_lr3_vs_lr5(conflict_detector):
    """肝脏同器官 LR-3 vs LR-5 → GRADING_CONFLICT。"""
    responses = [
        make_response(agent_name="liv_a", organ=Organ.LIVER, content="LI-RADS 3"),
        make_response(agent_name="liv_b", organ=Organ.LIVER, content="LR-5"),
    ]
    report = conflict_detector.detect(responses)
    assert report.has_conflict is True
    assert ConflictType.GRADING_CONFLICT in report.conflict_types


def test_grading_conflict_cardiac_lvef_40_vs_25(conflict_detector):
    """心脏 LVEF 40% vs 25% → GRADING_CONFLICT。"""
    responses = [
        make_response(agent_name="car_a", organ=Organ.CARDIAC, content="LVEF 40%"),
        make_response(agent_name="car_b", organ=Organ.CARDIAC, content="LVEF: 25%"),
    ]
    report = conflict_detector.detect(responses)
    assert report.has_conflict is True
    assert ConflictType.GRADING_CONFLICT in report.conflict_types


def test_grading_no_conflict_same_grade(conflict_detector):
    """同器官相同分级 → 不触发 GRADING_CONFLICT。"""
    responses = [
        make_response(agent_name="thy_a", organ=Organ.THYROID, content="TR3"),
        make_response(agent_name="thy_b", organ=Organ.THYROID, content="TR3"),
    ]
    report = conflict_detector.detect(responses)
    # confidence 一致（默认 0.85），不触发 confidence conflict；
    # grade 一致，不触发 grading conflict；无 recommendation 关键词
    # → 无冲突
    assert ConflictType.GRADING_CONFLICT not in report.conflict_types


def test_grading_conflict_different_organs_not_conflict(conflict_detector):
    """不同器官不同分级（thyroid TR3 vs liver LR-5）→ 不视为器官内冲突。"""
    responses = [
        make_response(agent_name="thy_a", organ=Organ.THYROID, content="TR3"),
        make_response(agent_name="liv_a", organ=Organ.LIVER, content="LR-5"),
    ]
    report = conflict_detector.detect(responses)
    # 跨器官不属于 GRADING_CONFLICT（grading conflict 是器官内）
    assert ConflictType.GRADING_CONFLICT not in report.conflict_types


# ======================================================================
# CONFIDENCE_CONFLICT — 5 用例
# ======================================================================


def test_confidence_conflict_gap_exceeds_threshold(conflict_detector):
    """confidence 差距 0.9 - 0.3 = 0.6 > 0.4 → CONFIDENCE_CONFLICT。"""
    responses = [
        make_response(agent_name="a", organ=Organ.THYROID, content="TR3", confidence=0.9),
        make_response(agent_name="b", organ=Organ.THYROID, content="TR4", confidence=0.3),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.CONFIDENCE_CONFLICT in report.conflict_types
    assert ConflictType.GRADING_CONFLICT in report.conflict_types


def test_confidence_no_conflict_below_threshold(conflict_detector):
    """confidence 差距 0.9 - 0.6 = 0.3 < 0.4 → 不触发。"""
    responses = [
        make_response(agent_name="a", organ=Organ.THYROID, content="TR3", confidence=0.9),
        make_response(agent_name="b", organ=Organ.THYROID, content="TR3", confidence=0.6),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.CONFIDENCE_CONFLICT not in report.conflict_types


def test_confidence_conflict_with_custom_threshold(conflict_detector):
    """自定义阈值 0.2 → 差距 0.3 > 0.2 → 触发。"""
    custom = ConflictDetector(confidence_gap_threshold=0.2)
    responses = [
        make_response(agent_name="a", content="TR3", confidence=0.8),
        make_response(agent_name="b", content="TR3", confidence=0.5),
    ]
    report = custom.detect(responses)
    assert ConflictType.CONFIDENCE_CONFLICT in report.conflict_types


def test_confidence_conflict_single_response_no_trigger(conflict_detector):
    """单响应 → confidence gap 不适用。"""
    responses = [make_response(agent_name="a", content="TR3", confidence=0.9)]
    report = conflict_detector.detect(responses)
    assert ConflictType.CONFIDENCE_CONFLICT not in report.conflict_types


def test_confidence_conflict_threshold_validation():
    """阈值必须在 [0.0, 1.0] 范围内。"""
    with pytest.raises(ValueError):
        ConflictDetector(confidence_gap_threshold=1.5)
    with pytest.raises(ValueError):
        ConflictDetector(confidence_gap_threshold=-0.1)


# ======================================================================
# RECOMMENDATION_CONFLICT — 5 用例
# ======================================================================


def test_recommendation_conflict_biopsy_vs_followup(conflict_detector):
    """活检 vs 随访 → RECOMMENDATION_CONFLICT。"""
    responses = [
        make_response(agent_name="a", content="建议立即活检"),
        make_response(agent_name="b", content="建议随访观察"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.RECOMMENDATION_CONFLICT in report.conflict_types
    # 涉及决策 → 人工复核
    assert report.resolution_suggestion == ConflictResolution.REQUIRE_HUMAN_REVIEW


def test_recommendation_conflict_surgery_vs_followup(conflict_detector):
    """立即手术 vs 观察 → RECOMMENDATION_CONFLICT。"""
    responses = [
        make_response(agent_name="a", content="需要立即手术"),
        make_response(agent_name="b", content="建议观察"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.RECOMMENDATION_CONFLICT in report.conflict_types


def test_recommendation_conflict_urgent_vs_followup(conflict_detector):
    """urgent vs 观察 → RECOMMENDATION_CONFLICT。"""
    responses = [
        make_response(agent_name="a", content="urgent treatment required"),
        make_response(agent_name="b", content="followup in 3 months"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.RECOMMENDATION_CONFLICT in report.conflict_types


def test_recommendation_no_conflict_same_group(conflict_detector):
    """同一处置组（活检）→ 不触发。"""
    responses = [
        make_response(agent_name="a", content="建议活检"),
        make_response(agent_name="b", content="推荐 biopsy"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.RECOMMENDATION_CONFLICT not in report.conflict_types


def test_recommendation_conflict_only_2_responses(conflict_detector):
    """仅 2 条响应 + 处置冲突 → 触发。"""
    responses = [
        make_response(agent_name="a", content="活检"),
        make_response(agent_name="b", content="随访"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.RECOMMENDATION_CONFLICT in report.conflict_types


# ======================================================================
# EVIDENCE_CONFLICT — 5 用例
# ======================================================================


def test_evidence_conflict_no_trigger_phase0a(conflict_detector):
    """Phase 0A 占位：共享否定对象反而是"一致排除"，不视为冲突。"""
    responses = [
        make_response(agent_name="a", content="排除恶性肿瘤"),
        make_response(agent_name="b", content="排除恶性可能"),
    ]
    report = conflict_detector.detect(responses)
    # Phase 0A 简化策略：共同否定对象 → 一致排除，不算冲突
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


def test_evidence_conflict_no_negation_keywords(conflict_detector):
    """响应中无否定关键词 → 不触发。"""
    responses = [
        make_response(agent_name="a", content="TR3 边界清晰"),
        make_response(agent_name="b", content="TI-RADS 3 良性特征"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


def test_evidence_conflict_single_response_no_trigger(conflict_detector):
    """单响应无法构成 evidence conflict。"""
    responses = [make_response(agent_name="a", content="排除恶性肿瘤")]
    report = conflict_detector.detect(responses)
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


def test_evidence_conflict_empty_content(conflict_detector):
    """空 content → 不触发 evidence conflict。"""
    responses = [
        make_response(agent_name="a", content=""),
        make_response(agent_name="b", content=""),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


def test_evidence_conflict_different_negation_objects(conflict_detector):
    """不同否定对象 → 不视为冲突（Phase 0A 保守策略）。"""
    responses = [
        make_response(agent_name="a", content="排除恶性肿瘤"),
        make_response(agent_name="b", content="排除良性结节"),
    ]
    report = conflict_detector.detect(responses)
    # 共同否定对象为空 → 简化策略不视为冲突
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


# ======================================================================
# 边界 / 复合
# ======================================================================


def test_empty_input_no_conflict(conflict_detector):
    """空输入 → 无冲突。"""
    report = conflict_detector.detect([])
    assert report.has_conflict is False
    assert report.conflict_types == []
    assert "no responses" in report.detail


def test_multiple_conflict_types_resolution(conflict_detector):
    """复合冲突（grading + recommendation）→ REQUIRE_HUMAN_REVIEW。"""
    responses = [
        make_response(agent_name="a", organ=Organ.THYROID, content="TR3 建议活检"),
        make_response(agent_name="b", organ=Organ.THYROID, content="TR4 建议随访"),
    ]
    report = conflict_detector.detect(responses)
    assert ConflictType.GRADING_CONFLICT in report.conflict_types
    assert ConflictType.RECOMMENDATION_CONFLICT in report.conflict_types
    assert report.resolution_suggestion == ConflictResolution.REQUIRE_HUMAN_REVIEW


def test_conflict_report_field_consistency(conflict_detector):
    """ConflictReport 字段一致性：conflict_types 非空 → has_conflict=True。"""
    responses = [
        make_response(agent_name="a", organ=Organ.THYROID, content="TR3"),
        make_response(agent_name="b", organ=Organ.THYROID, content="TR4"),
    ]
    report = conflict_detector.detect(responses)
    # 验证 Pydantic 字段一致性
    assert report.has_conflict == bool(report.conflict_types)


def test_conflicting_responses_deduplicated(conflict_detector):
    """conflicting_responses 按 agent_name 去重。"""
    responses = [
        make_response(agent_name="a", organ=Organ.THYROID, content="TR3"),
        make_response(agent_name="a", organ=Organ.THYROID, content="TR3"),  # duplicate
        make_response(agent_name="b", organ=Organ.THYROID, content="TR4"),
    ]
    report = conflict_detector.detect(responses)
    agent_names = [r.agent_name for r in report.conflicting_responses]
    assert len(agent_names) == len(set(agent_names))  # 去重
