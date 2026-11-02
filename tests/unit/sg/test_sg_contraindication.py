import pytest

from safety_gateway.contraindication import (
    ContraindicationEngine,
    ContraindicationMatch,
    ContraindicationRule,
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


class TestConditionFamilies:
    """A.110 / G32：别名表是"多键互引"的，匹配必须按**同族**而不是字面键。

    修复前的实测缺陷：规则 `global_pregnancy_radiation` 的 condition 是
    `pregnancy_radiation`，而 `_CONDITION_ALIASES` 的**键**里没有它 →
    `.get(condition, [condition])` 退化成字面比较 → 患者勾「妊娠」时该规则
    永远不命中（勾了不拦，反向 fail-open）。
    """

    def setup_method(self):
        self.engine = ContraindicationEngine()
        self.engine._load_default_rules()

    def test_pregnancy_radiation_rule_fires_on_pregnancy(self):
        matches = self.engine.check("thyroid", {"patient_conditions": ["pregnancy"]})
        ids = {m.rule_id for m in matches}
        assert "global_pregnancy_radiation" in ids, (
            "妊娠患者必须命中「孕期辐射禁忌」全局规则（修复前恒不命中）"
        )

    def test_family_is_canonical_and_symmetric(self):
        fam = ContraindicationEngine._condition_families()
        for alias in ("pregnancy", "pregnancy_radiation", "pregnancy_ct", "pregnancy_stress_echo"):
            assert fam[alias] == fam["pregnancy"], f"{alias} 未与 pregnancy 归入同族"
        for alias in ("coagulopathy", "bleeding_disorder", "coagulation_defect"):
            assert fam[alias] == fam["coagulopathy"]

    def test_no_cross_family_false_positive(self):
        """反腿：妊娠不得命中凝血类规则（否则"同族"退化成"全都命中"）。"""
        matches = self.engine.check("liver", {"patient_conditions": ["pregnancy"]})
        ids = {m.rule_id for m in matches}
        assert "liver_coagulopathy_biopsy" not in ids
        assert "thyroid_anticoagulant_biopsy" not in ids

    def test_each_reachable_rule_has_a_trigger(self):
        """逐条枚举：3 个受支持器官 + 全局规则，各给一个能命中的患者条件。"""
        cases = {
            ("thyroid", "pregnancy"): "thyroid_pregnancy_fna",
            ("thyroid", "anticoagulant_therapy"): "thyroid_anticoagulant_biopsy",
            ("liver", "coagulopathy"): "liver_coagulopathy_biopsy",
            ("liver", "pregnancy"): "liver_pregnancy_ct",
            ("cardiac", "pregnancy"): "cardiac_pregnancy_stress_echo",
            ("cardiac", "severe_aortic_stenosis"): "cardiac_severe_aortic_stenosis",
        }
        for (organ, condition), rule_id in cases.items():
            ids = {m.rule_id for m in self.engine.check(organ, {"patient_conditions": [condition]})}
            assert rule_id in ids, f"{organ}+{condition} 未命中 {rule_id}（实际 {sorted(ids)}）"

    def test_unsupported_organ_rules_stay_unreachable_by_design(self):
        """肾/乳腺/胆囊/脾/胰/血管规则：系统不支持这些器官 → 取不到，属设计而非缺陷。

        这里钉住"确实取不到"，避免把 G32 的 7 条当成可修 bug 反复返工。
        """
        for organ in ("kidney", "breast", "gallbladder", "spleen", "pancreas", "vascular"):
            matches = self.engine.check(organ, {"patient_conditions": ["coagulopathy", "contrast_allergy", "pregnancy"]})
            assert all(m.organ in ("global", organ) for m in matches)
