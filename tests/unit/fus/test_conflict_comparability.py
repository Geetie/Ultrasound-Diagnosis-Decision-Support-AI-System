"""A.135 / G21：单器官冲突检测"恒 False"的诚实修法——标注**不可比**。

缺口：单器官判读只派发一个 specialist 响应，四类检测全部 `len(responses) < 2` 直接返回 []，
于是 `has_conflict=False` 被读成"检查过且没冲突"，实际是"没得比"（G21）。

本组守卫：
1) 单条响应 / 空响应 → `comparable=False` + 原因；
2) 两条响应 → `comparable=True`（真的比过，无冲突时才允许说"无冲突"）；
3) 管线序列化把这两个信号写进 findings（None 不写，不造"比过"的假信号）；
4) 结果契约暴露给前端（None = 未知，不猜）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.fusion.conflict import ConflictDetector
from app.models.schemas import Organ
from app.agents.protocols import AgentResponse

pytestmark = pytest.mark.track_fus


def _resp(name="t1", confidence=0.8):
    return AgentResponse(agent_name=name, organ=Organ.THYROID, content="TI-RADS 3", confidence=confidence)


def test_empty_responses_not_comparable():
    report = ConflictDetector().detect([])
    assert report.has_conflict is False
    assert report.comparable is False
    assert report.skipped_reason == "no_responses"


def test_single_response_not_comparable():
    report = ConflictDetector().detect([_resp()])
    assert report.has_conflict is False, "单响应不应报冲突"
    assert report.comparable is False, "单响应不可比 —— 不得让下游读成'无冲突'"
    assert report.skipped_reason == "single_response"


def test_two_responses_are_comparable():
    report = ConflictDetector().detect([_resp("a", 0.9), _resp("b", 0.9)])
    assert report.comparable is True
    assert report.skipped_reason is None
    assert report.has_conflict is False


def test_two_responses_confidence_conflict():
    report = ConflictDetector().detect([_resp("a", 0.95), _resp("b", 0.2)])
    assert report.comparable is True and report.has_conflict is True


def test_pipeline_serialises_signals():
    from app.services.diagnosis_pipeline import _fusion_result_to_dict

    payload = _fusion_result_to_dict(
        SimpleNamespace(conflict_report=ConflictDetector().detect([_resp()]))
    )
    assert payload["conflict_comparable"] is False
    assert payload["conflict_skipped_reason"] == "single_response"


def test_pipeline_omits_signal_when_unknown():
    """没有 conflict_report → 不写这两个键（未知就是未知，不猜）。"""
    from app.services.diagnosis_pipeline import _fusion_result_to_dict

    payload = _fusion_result_to_dict(SimpleNamespace(conflict_report=None))
    assert "conflict_comparable" not in payload
    assert "conflict_skipped_reason" not in payload
