"""M7 处置建议知识表单测。"""
import pytest

from app.knowledge.clinical_actions import (
    LEVEL_GENERAL,
    LEVEL_TERTIARY,
    MAX_ACTIONS,
    actions_for,
    summarize_actions,
)
from app.models.schemas import ClinicalActionUrgency

pytestmark = [pytest.mark.track_rag, pytest.mark.track_data]


class TestThyroidActions:
    def test_high_risk_recommends_fna_at_tertiary(self):
        actions = actions_for("thyroid", ["micro"], "high")
        fna = [item for item in actions if item.action_type == "fine_needle_aspiration"]
        assert fna
        assert fna[0].target_department == "甲状腺外科"
        assert fna[0].urgency == ClinicalActionUrgency.SOON
        # 基层医院做不了穿刺，必须标注适用层级
        assert fna[0].applicable_level == LEVEL_TERTIARY

    def test_microcalcification_adds_specific_action(self):
        actions = actions_for("thyroid", ["micro"], "high")
        assert any(item.action_type == "microcalcification_review" for item in actions)

    def test_medium_risk_recommends_short_interval_followup(self):
        actions = actions_for("thyroid", [], "medium")
        assert any(item.action_type == "short_interval_followup" for item in actions)

    def test_low_risk_recommends_annual_followup(self):
        actions = actions_for("thyroid", [], "low")
        assert any(item.action_type == "annual_followup" for item in actions)

    def test_always_includes_function_test(self):
        for level in ("low", "medium", "high", None):
            actions = actions_for("thyroid", [], level)
            assert any(item.action_type == "thyroid_function_test" for item in actions)

    def test_every_action_has_source_reference(self):
        for level in ("low", "medium", "high"):
            for item in actions_for("thyroid", ["micro"], level):
                assert item.source_reference, item.action_type


class TestLiverAndCardiacActions:
    def test_liver_high_risk_recommends_contrast_imaging(self):
        actions = actions_for("liver", [], "high")
        contrast = [item for item in actions if item.action_type == "contrast_imaging"]
        assert contrast
        assert contrast[0].urgency == ClinicalActionUrgency.URGENT

    def test_cardiac_reduced_function_recommends_cardiology(self):
        actions = actions_for("cardiac", ["ef_moderate_reduced"], None)
        referral = [item for item in actions if item.action_type == "cardiology_referral"]
        assert referral
        assert referral[0].target_department == "心内科"
        assert referral[0].urgency == ClinicalActionUrgency.URGENT

    def test_cardiac_mild_reduction_is_not_urgent(self):
        actions = actions_for("cardiac", ["ef_mild_reduced"], None)
        referral = [item for item in actions if item.action_type == "cardiology_referral"]
        assert referral[0].urgency == ClinicalActionUrgency.SOON

    def test_cardiac_normal_function_routine_followup(self):
        actions = actions_for("cardiac", ["ef_normal"], None)
        assert any(item.action_type == "routine_followup" for item in actions)

    def test_cardiac_ignores_risk_level(self):
        """心脏超声不产出恶性概率，风险等级不应改变处置建议。"""
        assert actions_for("cardiac", ["ef_normal"], "high") == actions_for(
            "cardiac", ["ef_normal"], None
        )


class TestActionLimits:
    def test_unknown_organ_returns_empty(self):
        assert actions_for("unknown_organ", [], "high") == []

    def test_empty_organ_returns_empty(self):
        assert actions_for(None, [], "high") == []
        assert actions_for("", [], "high") == []

    def test_capped_at_max_actions(self):
        actions = actions_for("thyroid", ["micro"], "high")
        assert len(actions) <= MAX_ACTIONS

    def test_general_level_is_not_tertiary(self):
        actions = actions_for("thyroid", [], "medium")
        assert any(item.applicable_level == LEVEL_GENERAL for item in actions)


class TestSummary:
    def test_summary_contains_action_and_department(self):
        text = summarize_actions(actions_for("thyroid", ["micro"], "high"))
        assert "fine_needle_aspiration" in text
        assert "甲状腺外科" in text

    def test_summary_placeholder_when_empty(self):
        assert "未生成结构化处置建议" in summarize_actions([])
