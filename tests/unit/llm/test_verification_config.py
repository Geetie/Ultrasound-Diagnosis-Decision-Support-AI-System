"""

单元测试：验证配置管理模块
"""

from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_inf]

from app.inference.verification_config import (
    AdaptiveThresholdConfig,
    BoundaryConfig,
    BoundaryPairConfig,
    ForbiddenTermConfig,
    ForbiddenTermsConfig,
    NumericalRangeConfig,
    NumericalValidationConfig,
    StrategyWeightConfig,
    VerificationConfig,
    compute_adaptive_threshold,
    get_default_boundary_config,
    get_default_forbidden_terms_config,
    get_default_numerical_validation_config,
    get_default_verification_config,
    get_verification_config,
    load_config_from_json,
    reset_verification_config,
    save_config_to_json,
)


class TestBoundaryPairConfig:
    def test_valid_config(self):
        cfg = BoundaryPairConfig(from_grade="3", to_grade="4", weight=1.0, is_critical=True)
        assert cfg.from_grade == "3"
        assert cfg.to_grade == "4"
        assert cfg.weight == 1.0
        assert cfg.is_critical is True

    def test_defaults(self):
        cfg = BoundaryPairConfig(from_grade="3", to_grade="4")
        assert cfg.weight == 1.0
        assert cfg.is_critical is False


class TestBoundaryConfig:
    def test_empty_config(self):
        cfg = BoundaryConfig()
        assert cfg.version == "1.0"
        assert cfg.organ_boundaries == {}

    def test_with_boundaries(self):
        pairs = [BoundaryPairConfig(from_grade="3", to_grade="4", weight=1.0, is_critical=True)]
        cfg = BoundaryConfig(organ_boundaries={"thyroid": pairs})
        assert "thyroid" in cfg.organ_boundaries
        assert len(cfg.organ_boundaries["thyroid"]) == 1


class TestForbiddenTermConfig:
    def test_valid_config(self):
        cfg = ForbiddenTermConfig(term="肺癌", variants=["肺恶性肿瘤"], severity=2, description="test")
        assert cfg.term == "肺癌"
        assert cfg.variants == ["肺恶性肿瘤"]
        assert cfg.severity == 2

    def test_defaults(self):
        cfg = ForbiddenTermConfig(term="肺癌")
        assert cfg.variants == []
        assert cfg.severity == 2
        assert cfg.description == ""


class TestForbiddenTermsConfig:
    def test_empty_config(self):
        cfg = ForbiddenTermsConfig()
        assert cfg.organ_terms == {}

    def test_with_terms(self):
        terms = [ForbiddenTermConfig(term="肺癌")]
        cfg = ForbiddenTermsConfig(organ_terms={"thyroid": terms})
        assert "thyroid" in cfg.organ_terms
        assert len(cfg.organ_terms["thyroid"]) == 1


class TestStrategyWeightConfig:
    def test_defaults(self):
        cfg = StrategyWeightConfig()
        assert cfg.forbidden_terms == 1.0
        assert cfg.contradictory_grading == 1.5
        assert cfg.medical_common_sense == 2.0
        assert cfg.numerical_validity == 1.0


class TestNumericalRangeConfig:
    def test_valid_config(self):
        cfg = NumericalRangeConfig(parameter="LVEF", min_value=20.0, max_value=85.0, unit="%")
        assert cfg.parameter == "LVEF"
        assert cfg.min_value == 20.0
        assert cfg.max_value == 85.0
        assert cfg.unit == "%"


class TestNumericalValidationConfig:
    def test_empty_config(self):
        cfg = NumericalValidationConfig()
        assert cfg.organ_ranges == {}

    def test_with_ranges(self):
        ranges = [NumericalRangeConfig(parameter="LVEF", min_value=20.0, max_value=85.0)]
        cfg = NumericalValidationConfig(organ_ranges={"cardiac": ranges})
        assert "cardiac" in cfg.organ_ranges


class TestVerificationConfig:
    def test_default_config(self):
        cfg = VerificationConfig()
        assert cfg.version == "1.0"
        assert cfg.test_data_version == "2.0"
        assert isinstance(cfg.boundary_config, BoundaryConfig)
        assert isinstance(cfg.forbidden_terms_config, ForbiddenTermsConfig)
        assert isinstance(cfg.strategy_weights, StrategyWeightConfig)
        assert isinstance(cfg.numerical_validation_config, NumericalValidationConfig)

    def test_full_config(self):
        cfg = get_default_verification_config()
        assert cfg.boundary_config.organ_boundaries
        assert cfg.forbidden_terms_config.organ_terms
        assert cfg.numerical_validation_config.organ_ranges


class TestGetDefaultBoundaryConfig:
    def test_thyroid_boundaries(self):
        cfg = get_default_boundary_config()
        thyroid = cfg.organ_boundaries.get("thyroid", [])
        assert len(thyroid) == 22

    def test_liver_boundaries(self):
        cfg = get_default_boundary_config()
        liver = cfg.organ_boundaries.get("liver", [])
        assert len(liver) == 6

    def test_cardiac_boundaries_populated(self):
        cfg = get_default_boundary_config()
        cardiac = cfg.organ_boundaries.get("cardiac", [])
        assert len(cardiac) > 0
        assert any(b.from_grade == "normal" and b.to_grade == "severe" for b in cardiac)

    def test_breast_boundaries_populated(self):
        cfg = get_default_boundary_config()
        breast = cfg.organ_boundaries.get("breast", [])
        assert len(breast) > 0
        assert any(b.from_grade == "3" and b.to_grade == "4a" for b in breast)

    def test_critical_boundaries_present(self):
        cfg = get_default_boundary_config()
        thyroid = cfg.organ_boundaries.get("thyroid", [])
        critical = [b for b in thyroid if b.is_critical]
        assert len(critical) > 0

    def test_jump_boundaries_have_higher_weight(self):
        cfg = get_default_boundary_config()
        thyroid = cfg.organ_boundaries.get("thyroid", [])
        jump_3_5 = [b for b in thyroid if b.from_grade == "3" and b.to_grade == "5"]
        adjacent_3_4 = [b for b in thyroid if b.from_grade == "3" and b.to_grade == "4"]
        assert jump_3_5[0].weight > adjacent_3_4[0].weight


class TestGetDefaultForbiddenTermsConfig:
    def test_thyroid_terms(self):
        cfg = get_default_forbidden_terms_config()
        thyroid = cfg.organ_terms.get("thyroid", [])
        assert len(thyroid) == 18

    def test_liver_terms(self):
        cfg = get_default_forbidden_terms_config()
        liver = cfg.organ_terms.get("liver", [])
        assert len(liver) == 13

    def test_cardiac_terms(self):
        cfg = get_default_forbidden_terms_config()
        cardiac = cfg.organ_terms.get("cardiac", [])
        assert len(cardiac) == 12

    def test_breast_terms(self):
        cfg = get_default_forbidden_terms_config()
        breast = cfg.organ_terms.get("breast", [])
        assert len(breast) > 0

    def test_variants_present(self):
        cfg = get_default_forbidden_terms_config()
        for organ, terms in cfg.organ_terms.items():
            for term in terms:
                assert isinstance(term.variants, list)


class TestGetDefaultNumericalValidationConfig:
    def test_thyroid_ranges(self):
        cfg = get_default_numerical_validation_config()
        thyroid = cfg.organ_ranges.get("thyroid", [])
        assert len(thyroid) == 1
        assert thyroid[0].parameter == "结节大小"

    def test_liver_ranges(self):
        cfg = get_default_numerical_validation_config()
        liver = cfg.organ_ranges.get("liver", [])
        assert len(liver) == 1
        assert liver[0].parameter == "占位大小"

    def test_cardiac_ranges(self):
        cfg = get_default_numerical_validation_config()
        cardiac = cfg.organ_ranges.get("cardiac", [])
        assert len(cardiac) == 1
        assert cardiac[0].parameter == "LVEF"


class TestJsonRoundtrip:
    def test_save_and_load(self, tmp_path):
        cfg = get_default_verification_config()
        file_path = str(tmp_path / "test_config.json")
        saved = save_config_to_json(cfg, file_path)
        assert saved is True

        loaded = load_config_from_json(file_path)
        assert loaded is not None
        assert loaded.version == cfg.version
        assert loaded.test_data_version == cfg.test_data_version
        assert len(loaded.boundary_config.organ_boundaries.get("thyroid", [])) == 22
        assert len(loaded.forbidden_terms_config.organ_terms.get("thyroid", [])) == 18

    def test_load_nonexistent_file(self):
        loaded = load_config_from_json("/nonexistent/path/config.json")
        assert loaded is None

    def test_load_invalid_json(self, tmp_path):
        file_path = tmp_path / "bad.json"
        file_path.write_text("not valid json{{{")
        loaded = load_config_from_json(str(file_path))
        assert loaded is None

    def test_save_creates_parent_dirs(self, tmp_path):
        cfg = get_default_verification_config()
        file_path = str(tmp_path / "sub" / "dir" / "config.json")
        saved = save_config_to_json(cfg, file_path)
        assert saved is True
        assert Path(file_path).exists()


class TestGetVerificationConfig:
    def setup_method(self):
        reset_verification_config()

    def test_returns_default_config(self):
        cfg = get_verification_config()
        assert isinstance(cfg, VerificationConfig)
        assert cfg.test_data_version == "2.0"

    def test_singleton_behavior(self):
        cfg1 = get_verification_config()
        cfg2 = get_verification_config()
        assert cfg1 is cfg2

    def test_reset_creates_new_instance(self):
        cfg1 = get_verification_config()
        reset_verification_config()
        cfg2 = get_verification_config()
        assert cfg1 is not cfg2

    def test_load_from_json_path(self, tmp_path):
        cfg = get_default_verification_config()
        file_path = str(tmp_path / "config.json")
        save_config_to_json(cfg, file_path)

        reset_verification_config()
        loaded = get_verification_config(config_path=file_path)
        assert loaded is not None
        assert loaded.test_data_version == "2.0"

    def test_fallback_to_default_on_bad_path(self):
        reset_verification_config()
        cfg = get_verification_config(config_path="/nonexistent/path.json")
        assert isinstance(cfg, VerificationConfig)


class TestAdaptiveThresholdConfig:
    def test_valid_config(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="grading_consistency",
            base_threshold=0.95,
            min_threshold=0.90,
            max_threshold=0.99,
        )
        assert cfg.metric_name == "grading_consistency"
        assert cfg.base_threshold == 0.95
        assert cfg.adjustment_factor == 0.1

    def test_custom_adjustment_factor(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="accuracy",
            base_threshold=0.85,
            min_threshold=0.80,
            max_threshold=0.95,
            adjustment_factor=0.05,
        )
        assert cfg.adjustment_factor == 0.05


class TestComputeAdaptiveThreshold:
    def test_no_history_returns_base(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="grading_consistency",
            base_threshold=0.95,
            min_threshold=0.90,
            max_threshold=0.99,
        )
        result = compute_adaptive_threshold(cfg, [])
        assert result == 0.95

    def test_high_pass_rate_increases_threshold(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="grading_consistency",
            base_threshold=0.95,
            min_threshold=0.90,
            max_threshold=0.99,
            adjustment_factor=0.02,
        )
        result = compute_adaptive_threshold(cfg, [0.99, 0.98, 0.99])
        assert result == 0.97

    def test_low_pass_rate_decreases_threshold(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="grading_consistency",
            base_threshold=0.95,
            min_threshold=0.90,
            max_threshold=0.99,
            adjustment_factor=0.02,
        )
        result = compute_adaptive_threshold(cfg, [0.85, 0.88, 0.87])
        assert abs(result - 0.93) < 1e-9

    def test_medium_pass_rate_keeps_base(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="grading_consistency",
            base_threshold=0.95,
            min_threshold=0.90,
            max_threshold=0.99,
        )
        result = compute_adaptive_threshold(cfg, [0.93, 0.94, 0.95])
        assert result == 0.95

    def test_clamped_to_min(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="accuracy",
            base_threshold=0.85,
            min_threshold=0.82,
            max_threshold=0.95,
            adjustment_factor=0.1,
        )
        result = compute_adaptive_threshold(cfg, [0.80, 0.82, 0.81])
        assert result == 0.82

    def test_clamped_to_max(self):
        cfg = AdaptiveThresholdConfig(
            metric_name="accuracy",
            base_threshold=0.85,
            min_threshold=0.80,
            max_threshold=0.90,
            adjustment_factor=0.1,
        )
        result = compute_adaptive_threshold(cfg, [0.99, 0.99, 0.99])
        assert result == 0.90


class TestBoundaryPairConfigValidation:
    def test_negative_weight_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BoundaryPairConfig(from_grade="3", to_grade="4a", weight=-1.0)

    def test_zero_weight_allowed(self):
        cfg = BoundaryPairConfig(from_grade="3", to_grade="4a", weight=0.0)
        assert cfg.weight == 0.0

    def test_empty_grade_strings_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            BoundaryPairConfig(from_grade="", to_grade="4a", weight=1.0)
        with pytest.raises(ValidationError):
            BoundaryPairConfig(from_grade="3", to_grade="", weight=1.0)


class TestForbiddenTermConfigValidation:
    def test_empty_term(self):
        cfg = ForbiddenTermConfig(term="", description="test", severity=1, variants=[])
        assert cfg.term == ""

    def test_empty_variants_list(self):
        cfg = ForbiddenTermConfig(term="肺癌", description="test", severity=2, variants=[])
        assert cfg.variants == []

    def test_variants_with_duplicates(self):
        cfg = ForbiddenTermConfig(
            term="肺癌",
            description="test",
            severity=2,
            variants=["肺恶性肿瘤", "肺恶性肿瘤"],
        )
        assert len(cfg.variants) == 2


class TestStrategyWeightConfigValidation:
    def test_negative_weight_rejected(self):
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            StrategyWeightConfig(
                forbidden_terms=-0.5,
                contradictory_grading=0.2,
                medical_common_sense=0.2,
                numerical_validity=0.2,
                context_aware=0.2,
            )

    def test_zero_weights_allowed(self):
        cfg = StrategyWeightConfig(
            forbidden_terms=0.0,
            contradictory_grading=0.0,
            medical_common_sense=0.0,
            numerical_validity=0.0,
            context_aware=0.0,
        )
        assert cfg.forbidden_terms == 0.0


class TestVerificationConfigRoundtripWithNewFields:
    def test_consistency_thresholds_roundtrip(self):
        cfg = get_verification_config()
        d = cfg.model_dump()
        assert "consistency_thresholds" in d
        assert d["consistency_thresholds"]["grading_consistency_rate"] == 0.95
        assert d["consistency_thresholds"]["hallucination_rate_warning"] == 0.003
        assert d["consistency_thresholds"]["hallucination_rate_critical"] == 0.005

        restored = VerificationConfig.model_validate(d)
        assert restored.consistency_thresholds.grading_consistency_rate == 0.95
        assert restored.consistency_thresholds.hallucination_rate_warning == 0.003

    def test_benchmark_thresholds_roundtrip(self):
        cfg = get_verification_config()
        d = cfg.model_dump()
        assert "benchmark_thresholds" in d
        assert d["benchmark_thresholds"]["accuracy"] == 0.89
        assert d["benchmark_thresholds"]["latency_p99_ms"] == 3000.0

        restored = VerificationConfig.model_validate(d)
        assert restored.benchmark_thresholds.accuracy == 0.89

    def test_json_roundtrip_with_new_fields(self):
        cfg = get_verification_config()
        json_str = cfg.model_dump_json()
        restored = VerificationConfig.model_validate_json(json_str)
        assert restored.consistency_thresholds.grading_consistency_rate == cfg.consistency_thresholds.grading_consistency_rate
        assert restored.benchmark_thresholds.accuracy == cfg.benchmark_thresholds.accuracy


class TestConcurrentConfigAccess:
    def test_concurrent_get_verification_config(self):
        import threading
        results = []
        errors = []

        def get_config():
            try:
                cfg = get_verification_config()
                results.append(cfg.version)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=get_config) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert len(results) == 10
        assert all(v == results[0] for v in results)
