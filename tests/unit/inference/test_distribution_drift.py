"""分布漂移监控（二期 §20）。

不变量：
1. 相同分布 KL=0；分布整体位移必须被测出来；
2. **零概率陷阱**：参考分布某箱为 0 而当前有值 -> 未平滑时 KL=+∞，任何阈值都失效；
3. **样本不足不判定**：几百个样本估 20 箱必然是噪声，此时给数字只会被误读；
4. 箱区间必须由参考分布固定，否则两次直方图的箱不可比，散度算出来没有意义。
"""
import math

import pytest

from app.config.settings import settings
from app.inference.distribution_drift import (
    DEFAULT_BINS,
    DistributionDriftMonitor,
    DistributionType,
    histogram,
    kl_divergence,
    mean_of,
)


def _normal_samples(mean: float, std: float, count: int = 2000):
    """确定性伪正态样本（Box-Muller），避免测试依赖随机种子。"""
    import math as _math

    samples = []
    for index in range(count):
        u1 = ((index * 7919) % 997) / 997 + 1e-9
        u2 = ((index * 104729) % 991) / 991
        z = _math.sqrt(-2 * _math.log(u1)) * _math.cos(2 * _math.pi * u2)
        samples.append(mean + std * z)
    return samples


class TestHistogram:
    def test_normalized(self):
        probabilities = histogram([1.0, 2.0, 3.0], lower=0.0, upper=4.0)

        assert sum(probabilities) == pytest.approx(1.0)
        assert len(probabilities) == DEFAULT_BINS

    def test_out_of_range_samples_clamped(self):
        """越界样本夹到边界箱：丢弃会让总量与样本数不符。"""
        probabilities = histogram([-100.0, 100.0], lower=0.0, upper=10.0, bins=5)

        assert sum(probabilities) == pytest.approx(1.0)
        assert probabilities[0] > 0
        assert probabilities[-1] > 0

    def test_rejects_invalid_range(self):
        with pytest.raises(ValueError):
            histogram([1.0], lower=1.0, upper=1.0)

    def test_rejects_single_bin(self):
        with pytest.raises(ValueError):
            histogram([1.0], bins=1, lower=0.0, upper=1.0)

    def test_smoothing_keeps_every_bin_positive(self):
        probabilities = histogram([1.0, 1.0, 1.0], lower=0.0, upper=3.0, bins=10)

        assert all(probability > 0 for probability in probabilities)


class TestKlDivergence:
    def test_identical_distributions_are_zero(self):
        reference = [0.25, 0.25, 0.25, 0.25]

        assert kl_divergence(reference, reference) == pytest.approx(0.0, abs=1e-9)

    def test_shifted_distribution_is_positive(self):
        reference = [0.5, 0.5, 0.0, 0.0]
        current = [0.0, 0.0, 0.5, 0.5]

        assert kl_divergence(reference, current) > 0.5

    def test_zero_probability_bin_does_not_explode(self):
        """参考分布里的 0 箱 + 当前有值：未平滑会得到 +∞。"""
        reference = [1.0, 0.0, 0.0]
        current = [0.0, 1.0, 0.0]

        value = kl_divergence(reference, current)

        assert math.isfinite(value)

    def test_not_symmetric(self):
        first = kl_divergence([0.9, 0.1], [0.5, 0.5])
        second = kl_divergence([0.5, 0.5], [0.9, 0.1])

        assert first != pytest.approx(second)

    def test_bin_count_mismatch_rejected(self):
        with pytest.raises(ValueError):
            kl_divergence([0.5, 0.5], [1.0])


class TestMeanOf:
    def test_average(self):
        assert mean_of([1.0, 2.0, 3.0]) == pytest.approx(2.0)

    def test_empty_is_zero(self):
        assert mean_of([]) == 0.0


class TestReference:
    def test_requires_minimum_samples(self):
        monitor = DistributionDriftMonitor(min_samples=100)

        with pytest.raises(ValueError):
            monitor.set_reference("input", _normal_samples(0.5, 0.1, 50))

    def test_constant_samples_get_expanded_range(self):
        """全同值时 upper == lower 无法分箱，必须撑开区间而不是抛错。"""
        monitor = DistributionDriftMonitor(min_samples=10)

        reference = monitor.set_reference("input", [0.5] * 20)

        assert reference.upper > reference.lower

    def test_reference_recorded(self):
        monitor = DistributionDriftMonitor(min_samples=10)

        monitor.set_reference("input", _normal_samples(0.5, 0.1, 100))

        assert monitor.reference("input") is not None
        assert monitor.reference("input").sample_count == 100


class TestDriftDetection:
    def _monitor(self):
        monitor = DistributionDriftMonitor(
            min_samples=200,
            input_kl_threshold=0.1,
            output_kl_threshold=0.15,
            output_mean_shift_threshold=0.05,
        )
        monitor.set_reference("input", _normal_samples(0.5, 0.1))
        monitor.set_reference("output", _normal_samples(0.8, 0.05))
        return monitor

    def test_same_distribution_not_drifted(self):
        monitor = self._monitor()

        result = monitor.check("input", _normal_samples(0.5, 0.1))

        assert result.drifted is False
        assert result.kl_divergence < 0.1

    def test_shifted_input_distribution_detected(self):
        monitor = self._monitor()

        result = monitor.check("input", _normal_samples(0.9, 0.1))

        assert result.drifted is True
        assert result.kl_divergence > 0.1
        assert any("KL 散度" in reason for reason in result.reasons)

    def test_output_mean_shift_detected_even_with_small_kl(self):
        """输出侧还有均值偏移这条判据：KL 不敏感时不能漏掉置信度整体下移。"""
        monitor = DistributionDriftMonitor(
            min_samples=200,
            output_kl_threshold=100.0,  # 故意把 KL 门槛抬到不可能触发
            output_mean_shift_threshold=0.05,
        )
        monitor.set_reference("output", _normal_samples(0.8, 0.2))

        result = monitor.check("output", _normal_samples(0.9, 0.2))

        assert result.drifted is True
        assert any("均值偏移" in reason for reason in result.reasons)

    def test_mean_shift_only_applies_to_output(self):
        monitor = DistributionDriftMonitor(
            min_samples=200, input_kl_threshold=100.0, output_mean_shift_threshold=0.01
        )
        monitor.set_reference("input", _normal_samples(0.5, 0.2))

        result = monitor.check("input", _normal_samples(0.9, 0.2))

        assert result.drifted is False

    def test_insufficient_samples_not_judged(self):
        monitor = self._monitor()

        result = monitor.check("input", _normal_samples(0.9, 0.1, 50))

        assert result.insufficient_samples is True
        assert result.drifted is False
        assert "样本不足" in result.reasons[0]
        assert result.kl_divergence == 0.0

    def test_missing_reference_is_reported(self):
        monitor = DistributionDriftMonitor(min_samples=10)

        result = monitor.check("output", [0.5] * 100)

        assert result.drifted is False
        assert "尚未建立参考分布" in result.reasons[0]

    def test_result_serialization(self):
        monitor = self._monitor()

        payload = monitor.check("input", _normal_samples(0.9, 0.1)).as_dict()

        assert set(payload) == {
            "distribution_type",
            "drifted",
            "insufficient_samples",
            "kl_divergence",
            "mean_shift",
            "sample_count",
            "reasons",
        }

    def test_reference_serialization(self):
        monitor = self._monitor()

        payload = monitor.reference("input").as_dict()

        assert set(payload) == {
            "distribution_type",
            "bins",
            "lower",
            "upper",
            "mean",
            "sample_count",
        }

    def test_reset_clears_references(self):
        monitor = self._monitor()

        monitor.reset()

        assert monitor.reference("input") is None


class TestThresholdWiring:
    def test_thresholds_from_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "drift_input_kl_threshold", 0.05)
        monitor = DistributionDriftMonitor(min_samples=200)
        monitor.set_reference("input", _normal_samples(0.5, 0.1))

        result = monitor.check("input", _normal_samples(0.5, 0.1))

        assert result.drifted is (result.kl_divergence > 0.05)

    def test_min_samples_from_settings(self, monkeypatch):
        monkeypatch.setattr(settings, "drift_distribution_min_samples", 5000)

        monitor = DistributionDriftMonitor()

        with pytest.raises(ValueError, match="at least 5000"):
            monitor.set_reference("input", [0.5] * 100)

    def test_no_reference_takes_priority_over_sample_size(self):
        """没有参考分布时，原因应是"缺少基线"而不是"样本不足"。"""
        monitor = DistributionDriftMonitor(min_samples=10)

        result = monitor.check("input", [0.5] * 100)

        assert result.insufficient_samples is False
        assert "尚未建立参考分布" in result.reasons[0]

    def test_invalid_thresholds_rejected_by_validator(self, monkeypatch):
        from app.config.validator import validate_config

        monkeypatch.setattr(settings, "drift_input_kl_threshold", 0.0)

        errors = validate_config()

        assert any("DRIFT_INPUT_KL_THRESHOLD" in error for error in errors)

    def test_distribution_type_values(self):
        assert DistributionType.INPUT.value == "input"
        assert DistributionType.OUTPUT.value == "output"
