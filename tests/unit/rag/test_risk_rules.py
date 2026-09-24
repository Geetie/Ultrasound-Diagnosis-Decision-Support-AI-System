"""M5 恶性概率与 M9 后方回声规则单测。"""
import pytest

from app.knowledge.risk_rules import (
    BIAS_BENIGN,
    BIAS_MALIGNANT,
    FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE,
    LIVER_RISK_MODEL,
    ORGAN_RISK_MODELS,
    POSTERIOR_ACOUSTIC_TI_RADS_POINTS,
    RISK_LEVEL_LOW_MAX,
    RISK_LEVEL_MEDIUM_MAX,
    THYROID_RISK_MODEL,
    compute_malignancy_risk,
    extract_udaf_features,
    get_risk_model,
    pick_posterior_acoustic,
    pick_posterior_acoustic_term,
    posterior_acoustic_bias,
    ti_rads_points_for_posterior,
)
from app.models.schemas import MalignancyRiskLevel

pytestmark = [pytest.mark.track_rag, pytest.mark.track_data]


class TestRiskModels:
    def test_thyroid_and_liver_are_modelled(self):
        assert get_risk_model("thyroid") is THYROID_RISK_MODEL
        assert get_risk_model("liver") is LIVER_RISK_MODEL

    def test_cardiac_is_not_modelled(self):
        """心脏超声不产出恶性概率：返回 None 而不是概率 0。"""
        assert get_risk_model("cardiac") is None
        assert compute_malignancy_risk("cardiac", ["micro"]) is None

    def test_organ_lookup_is_case_insensitive_and_trimmed(self):
        assert get_risk_model(" THYROID ") is THYROID_RISK_MODEL

    def test_unknown_and_empty_organ(self):
        assert get_risk_model("nope") is None
        assert get_risk_model(None) is None
        assert get_risk_model("") is None

    def test_every_feature_has_label_and_reference(self):
        for model in ORGAN_RISK_MODELS.values():
            for feature in model.features:
                assert feature.label, feature.feature
                assert feature.source_reference, feature.feature
                assert feature.contribution != 0.0, feature.feature

    def test_base_risk_within_unit_interval(self):
        for model in ORGAN_RISK_MODELS.values():
            assert 0.0 <= model.base_risk <= 1.0


class TestPosteriorAcousticRules:
    def test_ti_rads_points_match_spec(self):
        # M9 规格：衰减 +1 分；声影/增强/无/混合 均不计分
        assert POSTERIOR_ACOUSTIC_TI_RADS_POINTS["posterior_attenuation"] == 1
        assert POSTERIOR_ACOUSTIC_TI_RADS_POINTS["posterior_shadowing"] == 0
        assert POSTERIOR_ACOUSTIC_TI_RADS_POINTS["posterior_enhancement"] == 0
        assert POSTERIOR_ACOUSTIC_TI_RADS_POINTS["posterior_none"] == 0

    def test_bias_direction(self):
        assert posterior_acoustic_bias("posterior_attenuation") == BIAS_MALIGNANT
        assert posterior_acoustic_bias("posterior_shadowing") == BIAS_BENIGN
        assert posterior_acoustic_bias("posterior_enhancement") == BIAS_BENIGN

    def test_non_posterior_term_returns_none(self):
        assert posterior_acoustic_bias("hyperechoic") is None
        assert ti_rads_points_for_posterior("hyperechoic") is None
        assert ti_rads_points_for_posterior(None) is None

    def test_term_picker_returns_udaf_term(self):
        features = ["hypoechoic", "posterior_attenuation"]
        assert pick_posterior_acoustic_term(features) == "posterior_attenuation"
        assert pick_posterior_acoustic(features) == "attenuation"

    def test_term_picker_prefers_malignant_when_conflicting(self):
        """表述矛盾时取恶性倾向（风险判断宁可保守）。"""
        features = ["posterior_enhancement", "posterior_attenuation"]
        assert pick_posterior_acoustic_term(features) == "posterior_attenuation"

    def test_term_picker_handles_no_match(self):
        assert pick_posterior_acoustic_term(["hypoechoic"]) is None
        assert pick_posterior_acoustic([]) is None


class TestComputeMalignancyRisk:
    def test_probability_and_level_for_malignant_features(self):
        risk = compute_malignancy_risk(
            "thyroid", ["micro", "irregular", "ill-defined", "hypoechoic"]
        )
        assert risk is not None
        assert risk.probability > RISK_LEVEL_MEDIUM_MAX
        assert risk.level == MalignancyRiskLevel.HIGH
        assert risk.source_reference

    def test_benign_features_lower_the_probability(self):
        malignant_only = compute_malignancy_risk("thyroid", ["micro"])
        with_benign = compute_malignancy_risk("thyroid", ["micro", "clear", "oval"])
        assert with_benign.probability < malignant_only.probability

    def test_probability_clamped_to_unit_interval(self):
        risk = compute_malignancy_risk(
            "thyroid",
            ["micro", "irregular", "ill-defined", "hypoechoic", "mixed", "lobulated"],
        )
        assert 0.0 <= risk.probability <= 1.0

    def test_basis_is_returned_and_sorted_by_impact(self):
        risk = compute_malignancy_risk("thyroid", ["micro", "clear", "ill-defined"])
        contributions = [abs(item.contribution) for item in risk.basis]
        assert contributions == sorted(contributions, reverse=True)
        assert any(item.feature == "micro" for item in risk.basis)

    def test_unknown_features_are_ignored(self):
        assert compute_malignancy_risk("thyroid", ["TI-RADS 4C", "12.0x8.0 cm"]) is None

    def test_no_features_returns_none(self):
        """依据不足不应伪装成概率为零。"""
        assert compute_malignancy_risk("thyroid", []) is None

    def test_duplicate_features_counted_once(self):
        single = compute_malignancy_risk("thyroid", ["micro"])
        duplicated = compute_malignancy_risk("thyroid", ["micro", "micro"])
        assert duplicated.probability == single.probability

    def test_aspect_ratio_feature_participates(self):
        without = compute_malignancy_risk("thyroid", ["hypoechoic"])
        with_ratio = compute_malignancy_risk(
            "thyroid", ["hypoechoic", FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE]
        )
        assert with_ratio.probability > without.probability

    @pytest.mark.parametrize(
        "features,expected_level",
        [
            (["hypoechoic"], MalignancyRiskLevel.LOW),
            (["clear", "oval"], MalignancyRiskLevel.LOW),
            (["hypoechoic", "irregular"], MalignancyRiskLevel.MEDIUM),
            (["micro", "ill-defined", "irregular"], MalignancyRiskLevel.HIGH),
        ],
    )
    def test_level_thresholds(self, features, expected_level):
        assert compute_malignancy_risk("thyroid", features).level == expected_level

    def test_level_threshold_constants_in_order(self):
        assert 0.0 < RISK_LEVEL_LOW_MAX < RISK_LEVEL_MEDIUM_MAX < 1.0

    def test_liver_model_used_for_liver(self):
        risk = compute_malignancy_risk("liver", ["internal", "irregular"])
        assert risk is not None
        assert "LI-RADS" in risk.source_reference


class TestExtractUdafFeatures:
    def test_extracts_standard_terms(self):
        features = extract_udaf_features("甲状腺低回声结节，边界不清，形态不规则", organ="thyroid")
        assert "hypoechoic" in features
        assert "ill-defined" in features
        assert "irregular" in features

    def test_extracts_posterior_acoustic(self):
        features = extract_udaf_features("结节后方回声衰减", organ="thyroid")
        assert "posterior_attenuation" in features

    def test_empty_or_none_text(self):
        assert extract_udaf_features(None) == []
        assert extract_udaf_features("   ") == []

    def test_deduplicates_preserving_order(self):
        features = extract_udaf_features("低回声，低回声，边界不清")
        assert features.count("hypoechoic") == 1

    def test_parser_failure_is_swallowed(self, monkeypatch):
        class _BrokenParser:
            def parse(self, *_args, **_kwargs):
                raise RuntimeError("parser down")

        from app.knowledge import risk_rules

        original = risk_rules.get_shared_udt_parser()
        risk_rules.set_shared_udt_parser(_BrokenParser())
        try:
            assert extract_udaf_features("低回声") == []
        finally:
            risk_rules.set_shared_udt_parser(original)
