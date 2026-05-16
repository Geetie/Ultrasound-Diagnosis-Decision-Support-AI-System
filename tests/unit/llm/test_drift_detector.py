"""

单元测试：模型漂移检测器
"""

import json
import math
import os
import tempfile
from pathlib import Path

import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_obs]

from app.inference.drift_detector import (
    DriftAlert,
    DriftDetector,
    DriftReport,
    DriftSeverity,
    MetricSnapshot,
    MetricType,
    PerformanceBaseline,
)


@pytest.fixture
def tmp_baseline_dir(tmp_path):
    return str(tmp_path / "drift_baselines")


@pytest.fixture
def detector(tmp_baseline_dir):
    return DriftDetector(
        model_name="test_model",
        model_version="1.0",
        baseline_dir=tmp_baseline_dir,
    )


def _make_history(n: int = 20, base: float = 0.90, noise: float = 0.02):
    import random
    rng = random.Random(42)
    return [base + rng.gauss(0, noise) for _ in range(n)]


class TestDriftDetectorInit:
    def test_initialization(self, detector):
        assert detector.model_name == "test_model"
        assert detector.model_version == "1.0"
        assert detector.baselines == {}

    def test_baseline_dir_created(self, tmp_baseline_dir):
        DriftDetector(model_name="x", baseline_dir=tmp_baseline_dir)
        assert Path(tmp_baseline_dir).exists()


class TestComputeBaseline:
    def test_compute_baseline_success(self, detector):
        values = _make_history(30, base=0.90, noise=0.01)
        baseline = detector.compute_baseline(MetricType.ACCURACY, values)
        assert baseline is not None
        assert baseline.metric_type == MetricType.ACCURACY
        assert baseline.sample_size == 30
        assert abs(baseline.mean - 0.90) < 0.05
        assert baseline.warning_threshold < baseline.mean
        assert baseline.critical_threshold < baseline.warning_threshold

    def test_compute_baseline_lower_is_better(self, detector):
        values = _make_history(30, base=200.0, noise=10.0)
        baseline = detector.compute_baseline(MetricType.LATENCY_P99, values)
        assert baseline is not None
        assert baseline.warning_threshold > baseline.mean
        assert baseline.critical_threshold > baseline.warning_threshold

    def test_compute_baseline_insufficient_data(self, detector):
        values = [0.9, 0.91, 0.89]
        baseline = detector.compute_baseline(MetricType.ACCURACY, values)
        assert baseline is None

    def test_compute_baseline_minimum_data(self, detector):
        values = _make_history(30, base=0.85, noise=0.01)
        baseline = detector.compute_baseline(MetricType.ACCURACY, values)
        assert baseline is not None
        assert baseline.sample_size == 30


class TestSaveLoadBaseline:
    def test_save_and_load_roundtrip(self, detector):
        baseline = PerformanceBaseline(
            metric_type=MetricType.ACCURACY,
            mean=0.90,
            std_dev=0.02,
            sample_size=20,
            warning_threshold=0.86,
            critical_threshold=0.84,
            organ="thyroid",
        )
        saved = detector.save_baseline(baseline, organ="thyroid")
        assert saved is True

        loaded = detector.load_baseline(MetricType.ACCURACY, organ="thyroid")
        assert loaded is not None
        assert loaded.metric_type == MetricType.ACCURACY
        assert abs(loaded.mean - 0.90) < 1e-9
        assert abs(loaded.std_dev - 0.02) < 1e-9
        assert loaded.sample_size == 20
        assert loaded.organ == "thyroid"

    def test_load_nonexistent_baseline(self, detector):
        loaded = detector.load_baseline(MetricType.HALLUCINATION_RATE, organ="liver")
        assert loaded is None

    def test_save_creates_file(self, detector, tmp_baseline_dir):
        baseline = PerformanceBaseline(
            metric_type=MetricType.LATENCY_P50,
            mean=150.0,
            std_dev=20.0,
            sample_size=15,
            warning_threshold=190.0,
            critical_threshold=210.0,
        )
        detector.save_baseline(baseline, organ="all")
        path = detector._get_baseline_path("all", MetricType.LATENCY_P50.value)
        assert path.exists()

    def test_multiple_metric_types_no_overwrite(self, detector, tmp_baseline_dir):
        baseline_acc = PerformanceBaseline(
            metric_type=MetricType.ACCURACY,
            mean=0.90,
            std_dev=0.02,
            sample_size=20,
            warning_threshold=0.86,
            critical_threshold=0.84,
            organ="thyroid",
        )
        baseline_lat = PerformanceBaseline(
            metric_type=MetricType.LATENCY_P50,
            mean=150.0,
            std_dev=20.0,
            sample_size=20,
            warning_threshold=190.0,
            critical_threshold=210.0,
            organ="thyroid",
        )
        detector.save_baseline(baseline_acc, organ="thyroid")
        detector.save_baseline(baseline_lat, organ="thyroid")

        loaded_acc = detector.load_baseline(MetricType.ACCURACY, organ="thyroid")
        loaded_lat = detector.load_baseline(MetricType.LATENCY_P50, organ="thyroid")

        assert loaded_acc is not None
        assert loaded_acc.metric_type == MetricType.ACCURACY
        assert abs(loaded_acc.mean - 0.90) < 1e-9
        assert loaded_lat is not None
        assert loaded_lat.metric_type == MetricType.LATENCY_P50
        assert abs(loaded_lat.mean - 150.0) < 1e-9

    def test_load_from_memory_cache(self, detector):
        baseline = PerformanceBaseline(
            metric_type=MetricType.GRADING_CONSISTENCY,
            mean=0.96,
            std_dev=0.01,
            sample_size=30,
            warning_threshold=0.94,
            critical_threshold=0.93,
        )
        detector.save_baseline(baseline, organ="all")
        loaded = detector.load_baseline(MetricType.GRADING_CONSISTENCY, organ="all")
        assert loaded is baseline


class TestDetectDrift:
    def test_no_drift(self, detector):
        values = _make_history(30, base=0.90, noise=0.02)
        detector.compute_baseline(MetricType.ACCURACY, values)
        alert = detector.detect_drift(MetricType.ACCURACY, current_value=0.90)
        assert alert is None

    def test_warning_drift_higher_is_better(self, detector):
        values = _make_history(30, base=0.90, noise=0.005)
        detector.compute_baseline(MetricType.ACCURACY, values)
        alert = detector.detect_drift(MetricType.ACCURACY, current_value=0.80)
        assert alert is not None
        assert alert.severity in (DriftSeverity.WARNING, DriftSeverity.CRITICAL)
        assert alert.current_value == 0.80

    def test_critical_drift_higher_is_better(self, detector):
        values = [0.90] * 30
        detector.compute_baseline(MetricType.ACCURACY, values)
        alert = detector.detect_drift(MetricType.ACCURACY, current_value=0.50)
        assert alert is not None
        assert alert.severity == DriftSeverity.CRITICAL

    def test_warning_drift_lower_is_better(self, detector):
        values = _make_history(30, base=200.0, noise=5.0)
        detector.compute_baseline(MetricType.LATENCY_P99, values)
        alert = detector.detect_drift(MetricType.LATENCY_P99, current_value=300.0)
        assert alert is not None
        assert alert.severity in (DriftSeverity.WARNING, DriftSeverity.CRITICAL)

    def test_critical_drift_lower_is_better(self, detector):
        values = [200.0] * 30
        detector.compute_baseline(MetricType.LATENCY_P99, values)
        alert = detector.detect_drift(MetricType.LATENCY_P99, current_value=500.0)
        assert alert is not None
        assert alert.severity == DriftSeverity.CRITICAL

    def test_no_baseline_returns_none(self, detector):
        alert = detector.detect_drift(MetricType.HALLUCINATION_RATE, current_value=0.01)
        assert alert is None

    def test_drift_alert_fields(self, detector):
        values = [0.90] * 30
        detector.compute_baseline(MetricType.ACCURACY, values)
        alert = detector.detect_drift(MetricType.ACCURACY, current_value=0.50)
        assert alert.metric_type == MetricType.ACCURACY
        assert alert.baseline_mean == 0.90
        assert alert.deviation > 0
        assert alert.deviation_pct > 0
        assert alert.timestamp != ""
        assert alert.message != ""


class TestRunDetection:
    def test_run_detection_no_drift(self, detector):
        values = _make_history(30, base=0.90, noise=0.02)
        detector.compute_baseline(MetricType.ACCURACY, values)

        report = detector.run_detection(
            current_metrics={MetricType.ACCURACY: 0.90},
            test_data_version="2.0",
        )
        assert report.overall_status == DriftSeverity.NONE
        assert len(report.alerts) == 0
        assert len(report.snapshots) == 1
        assert report.test_data_version == "2.0"

    def test_run_detection_with_drift(self, detector):
        values = [0.90] * 30
        detector.compute_baseline(MetricType.ACCURACY, values)
        detector.compute_baseline(MetricType.LATENCY_P99, [200.0] * 30)

        report = detector.run_detection(
            current_metrics={
                MetricType.ACCURACY: 0.50,
                MetricType.LATENCY_P99: 200.0,
            },
        )
        assert report.overall_status in (DriftSeverity.WARNING, DriftSeverity.CRITICAL)
        assert len(report.alerts) >= 1

    def test_run_detection_with_organ_metrics(self, detector):
        values = [0.90] * 30
        detector.compute_baseline(MetricType.ACCURACY, values, organ="thyroid")

        report = detector.run_detection(
            current_metrics={MetricType.ACCURACY: 0.90},
            organ_metrics={
                "thyroid": {MetricType.ACCURACY: 0.50},
            },
        )
        assert len(report.alerts) >= 1
        assert any(a.organ == "thyroid" for a in report.alerts)

    def test_run_detection_report_fields(self, detector):
        report = detector.run_detection(
            current_metrics={MetricType.ACCURACY: 0.90},
            test_data_version="2.0",
        )
        assert report.model_name == "test_model"
        assert report.model_version == "1.0"
        assert report.test_timestamp != ""
        assert report.test_data_version == "2.0"


class TestReportSerialization:
    def test_report_to_dict(self, detector):
        values = [0.90] * 30
        detector.compute_baseline(MetricType.ACCURACY, values)

        report = detector.run_detection(
            current_metrics={MetricType.ACCURACY: 0.50},
        )
        d = detector.report_to_dict(report)
        assert "model_name" in d
        assert "overall_status" in d
        assert "alerts" in d
        assert "snapshots" in d
        assert "baselines" in d
        assert d["model_name"] == "test_model"

    def test_report_to_json(self, detector):
        report = detector.run_detection(
            current_metrics={MetricType.ACCURACY: 0.90},
        )
        json_str = detector.report_to_json(report)
        parsed = json.loads(json_str)
        assert "model_name" in parsed
        assert "alerts" in parsed

    def test_report_to_dict_alert_fields(self, detector):
        values = [0.90] * 30
        detector.compute_baseline(MetricType.ACCURACY, values)

        report = detector.run_detection(
            current_metrics={MetricType.ACCURACY: 0.50},
        )
        if report.alerts:
            d = detector.report_to_dict(report)
            alert_dict = d["alerts"][0]
            assert "severity" in alert_dict
            assert "metric_type" in alert_dict
            assert "current_value" in alert_dict
            assert "baseline_mean" in alert_dict
            assert "deviation" in alert_dict
            assert "message" in alert_dict


class TestEdgeCases:
    def test_zero_std_dev(self, detector):
        values = [0.90] * 30
        baseline = detector.compute_baseline(MetricType.ACCURACY, values)
        assert baseline is not None
        assert baseline.std_dev == 0.0
        alert = detector.detect_drift(MetricType.ACCURACY, current_value=0.90)
        assert alert is None

    def test_organ_isolation(self, detector):
        values_thyroid = [0.90] * 35
        values_liver = [0.80] * 35
        detector.compute_baseline(MetricType.ACCURACY, values_thyroid, organ="thyroid")
        detector.compute_baseline(MetricType.ACCURACY, values_liver, organ="liver")

        alert_thyroid = detector.detect_drift(MetricType.ACCURACY, 0.90, organ="thyroid")
        alert_liver = detector.detect_drift(MetricType.ACCURACY, 0.90, organ="liver")
        assert alert_thyroid is None
        assert alert_liver is None

    def test_multiple_metrics(self, detector):
        detector.compute_baseline(MetricType.ACCURACY, [0.90] * 35)
        detector.compute_baseline(MetricType.LATENCY_P99, [200.0] * 35)
        detector.compute_baseline(MetricType.HALLUCINATION_RATE, [0.001] * 35)

        report = detector.run_detection(
            current_metrics={
                MetricType.ACCURACY: 0.90,
                MetricType.LATENCY_P99: 200.0,
                MetricType.HALLUCINATION_RATE: 0.001,
            },
        )
        assert report.overall_status == DriftSeverity.NONE
        assert len(report.snapshots) == 3

    def test_special_chars_in_model_name(self, tmp_baseline_dir):
        det = DriftDetector(
            model_name="org/model-v2.0",
            baseline_dir=tmp_baseline_dir,
        )
        baseline = PerformanceBaseline(
            metric_type=MetricType.ACCURACY,
            mean=0.90,
            std_dev=0.02,
            sample_size=20,
            warning_threshold=0.86,
            critical_threshold=0.84,
        )
        assert det.save_baseline(baseline) is True
        loaded = det.load_baseline(MetricType.ACCURACY)
        assert loaded is not None


class TestHallucinationRateAbsoluteThreshold:
    def test_hallucination_rate_uses_absolute_threshold(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        values = [0.003, 0.004, 0.005, 0.006, 0.007,
                  0.003, 0.004, 0.005, 0.006, 0.007] * 3
        baseline = det.compute_baseline(MetricType.HALLUCINATION_RATE, values)
        assert baseline is not None
        assert baseline.warning_threshold == 0.003
        assert baseline.critical_threshold == 0.005

    def test_hallucination_rate_no_drift(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        values = [0.003, 0.004, 0.005, 0.006, 0.007,
                  0.003, 0.004, 0.005, 0.006, 0.007] * 3
        det.compute_baseline(MetricType.HALLUCINATION_RATE, values)
        alert = det.detect_drift(MetricType.HALLUCINATION_RATE, 0.003)
        assert alert is None

    def test_hallucination_rate_warning_drift(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        values = [0.003, 0.004, 0.005, 0.006, 0.007,
                  0.003, 0.004, 0.005, 0.006, 0.007] * 3
        det.compute_baseline(MetricType.HALLUCINATION_RATE, values)
        alert = det.detect_drift(MetricType.HALLUCINATION_RATE, 0.004)
        assert alert is not None
        assert alert.severity == DriftSeverity.WARNING

    def test_hallucination_rate_critical_drift(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        values = [0.003, 0.004, 0.005, 0.006, 0.007,
                  0.003, 0.004, 0.005, 0.006, 0.007] * 3
        det.compute_baseline(MetricType.HALLUCINATION_RATE, values)
        alert = det.detect_drift(MetricType.HALLUCINATION_RATE, 0.006)
        assert alert is not None
        assert alert.severity == DriftSeverity.CRITICAL


class TestGradingConsistencyMetric:
    def test_compute_baseline_grading_consistency(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        values = [0.94, 0.95, 0.96, 0.95, 0.94,
                  0.95, 0.96, 0.95, 0.94, 0.95] * 3
        baseline = det.compute_baseline(MetricType.GRADING_CONSISTENCY, values)
        assert baseline is not None
        assert baseline.mean > 0.9
        assert baseline.warning_threshold < baseline.mean
        assert baseline.critical_threshold < baseline.warning_threshold

    def test_detect_drift_grading_consistency(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        values = [0.94, 0.95, 0.96, 0.95, 0.94,
                  0.95, 0.96, 0.95, 0.94, 0.95] * 3
        det.compute_baseline(MetricType.GRADING_CONSISTENCY, values)
        alert = det.detect_drift(MetricType.GRADING_CONSISTENCY, 0.80)
        assert alert is not None
        assert alert.severity in [DriftSeverity.WARNING, DriftSeverity.CRITICAL]


class TestLoadCorruptedBaseline:
    def test_load_corrupted_json(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        baseline_path = Path(tmp_baseline_dir) / "test_all_accuracy.json"
        baseline_path.write_text("{invalid json content", encoding="utf-8")
        result = det.load_baseline(MetricType.ACCURACY)
        assert result is None

    def test_load_partial_json(self, tmp_baseline_dir):
        det = DriftDetector(model_name="test", baseline_dir=tmp_baseline_dir)
        baseline_path = Path(tmp_baseline_dir) / "accuracy_all.json"
        baseline_path.write_text('{"metric_type": "accuracy"', encoding="utf-8")
        result = det.load_baseline(MetricType.ACCURACY)
        assert result is None
