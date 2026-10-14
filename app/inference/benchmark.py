import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.inference.hallucination_detector import detect_hallucination as unified_detect_hallucination
from app.inference.protocols import InferenceEngine, InferenceRequest
from app.inference.utils import percentile as _percentile

logger = logging.getLogger(__name__)


@dataclass
class ModelCandidate:
    name: str
    engine_type: str
    model_path_or_id: str
    quantization: str = ""
    parameter_count: str = ""
    context_length: int = 0


@dataclass
class BenchmarkResult:
    candidate: ModelCandidate
    total_cases: int = 0
    correct_count: int = 0
    accuracy: float = 0.0
    avg_latency_ms: float = 0.0
    p50_latency_ms: float = 0.0
    p99_latency_ms: float = 0.0
    max_latency_ms: float = 0.0
    hallucination_count: int = 0
    hallucination_rate: float = 0.0
    baseline_hallucination_rate: float = 0.0
    errors: list[str] = field(default_factory=list)
    organ_breakdown: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def quantized_hallucination_increment(self) -> float:
        return max(0.0, self.hallucination_rate - self.baseline_hallucination_rate)


@dataclass
class SensitivityAnalysisResult:
    """敏感性分析结果"""
    metric_name: str
    threshold: float
    passing_rate: float
    passed_count: int
    total_count: int
    meets_threshold: bool


@dataclass
class BenchmarkReport:
    candidates: list[BenchmarkResult] = field(default_factory=list)
    test_set_size: int = 0
    test_set_name: str = ""
    winner: str = ""
    elapsed_seconds: float = 0.0
    sensitivity_analysis: dict[str, list[SensitivityAnalysisResult]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_set_name": self.test_set_name,
            "test_set_size": self.test_set_size,
            "winner": self.winner,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "candidates": [
                {
                    "name": r.candidate.name,
                    "engine_type": r.candidate.engine_type,
                    "quantization": r.candidate.quantization,
                    "accuracy": f"{r.accuracy:.4f}",
                    "avg_latency_ms": round(r.avg_latency_ms, 2),
                    "p99_latency_ms": round(r.p99_latency_ms, 2),
                    "hallucination_rate": f"{r.hallucination_rate:.4f}",
                    "quantized_hallucination_increment": f"{r.quantized_hallucination_increment:.4f}",
                    "organ_breakdown": r.organ_breakdown,
                    "errors": r.errors,
                }
                for r in self.candidates
            ],
            "sensitivity_analysis": {
                metric: [
                    {
                        "threshold": r.threshold,
                        "passing_rate": f"{r.passing_rate:.4f}",
                        "passed_count": r.passed_count,
                        "total_count": r.total_count,
                        "meets_threshold": r.meets_threshold,
                    }
                    for r in results
                ]
                for metric, results in self.sensitivity_analysis.items()
            },
        }


class ModelBenchmark:
    def __init__(
        self,
        concurrency: int = 2,
        request_timeout_seconds: float = 30.0,
        warmup_rounds: int = 3,
    ):
        self._concurrency = concurrency
        self._request_timeout = request_timeout_seconds
        self._warmup_rounds = warmup_rounds
        self._log = structlog.get_logger("app.inference.benchmark")
        from app.inference.verification_config import get_verification_config
        self._thresholds = get_verification_config().benchmark_thresholds

    async def benchmark_candidate(
        self,
        engine: InferenceEngine,
        candidate: ModelCandidate,
        test_cases: list[dict[str, Any]],
        grading_extractor: Any = None,
        baseline_hallucination_rate: float = 0.0,
    ) -> BenchmarkResult:
        result = BenchmarkResult(
            candidate=candidate,
            baseline_hallucination_rate=baseline_hallucination_rate,
        )

        self._log.info(
            "benchmark_started",
            candidate=candidate.name,
            engine_type=candidate.engine_type,
            test_cases=len(test_cases),
        )

        for _ in range(self._warmup_rounds):
            try:
                warmup_req = InferenceRequest(
                    prompt="测试",
                    max_tokens=32,
                    temperature=0.05,
                )
                await asyncio.wait_for(
                    engine.generate(warmup_req),
                    timeout=self._request_timeout,
                )
            except Exception as e:
                self._log.warning("warmup_request_failed", error=str(e))

        semaphore = asyncio.Semaphore(self._concurrency)
        latencies: list[float] = []
        correct = 0
        hallucination = 0
        organ_stats: dict[str, dict[str, int]] = {}

        async def _run_single(case: dict[str, Any]) -> dict[str, Any]:
            async with semaphore:
                return await self._benchmark_single(
                    engine, case, grading_extractor
                )

        tasks = [_run_single(c) for c in test_cases]
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)

        for i, outcome in enumerate(outcomes):
            case = test_cases[i]
            organ = case.get("organ", "unknown")

            if isinstance(outcome, Exception):
                result.errors.append(f"case {case.get('case_id', i)}: {outcome}")
                organ_stats.setdefault(organ, {"total": 0, "correct": 0, "hallucination": 0})
                organ_stats[organ]["total"] += 1
                continue

            latency = outcome.get("latency_ms", 0.0)
            is_correct = outcome.get("is_correct", False)
            is_hallucination = outcome.get("is_hallucination", False)

            if latency is not None:
                latencies.append(latency)
            if is_correct:
                correct += 1
            if is_hallucination:
                hallucination += 1

            organ_stats.setdefault(organ, {"total": 0, "correct": 0, "hallucination": 0})
            organ_stats[organ]["total"] += 1
            if is_correct:
                organ_stats[organ]["correct"] += 1
            if is_hallucination:
                organ_stats[organ]["hallucination"] += 1

        result.total_cases = len(test_cases)
        result.correct_count = correct
        result.accuracy = correct / len(test_cases) if test_cases else 0.0
        result.hallucination_count = hallucination
        result.hallucination_rate = hallucination / len(test_cases) if test_cases else 0.0

        if latencies:
            sorted_lat = sorted(latencies)
            result.avg_latency_ms = sum(latencies) / len(latencies)
            result.p50_latency_ms = _percentile(sorted_lat, 50)
            result.p99_latency_ms = _percentile(sorted_lat, 99)
            result.max_latency_ms = sorted_lat[-1]

        for organ_name, stats in organ_stats.items():
            total = stats["total"]
            result.organ_breakdown[organ_name] = {
                "total": total,
                "correct": stats["correct"],
                "accuracy": stats["correct"] / total if total > 0 else 0.0,
                "hallucination_rate": stats["hallucination"] / total if total > 0 else 0.0,
            }

        self._log.info(
            "benchmark_completed",
            candidate=candidate.name,
            accuracy=f"{result.accuracy:.2%}",
            p99_latency=f"{result.p99_latency_ms:.0f}ms",
            hallucination_rate=f"{result.hallucination_rate:.2%}",
        )

        return result

    async def _benchmark_single(
        self,
        engine: InferenceEngine,
        case: dict[str, Any],
        grading_extractor: Any = None,
    ) -> dict[str, Any]:
        prompt = case.get("prompt", "")
        expected = case.get("expected_grading") or case.get("expected_answer", "")
        is_hallucination_check = case.get("check_hallucination", True)

        request = InferenceRequest(
            prompt=prompt,
            max_tokens=512,
            temperature=0.05,
            top_p=0.95,
            request_id=f"benchmark-{case.get('case_id', '')}",
        )

        start = time.monotonic()
        try:
            response = await asyncio.wait_for(
                engine.generate(request),
                timeout=self._request_timeout,
            )
            latency_ms = (time.monotonic() - start) * 1000
        except Exception as exc:
            logger.warning("ModelBenchmark._benchmark_single: 未预期异常，按原逻辑降级", exc_info=True)
            return {
                "latency_ms": None,
                "is_correct": False,
                "is_hallucination": False,
                "error": str(exc),
            }

        text = response.text
        is_correct = False
        if expected and text:
            from app.inference.grading_definitions import extract_grading
            extracted = extract_grading(text, case.get("organ", ""))
            if extracted:
                is_correct = extracted == expected
            elif grading_extractor:
                extracted_custom = grading_extractor(text, case.get("organ", ""))
                if extracted_custom:
                    is_correct = extracted_custom == expected

        is_hallucination = False
        if is_hallucination_check and text:
            is_hallucination = self._detect_hallucination(text, case)

        return {
            "latency_ms": latency_ms,
            "is_correct": is_correct,
            "is_hallucination": is_hallucination,
        }

    @staticmethod
    def _detect_hallucination(text: str, case: dict[str, Any]) -> bool:
        organ = case.get("organ", "")
        return unified_detect_hallucination(text, organ)

    def determine_winner(self, results: list[BenchmarkResult]) -> str:
        if not results:
            self._log.warning("no_candidates_provided")
            return ""

        eligible: list[BenchmarkResult] = []
        for r in results:
            if (
                r.accuracy >= self._thresholds.accuracy
                and r.p99_latency_ms <= self._thresholds.latency_p99_ms
                and r.quantized_hallucination_increment <= self._thresholds.hallucination_increment
            ):
                eligible.append(r)

        if not eligible:
            self._log.warning("no_candidate_meets_all_thresholds")
            safe_candidates = [r for r in results if r.hallucination_rate < 0.05]
            if not safe_candidates:
                safe_candidates = results
            best_by_accuracy = max(safe_candidates, key=lambda r: r.accuracy)
            return best_by_accuracy.candidate.name

        if len(eligible) == 1:
            return eligible[0].candidate.name

        eligible.sort(
            key=lambda r: (-r.accuracy, r.p99_latency_ms, r.hallucination_rate),
        )
        return eligible[0].candidate.name

    def generate_report(
        self,
        results: list[BenchmarkResult],
        test_set_name: str = "",
        test_set_size: int = 0,
    ) -> BenchmarkReport:
        winner = self.determine_winner(results)
        return BenchmarkReport(
            candidates=results,
            test_set_size=test_set_size,
            test_set_name=test_set_name,
            winner=winner,
        )

    def analyze_threshold_sensitivity(
        self,
        results: list[BenchmarkResult],
    ) -> dict[str, list[SensitivityAnalysisResult]]:
        """
        分析不同阈值下的模型表现

        Args:
            results: 基准测试结果列表

        Returns:
            dict: 各指标在不同阈值下的敏感性分析结果
        """
        sensitivity_results: dict[str, list[SensitivityAnalysisResult]] = {}

        from app.inference.verification_config import get_verification_config
        config = get_verification_config()
        bt = config.benchmark_thresholds

        # 准确率敏感性分析
        accuracy_thresholds = bt.accuracy_sensitivity_thresholds
        accuracy_results = []
        for threshold in accuracy_thresholds:
            passed = sum(1 for r in results if r.accuracy >= threshold)
            total = len(results)
            accuracy_results.append(SensitivityAnalysisResult(
                metric_name="accuracy",
                threshold=threshold,
                passing_rate=passed / total if total > 0 else 0.0,
                passed_count=passed,
                total_count=total,
                meets_threshold=passed == total,
            ))
        sensitivity_results["accuracy"] = accuracy_results

        # 延迟敏感性分析
        latency_thresholds = bt.latency_sensitivity_thresholds_ms
        latency_results = []
        for threshold in latency_thresholds:
            passed = sum(1 for r in results if r.p99_latency_ms <= threshold)
            total = len(results)
            latency_results.append(SensitivityAnalysisResult(
                metric_name="latency_p99",
                threshold=threshold,
                passing_rate=passed / total if total > 0 else 0.0,
                passed_count=passed,
                total_count=total,
                meets_threshold=passed == total,
            ))
        sensitivity_results["latency_p99"] = latency_results

        # 幻觉率敏感性分析（使用量化幻觉增量以与determine_winner一致）
        hallucination_thresholds = bt.hallucination_sensitivity_thresholds
        hallucination_results = []
        for threshold in hallucination_thresholds:
            passed = sum(1 for r in results if r.quantized_hallucination_increment <= threshold)
            total = len(results)
            hallucination_results.append(SensitivityAnalysisResult(
                metric_name="hallucination_increment",
                threshold=threshold,
                passing_rate=passed / total if total > 0 else 0.0,
                passed_count=passed,
                total_count=total,
                meets_threshold=passed == total,
            ))
        sensitivity_results["hallucination_increment"] = hallucination_results

        self._log.info(
            "threshold_sensitivity_analysis_completed",
            metrics_analyzed=list(sensitivity_results.keys()),
        )

        return sensitivity_results
