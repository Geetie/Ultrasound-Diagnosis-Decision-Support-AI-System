"""EVIDENCE_CONFLICT 三个触发条件与辅助函数的分支覆盖（P0-3 实现后补测）。"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_fus]

from app.fusion.conflict import (
    ConflictDetector,
    _extract_grade,
    _extract_negated_objects,
    _recommendation_groups_in,
)
from app.fusion.protocols import ConflictType
from app.models.schemas import Organ
from tests.unit.fus.conftest import make_response

# 与 ConflictDetector._RECOMMENDATION_GROUPS 对齐的触发词（活检/手术/随访/紧急）


def test_evidence_conflict_condition1_mutually_exclusive_recommendations():
    """共享 chunk + 互斥建议组 → EVIDENCE_CONFLICT。"""
    r1 = make_response(
        agent_name="a", organ=Organ.THYROID,
        content="建议穿刺活检", evidence=["c1", "c2"],
    )
    r2 = make_response(
        agent_name="b", organ=Organ.THYROID,
        content="建议随访观察", evidence=["c1"],
    )
    report = ConflictDetector().detect([r1, r2])
    assert ConflictType.EVIDENCE_CONFLICT in report.conflict_types


def test_evidence_conflict_condition2_same_organ_different_grades():
    """共享 chunk + 同器官不同分级 → EVIDENCE_CONFLICT。"""
    r1 = make_response(
        agent_name="a", organ=Organ.THYROID,
        content="TR3 结节", evidence=["c9"],
    )
    r2 = make_response(
        agent_name="b", organ=Organ.THYROID,
        content="考虑 TR4", evidence=["c9"],
    )
    report = ConflictDetector().detect([r1, r2])
    assert ConflictType.EVIDENCE_CONFLICT in report.conflict_types


def test_evidence_conflict_condition3_contradicting_negations():
    """共享 chunk + 否定对象不同且含恶性/良性词 → EVIDENCE_CONFLICT。

    注意 \\S+ 贪婪：否定词必须以标点/结尾终止才能抽到精确 token。
    """
    r1 = make_response(
        agent_name="a", organ=Organ.LIVER,
        content="排除恶性", evidence=["c7"],
    )
    r2 = make_response(
        agent_name="b", organ=Organ.LIVER,
        content="排除良性", evidence=["c7"],
    )
    report = ConflictDetector().detect([r1, r2])
    assert ConflictType.EVIDENCE_CONFLICT in report.conflict_types


def test_evidence_conflict_not_triggered_same_negated_object():
    """共享 chunk + 否定同一对象（一致排除）→ 不触发。"""
    r1 = make_response(
        agent_name="a", organ=Organ.LIVER,
        content="排除恶性可能", evidence=["c7"],
    )
    r2 = make_response(
        agent_name="b", organ=Organ.LIVER,
        content="排除恶性转移", evidence=["c7"],
    )
    report = ConflictDetector().detect([r1, r2])
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


def test_evidence_conflict_no_shared_evidence():
    """无共享 chunk → 不触发（保守策略）。"""
    r1 = make_response(
        agent_name="a", organ=Organ.THYROID, content="建议穿刺活检", evidence=["c1"],
    )
    r2 = make_response(
        agent_name="b", organ=Organ.THYROID, content="建议随访观察", evidence=["c2"],
    )
    report = ConflictDetector().detect([r1, r2])
    assert ConflictType.EVIDENCE_CONFLICT not in report.conflict_types


# ----------------------------------------------------------------------
# 辅助函数
# ----------------------------------------------------------------------


def test_extract_grade_unknown_organ_and_failures():
    d = ConflictDetector()
    r = make_response(agent_name="a", organ=Organ.THYROID, content="TR3")
    assert _extract_grade(r, "brain", d._grading_extractors) is None
    assert _extract_grade(r, "thyroid", d._grading_extractors) == "TR3"
    no_match = make_response(agent_name="b", organ=Organ.THYROID, content="无分级")
    assert _extract_grade(no_match, "thyroid", d._grading_extractors) is None


def test_extract_grade_bad_format_returns_none():
    """fmt.format 抛异常 → None（497-498）。"""
    d = ConflictDetector()
    r = make_response(agent_name="a", organ=Organ.THYROID, content="TR3")
    # fmt 含非法占位符即可让 format 炸
    broken = {**d._grading_extractors}
    broken["thyroid"] = (broken["thyroid"][0], "{nonexistent_field}")
    assert _extract_grade(r, "thyroid", broken) is None


def test_recommendation_groups_in():
    groups = [("穿刺", "活检"), ("随访", "观察")]
    r = make_response(agent_name="a", organ=Organ.THYROID, content="建议穿刺活检")
    assert _recommendation_groups_in(r, groups) == {0}
    empty = make_response(agent_name="b", organ=Organ.THYROID, content="")
    assert _recommendation_groups_in(empty, groups) == set()


def test_extract_negated_objects_patterns():
    # \S+ 贪婪：吞到非空白边界
    assert _extract_negated_objects("排除恶性可能") == {"恶性可能"}
    assert _extract_negated_objects("排除恶性") == {"恶性"}
    assert "malignant" in _extract_negated_objects("This rules out malignant tumor")
    assert _extract_negated_objects("") == set()
    # 超长 token（>20 字符）被丢弃
    long_tok = "x" * 25
    assert _extract_negated_objects(f"排除{long_tok}") == set()
