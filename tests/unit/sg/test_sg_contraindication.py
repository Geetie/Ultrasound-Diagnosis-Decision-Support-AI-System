import pytest

from safety_gateway.contraindication import (
    ContraindicationEngine,
    ContraindicationRule,
    ContraindicationMatch,
    get_contraindication_engine,
)

pytestmark = [pytest.mark.track_sg, pytest.mark.track_sec, pytest.mark.sec_domain_sg]


class TestContraindicationEngine:

    def setup_method(self):
        self.engine = ContraindicationEngine()

    def test_default_rules_loaded(self):
        self.engine._load_default_rules()
        assert self.engine._loaded is True
        assert len(self.engine._organ_rules) > 0
        assert len(self.engine._global_rules) > 0

    def test_thyroid_contraindication_hit(self):
        self.engine._load_default_rules()
        matches = self.engine.check("thyroid", {"patient_conditions": ["pregnancy"]})
        assert len(matches) > 0
        assert any(m.organ == "thyroid" for m in matches)

    def test_thyroid_contraindication_no_hit(self):
        self.engine._load_default_rules()
        matches = self.engine.check("thyroid", {"patient_conditions": []})
        thyroid_matches = [m for m in matches if m.organ == "thyroid"]
        assert len(thyroid_matches) == 0

    def test_thyroid_contraindication_missing_conditions_conservative(self):
        self.engine._load_default_rules()
        matches = self.engine.check("thyroid", {})
        thyroid_matches = [m for m in matches if m.organ == "thyroid"]
        assert len(thyroid_matches) > 0

    def test_liver_contraindication_hit(self):
        self.engine._load_default_rules()
        matches = self.engine.check("liver", {"patient_conditions": ["coagulopathy"]})
        assert len(matches) > 0

    def test_cardiac_contraindication_hit(self):
        self.engine._load_default_rules()
        matches = self.engine.check("cardiac", {"patient_conditions": ["severe_aortic_stenosis"]})
        assert len(matches) > 0

    def test_global_contraindication_hit(self):
        self.engine._load_default_rules()
        matches = self.engine.check("thyroid", {"patient_conditions": ["contrast_allergy"]})
        assert any(m.organ == "global" for m in matches)

    def test_unknown_organ_no_hit(self):
        self.engine._load_default_rules()
        matches = self.engine.check("unknown_organ", {"patient_conditions": []})
        organ_matches = [m for m in matches if m.organ == "unknown_organ"]
        assert len(organ_matches) == 0

    def test_clinical_context_matching(self):
        self.engine._load_default_rules()
        matches = self.engine.check("thyroid", {
            "clinical_context": {"conditions": ["pregnancy"]},
            "patient_conditions": ["pregnancy"],
        })
        assert len(matches) > 0

    def test_get_rules_for_organ(self):
        self.engine._load_default_rules()
        rules = self.engine.get_rules_for_organ("thyroid")
        assert len(rules) > 0
        assert all(r.organ == "thyroid" for r in rules)

    def test_get_global_rules(self):
        self.engine._load_default_rules()
        rules = self.engine.get_global_rules()
        assert len(rules) > 0
        assert all(r.organ == "global" for r in rules)

    def test_disabled_rule_not_matched(self):
        self.engine._load_default_rules()
        for rule in self.engine._organ_rules.get("thyroid", []):
            rule.enabled = False
        for rule in self.engine._global_rules:
            rule.enabled = False
        matches = self.engine.check("thyroid", {"patient_conditions": ["pregnancy"]})
        assert len(matches) == 0

    def test_contraindication_match_model(self):
        match = ContraindicationMatch(
            rule_id="test_rule",
            organ="thyroid",
            condition="pregnancy",
            description="测试禁忌",
            severity="high",
        )
        assert match.rule_id == "test_rule"
        assert match.severity == "high"


class TestContraindicationRuleModel:

    def test_rule_creation(self):
        rule = ContraindicationRule(
            id="test_rule",
            organ="thyroid",
            condition="pregnancy",
            description="测试禁忌",
            severity="high",
        )
        assert rule.id == "test_rule"
        assert rule.enabled is True

    def test_rule_disabled(self):
        rule = ContraindicationRule(
            id="test_rule",
            organ="thyroid",
            condition="pregnancy",
            description="测试禁忌",
            enabled=False,
        )
        assert rule.enabled is False
