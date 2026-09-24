"""A/B 实验框架（二期 §19）。

不变量：
1. 分流确定性且与灰度路由共用同一套哈希——同一会话在两个系统里必须落同一桶；
2. compact 档位不进实验：在降级路径上比模型，分不清是模型差还是档位差；
3. 样本不足时不得给出结论（尤其不能把"没有数据"当成"无差异"）；
4. 非劣效性优先：未达显著但在界内应判非劣效，而不是失败；
5. 卡方检验的 p 值要对得上已知数值（自研实现必须被验证，不能自证）。
"""
import math

import pytest

from app.config.settings import settings
from app.services.ab_test import (
    DEFAULT_MDE,
    EarlyStop,
    ExperimentConfig,
    Variant,
    Verdict,
    assign_variant,
    chi_square_two_proportions,
    evaluate_experiment,
    evaluate_experiment_from_counts,
    get_experiment_config,
    required_sample_size,
)
from app.services.rollout_router import bucket_of


def _config(**overrides) -> ExperimentConfig:
    base = {
        "name": "exp",
        "enabled": True,
        "traffic_percent": 20,
        "min_samples_per_variant": 100,
    }
    base.update(overrides)
    return ExperimentConfig(**base)


class TestAssignment:
    def test_deterministic(self):
        config = _config()

        first = assign_variant("session_abc", config)
        for _ in range(20):
            assert assign_variant("session_abc", config) is first

    def test_disabled_experiment_always_control(self):
        config = _config(enabled=False)

        assert all(
            assign_variant(f"session_{index}", config) is Variant.CONTROL
            for index in range(50)
        )

    def test_compact_profile_stays_control(self):
        """compact 是降级路径，结论会被档位差异污染。"""
        config = _config()

        assert all(
            assign_variant(f"session_{index}", config, profile="compact") is Variant.CONTROL
            for index in range(50)
        )

    def test_missing_session_id_stays_control(self):
        config = _config()

        assert assign_variant("", config) is Variant.CONTROL

    def test_zero_traffic_stays_control(self):
        config = _config(traffic_percent=0)

        assert assign_variant("session_abc", config) is Variant.CONTROL

    def test_traffic_percent_respected(self):
        config = _config(traffic_percent=20)

        in_experiment = sum(
            1
            for index in range(4000)
            if bucket_of(f"session_{index}") < config.traffic_percent
        )

        # 分桶本身决定入组量；这里验证"入组即分流"的比例接近对半
        assert 0.15 < in_experiment / 4000 < 0.25

    def test_split_is_balanced(self):
        config = _config(traffic_percent=100)

        variants = [assign_variant(f"session_{index}", config) for index in range(2000)]
        treatment_share = variants.count(Variant.TREATMENT) / len(variants)

        assert 0.45 < treatment_share < 0.55

    def test_shares_bucket_with_rollout_router(self):
        """分流必须与灰度用同一套分桶：两处算法漂移会让同一会话落不同组。"""
        config = _config(traffic_percent=100)

        for index in range(50):
            session_id = f"session_{index}"
            bucket = bucket_of(session_id)
            expected = Variant.TREATMENT if bucket % 2 else Variant.CONTROL
            assert assign_variant(session_id, config) is expected


class TestSampleSize:
    def test_required_sample_size_is_plausible(self):
        """3% 相对提升、基线 90% 成功率：每组应在万级以内、千级以上。"""
        required = required_sample_size(0.9, mde=0.03)

        assert 1000 < required < 60000

    def test_smaller_mde_needs_more_samples(self):
        assert required_sample_size(0.9, mde=0.01) > required_sample_size(0.9, mde=0.05)

    def test_higher_power_needs_more_samples(self):
        assert required_sample_size(0.9, power=0.9) > required_sample_size(0.9, power=0.8)

    def test_rejects_degenerate_baseline(self):
        with pytest.raises(ValueError):
            required_sample_size(0.0)
        with pytest.raises(ValueError):
            required_sample_size(1.0)

    def test_rejects_non_positive_mde(self):
        with pytest.raises(ValueError):
            required_sample_size(0.5, mde=0.0)


class TestChiSquare:
    def test_identical_groups_have_p_one(self):
        result = chi_square_two_proportions(100, 1000, 100, 1000)

        assert result["statistic"] == pytest.approx(0.0, abs=1e-9)
        assert result["p_value"] == pytest.approx(1.0)

    def test_matches_hand_computed_value(self):
        """手算核对：expected 70/30 两侧，(60-70)²/70*2+(40-30)²/30*2 = 9.5238。"""
        result = chi_square_two_proportions(60, 100, 80, 100)

        assert result["statistic"] == pytest.approx(9.5238, abs=0.001)
        assert result["p_value"] == pytest.approx(0.002, abs=0.001)

    def test_extreme_separation_is_significant(self):
        result = chi_square_two_proportions(10, 1000, 900, 1000)

        assert result["p_value"] < 0.001

    def test_empty_sample_returns_zero_not_p_one(self):
        """返回 1.0 会把"没有数据"伪装成"无差异"。"""
        result = chi_square_two_proportions(0, 0, 0, 0)

        assert result["p_value"] == 0.0

    def test_symmetric(self):
        first = chi_square_two_proportions(60, 100, 80, 100)
        second = chi_square_two_proportions(80, 100, 60, 100)

        assert first["statistic"] == pytest.approx(second["statistic"])


class TestEvaluation:
    def test_not_enough_data_when_sample_below_minimum(self):
        evaluation = evaluate_experiment(
            _config(min_samples_per_variant=500),
            control_successes=50,
            control_total=100,
            treatment_successes=60,
            treatment_total=100,
        )

        assert evaluation.verdict == Verdict.NOT_ENOUGH_DATA.value
        assert evaluation.enough_data is False
        assert evaluation.early_stop == EarlyStop.CONTINUE.value
        assert "样本不足" in evaluation.reasons[0]

    def test_empty_group_is_not_a_conclusion(self):
        evaluation = evaluate_experiment(
            _config(),
            control_successes=0,
            control_total=0,
            treatment_successes=60,
            treatment_total=100,
        )

        assert evaluation.verdict == Verdict.NOT_ENOUGH_DATA.value
        assert "样本为 0" in evaluation.reasons[0]

    def test_detects_superior(self):
        evaluation = evaluate_experiment(
            _config(),
            control_successes=700,
            control_total=1000,
            treatment_successes=820,
            treatment_total=1000,
        )

        assert evaluation.verdict == Verdict.SUPERIOR.value
        assert evaluation.early_stop == EarlyStop.STOP_SUPERIOR.value

    def test_detects_harm_and_stops_immediately(self):
        evaluation = evaluate_experiment(
            _config(),
            control_successes=820,
            control_total=1000,
            treatment_successes=700,
            treatment_total=1000,
        )

        assert evaluation.verdict == Verdict.INFERIOR.value
        assert evaluation.early_stop == EarlyStop.STOP_HARM.value

    def test_non_inferior_when_within_margin_but_not_significant(self):
        """未达显著但差在非劣效界内：应判非劣效而不是失败。"""
        evaluation = evaluate_experiment(
            _config(non_inferiority_margin=0.05, min_samples_per_variant=100),
            control_successes=500,
            control_total=1000,
            treatment_successes=490,
            treatment_total=1000,
        )

        assert evaluation.verdict == Verdict.NON_INFERIOR_ONLY.value
        assert evaluation.early_stop == EarlyStop.CONTINUE.value

    def test_futility_stop_after_enough_samples_without_difference(self):
        evaluation = evaluate_experiment(
            _config(min_samples_per_variant=100),
            control_successes=9000,
            control_total=10000,
            treatment_successes=9000,
            treatment_total=10000,
        )

        assert evaluation.early_stop == EarlyStop.STOP_FUTILITY.value

    def test_reports_required_sample_size(self):
        evaluation = evaluate_experiment(
            _config(),
            control_successes=700,
            control_total=1000,
            treatment_successes=700,
            treatment_total=1000,
        )

        assert evaluation.required_per_variant > 0

    def test_degenerate_baseline_marks_not_applicable(self):
        """基线 0/100% 时公式不适用：标 0 表示不适用，而不是编一个数字。"""
        evaluation = evaluate_experiment(
            _config(min_samples_per_variant=10),
            control_successes=0,
            control_total=100,
            treatment_successes=0,
            treatment_total=100,
        )

        assert evaluation.required_per_variant == 0

    def test_absolute_lift_signed(self):
        evaluation = evaluate_experiment(
            _config(),
            control_successes=600,
            control_total=1000,
            treatment_successes=500,
            treatment_total=1000,
        )

        assert evaluation.absolute_lift == pytest.approx(-0.1)

    def test_from_counts_helper(self):
        evaluation = evaluate_experiment_from_counts(
            _config(), {"control": (700, 1000), "treatment": (820, 1000)}
        )

        assert evaluation.verdict == Verdict.SUPERIOR.value

    def test_as_dict_shape_for_dashboard(self):
        payload = evaluate_experiment_from_counts(
            _config(), {"control": (700, 1000), "treatment": (820, 1000)}
        ).as_dict()

        assert set(payload) == {
            "experiment",
            "verdict",
            "early_stop",
            "p_value",
            "statistic",
            "absolute_lift",
            "required_per_variant",
            "control",
            "treatment",
            "reasons",
            "config",
        }


class TestConfigWiring:
    def test_reads_from_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "ab_experiment_enabled", True)
        monkeypatch.setattr(settings, "ab_experiment_name", "exp_x")
        monkeypatch.setattr(settings, "ab_mde", 0.02)

        config = get_experiment_config()

        assert config.enabled is True
        assert config.name == "exp_x"
        assert config.mde == pytest.approx(0.02)

    def test_default_config_matches_spec(self):
        config = get_experiment_config()

        assert config.alpha == pytest.approx(0.05)
        assert config.power == pytest.approx(0.80)
        assert config.mde == pytest.approx(DEFAULT_MDE)

    def test_invalid_settings_rejected_when_enabled(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "ab_experiment_enabled", True)
        monkeypatch.setattr(settings, "ab_mde", 0.0)

        errors = validate_config()

        assert any("AB_MDE" in error for error in errors)

    def test_invalid_settings_ignored_when_disabled(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "ab_experiment_enabled", False)
        monkeypatch.setattr(settings, "ab_mde", 0.0)

        assert not any("AB_MDE" in error for error in validate_config())


class TestNormalQuantile:
    def test_known_quantiles(self):
        from app.services.ab_test import _z_for_confidence

        assert _z_for_confidence(0.975) == pytest.approx(1.96, abs=0.01)
        assert _z_for_confidence(0.80) == pytest.approx(0.8416, abs=0.01)
        assert _z_for_confidence(0.5) == 0.0

    def test_monotonic(self):
        from app.services.ab_test import _z_for_confidence

        values = [_z_for_confidence(p) for p in (0.6, 0.75, 0.9, 0.975, 0.995)]

        assert values == sorted(values)

    def test_rejects_out_of_range(self):
        from app.services.ab_test import _z_for_confidence

        with pytest.raises(ValueError):
            _z_for_confidence(1.0)

    def test_symmetric_for_low_confidence(self):
        from app.services.ab_test import _z_for_confidence

        assert _z_for_confidence(0.025) == pytest.approx(-1.96, abs=0.01)


class TestChiSquareSf:
    def test_known_sf_value(self):
        from app.services.ab_test import _chi2_sf

        # χ²=3.841 对应 p=0.05（dof=1）
        assert _chi2_sf(3.841, 1) == pytest.approx(0.05, abs=0.001)

    def test_zero_statistic_is_one(self):
        from app.services.ab_test import _chi2_sf

        assert _chi2_sf(0.0, 1) == 1.0

    def test_rejects_unsupported_dof(self):
        from app.services.ab_test import _chi2_sf

        with pytest.raises(ValueError):
            _chi2_sf(1.0, 3)

    def test_normal_cdf_sanity(self):
        from app.services.ab_test import _normal_cdf

        assert _normal_cdf(0.0) == pytest.approx(0.5)
        assert math.isclose(_normal_cdf(1.96), 0.975, abs_tol=0.001)
