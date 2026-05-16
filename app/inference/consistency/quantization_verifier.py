from dataclasses import dataclass
from typing import Optional

from app.inference.verification_config import BenchmarkThresholdConfig


@dataclass
class QuantizationResult:
    baseline_rate: float
    test_rate: float
    increment: float
    threshold: float
    passed: bool


class QuantizationHallucinationVerifier:
    def __init__(
        self,
        threshold: float = BenchmarkThresholdConfig.model_fields["hallucination_increment"].default,
    ):
        self._threshold = threshold

    def verify(
        self,
        baseline_hallucination_rate: float,
        test_hallucination_rate: float,
    ) -> QuantizationResult:
        increment = test_hallucination_rate - baseline_hallucination_rate
        return QuantizationResult(
            baseline_rate=baseline_hallucination_rate,
            test_rate=test_hallucination_rate,
            increment=increment,
            threshold=self._threshold,
            passed=increment <= self._threshold,
        )

    def verify_and_update_report(
        self,
        baseline_hallucination_rate: float,
        test_hallucination_rate: float,
        report: Optional[object] = None,
    ) -> QuantizationResult:
        result = self.verify(baseline_hallucination_rate, test_hallucination_rate)
        if report is not None:
            report.quantization_hallucination_increment = result.increment
            report.quantization_baseline_rate = result.baseline_rate
            report.quantization_test_rate = result.test_rate
            report.quantization_increment_exceeded = not result.passed
        return result
