"""
验证新功能实现的测试脚本
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

def test_imports():
    """测试所有新模块和类可以正确导入"""
    print("Testing imports...")

    try:
        from app.inference.consistency import (
            ConsistencyVerifier,
            ConsistencyReport,
            ConsistencyPair,
            FalseStats,
            CrossValidationResult,
        )
        print("✓ consistency.py imports successful")

        from app.inference.benchmark import (
            ModelBenchmark,
            BenchmarkReport,
            SensitivityAnalysisResult,
        )
        print("✓ benchmark.py imports successful")

        from app.inference.verification_config import (
            get_verification_config,
            VerificationConfig,
        )
        print("✓ verification_config.py imports successful")

        from app.inference.hallucination_detector import (
            HallucinationDetector,
            DetectionStrategy,
        )
        print("✓ hallucination_detector.py imports successful")

    except Exception as e:
        print(f"✗ Import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_false_stats():
    """测试FalseStats计算"""
    print("\nTesting FalseStats...")

    try:
        from app.inference.consistency import FalseStats

        stats = FalseStats(
            false_positives=5,
            false_negatives=3,
            true_positives=10,
            true_negatives=82,
        )

        assert stats.precision == 10 / (10 + 5), "Precision calculation failed"
        assert stats.recall == 10 / (10 + 3), "Recall calculation failed"
        assert stats.f1_score == 2 * (stats.precision * stats.recall) / (stats.precision + stats.recall), "F1 score calculation failed"

        print(f"✓ FalseStats precision: {stats.precision:.4f}")
        print(f"✓ FalseStats recall: {stats.recall:.4f}")
        print(f"✓ FalseStats F1: {stats.f1_score:.4f}")

    except Exception as e:
        print(f"✗ FalseStats test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_cross_validation_result():
    """测试CrossValidationResult"""
    print("\nTesting CrossValidationResult...")

    try:
        from app.inference.consistency import CrossValidationResult

        cv_result = CrossValidationResult(
            fold=1,
            train_count=240,
            validation_count=60,
            train_accuracy=0.96,
            validation_accuracy=0.94,
            train_hallucination_rate=0.02,
            validation_hallucination_rate=0.025,
            accuracy_diff=0.02,
            hallucination_rate_diff=0.005,
        )

        assert cv_result.fold == 1
        assert cv_result.train_count == 240
        assert cv_result.validation_count == 60
        assert cv_result.accuracy_diff == 0.02

        print(f"✓ CrossValidationResult fold: {cv_result.fold}")
        print(f"✓ CrossValidationResult train/val: {cv_result.train_count}/{cv_result.validation_count}")
        print(f"✓ CrossValidationResult accuracy diff: {cv_result.accuracy_diff:.4f}")

    except Exception as e:
        print(f"✗ CrossValidationResult test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_sensitivity_analysis_result():
    """测试SensitivityAnalysisResult"""
    print("\nTesting SensitivityAnalysisResult...")

    try:
        from app.inference.benchmark import SensitivityAnalysisResult

        sensitivity = SensitivityAnalysisResult(
            metric_name="accuracy",
            threshold=0.95,
            passing_rate=0.8,
            passed_count=4,
            total_count=5,
            meets_threshold=False,
        )

        assert sensitivity.metric_name == "accuracy"
        assert sensitivity.threshold == 0.95
        assert sensitivity.passing_rate == 0.8

        print(f"✓ SensitivityAnalysisResult metric: {sensitivity.metric_name}")
        print(f"✓ SensitivityAnalysisResult threshold: {sensitivity.threshold}")
        print(f"✓ SensitivityAnalysisResult passing rate: {sensitivity.passing_rate:.2%}")

    except Exception as e:
        print(f"✗ SensitivityAnalysisResult test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_verification_config():
    """测试VerificationConfig包含新字段"""
    print("\nTesting VerificationConfig...")

    try:
        from app.inference.verification_config import get_verification_config

        config = get_verification_config()

        assert hasattr(config, 'test_data_version')
        assert hasattr(config, 'boundary_config')
        assert hasattr(config, 'forbidden_terms_config')
        assert hasattr(config, 'strategy_weights')
        assert hasattr(config, 'numerical_validation_config')

        print(f"✓ VerificationConfig test_data_version: {config.test_data_version}")
        print(f"✓ VerificationConfig boundary pairs (thyroid): {len(config.boundary_config.organ_boundaries.get('thyroid', []))}")
        print(f"✓ VerificationConfig forbidden terms (thyroid): {len(config.forbidden_terms_config.organ_terms.get('thyroid', []))}")

    except Exception as e:
        print(f"✗ VerificationConfig test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_consistency_report_has_failure_analysis():
    """测试ConsistencyReport包含failure_analysis字段"""
    print("\nTesting ConsistencyReport.failure_analysis...")

    try:
        from app.inference.consistency import ConsistencyReport

        report = ConsistencyReport(
            engine_a_name="engine_a",
            engine_b_name="engine_b",
            total_cases=100,
            grading_consistency_rate=0.95,
            clinical_boundary_diff_rate=0.01,
            hallucination_rate_diff=0.005,
            failure_analysis={"summary": {"total_failures": 5}},
        )

        assert hasattr(report, 'failure_analysis')
        assert "summary" in report.failure_analysis

        print(f"✓ ConsistencyReport has failure_analysis field")
        print(f"✓ ConsistencyReport failure_analysis summary: {report.failure_analysis['summary']}")

    except Exception as e:
        print(f"✗ ConsistencyReport.failure_analysis test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def test_benchmark_report_has_sensitivity_analysis():
    """测试BenchmarkReport包含sensitivity_analysis字段"""
    print("\nTesting BenchmarkReport.sensitivity_analysis...")

    try:
        from app.inference.benchmark import BenchmarkReport, SensitivityAnalysisResult

        sensitivity = SensitivityAnalysisResult(
            metric_name="accuracy",
            threshold=0.95,
            passing_rate=0.8,
            passed_count=4,
            total_count=5,
            meets_threshold=False,
        )

        report = BenchmarkReport(
            test_set_size=100,
            sensitivity_analysis={"accuracy": [sensitivity]},
        )

        assert hasattr(report, 'sensitivity_analysis')
        assert "accuracy" in report.sensitivity_analysis

        print(f"✓ BenchmarkReport has sensitivity_analysis field")
        print(f"✓ BenchmarkReport sensitivity_analysis metrics: {list(report.sensitivity_analysis.keys())}")

    except Exception as e:
        print(f"✗ BenchmarkReport.sensitivity_analysis test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    return True


def main():
    """运行所有测试"""
    print("=" * 60)
    print("Running verification tests for new implementations")
    print("=" * 60)

    tests = [
        test_imports,
        test_false_stats,
        test_cross_validation_result,
        test_sensitivity_analysis_result,
        test_verification_config,
        test_consistency_report_has_failure_analysis,
        test_benchmark_report_has_sensitivity_analysis,
    ]

    passed = 0
    failed = 0

    for test in tests:
        if test():
            passed += 1
        else:
            failed += 1

    print("\n" + "=" * 60)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 60)

    return failed == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
