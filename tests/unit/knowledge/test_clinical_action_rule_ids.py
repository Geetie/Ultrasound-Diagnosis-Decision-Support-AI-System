"""A.127 / G31：处置建议必须有可追溯的规则条目 ID。

缺口：`ClinicalAction` 只有一句书名级 `source_reference`，报告与前端都不渲染出处；
"这条建议来自规则表哪一条"无法回答（G31）。这里给确定性 ID：`<organ>:<action_type>`。
"""

from __future__ import annotations

import pytest

from app.knowledge.clinical_actions import actions_for, summarize_actions

pytestmark = pytest.mark.track_rag


def test_every_action_gets_deterministic_rule_id():
    for organ, risk in (("thyroid", "low"), ("liver", "high"), ("cardiac", "low")):
        for action in actions_for(organ, [], risk):
            assert action.rule_id, f"{organ} 有建议缺 rule_id"
            assert action.rule_id == f"{organ}:{action.action_type}"


def test_rule_id_is_stable_and_unique_per_action():
    acts = actions_for("thyroid", [], "high")
    ids = [a.rule_id for a in acts]
    assert len(ids) == len(set(ids)), f"同一批建议里 rule_id 重复：{ids}"


def test_unknown_organ_returns_empty_without_ids():
    assert actions_for("not_an_organ", [], None) == []


def test_summary_includes_rule_id():
    text = summarize_actions(actions_for("thyroid", [], "low"))
    assert "规则：thyroid:" in text


class TestFollowupIntervalsAreConstants:
    """A.134 / G40：随访间隔集中成常量，不再散落成描述里的裸数字。

    这样"本院要改间隔"只改一处（或走 A.129 的外部规则文件），且描述文案由常量拼出，
    不会出现"改了一个动作、漏了另一个"的漂移。`source_reference` 仍标"（定性）"是
    诚实的：机构默认值不等于指南逐字规定。
    """

    def test_thyroid_descriptions_use_constants(self):
        from app.knowledge import clinical_actions as ca

        medium = ca.actions_for("thyroid", [], "medium")[0]
        low = [a for a in ca.actions_for("thyroid", [], "low") if a.action_type == "annual_followup"][0]
        assert ca.FOLLOWUP_MEDIUM_MONTHS_RANGE in medium.description
        assert ca.FOLLOWUP_LOW_MONTHS in low.description

    def test_liver_descriptions_use_constants(self):
        from app.knowledge import clinical_actions as ca

        medium = ca.actions_for("liver", [], "medium")[0]
        low = [a for a in ca.actions_for("liver", [], "low") if a.action_type == "six_month_followup"][0]
        assert ca.LIVER_FOLLOWUP_MEDIUM_MONTHS in medium.description
        assert ca.LIVER_FOLLOWUP_LOW_MONTHS in low.description
