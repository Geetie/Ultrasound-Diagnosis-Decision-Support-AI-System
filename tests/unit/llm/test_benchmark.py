
from app.inference.benchmark import (
    BenchmarkReport,
    BenchmarkResult,
    ModelBenchmark,
    ModelCandidate,
    SensitivityAnalysisResult,
)
from app.inference.protocols import InferenceEngine, InferenceRequest, InferenceResponse, TokenUsage

import pytest
pytestmark = [pytest.mark.track_llm, pytest.mark.track_inf]


class TestModelCandidate:
    def test_default_values(self):
        candidate = ModelCandidate(
            name="Qwen2.5-7B",
            engine_type="llama.cpp",
            model_path_or_id="test-model",
        )
        assert candidate.quantization == ""
        assert candidate.parameter_count == ""
        assert candidate.context_length == 0


class TestBenchmarkResult:
    def test_default_values(self):
        candidate = ModelCandidate(
            name="test",
            engine_type="llama.cpp",
            model_path_or_id="test",
        )
        result = BenchmarkResult(candidate=candidate)
        assert result.total_cases == 0
        assert result.accuracy == 0.0
        assert result.hallucination_rate == 0.0
        assert result.errors == []

    def test_quantized_hallucination_increment(self):
        candidate = ModelCandidate(
            name="test",
            engine_type="llama.cpp",
            model_path_or_id="test",
        )
        result = BenchmarkResult(
            candidate=candidate,
            hallucination_rate=0.008,
            baseline_hallucination_rate=0.003,
        )
        assert result.quantized_hallucination_increment == 0.005

    def test_quantized_hallucination_increment_negative(self):
        candidate = ModelCandidate(
            name="test",
            engine_type="llama.cpp",
            model_path_or_id="test",
        )
        result = BenchmarkResult(
            candidate=candidate,
            hallucination_rate=0.002,
            baseline_hallucination_rate=0.005,
        )
        assert result.quantized_hallucination_increment == 0.0


class TestBenchmarkReport:
    def test_to_dict(self):
        candidate = ModelCandidate(
            name="Qwen2.5-7B",
            engine_type="llama.cpp",
            model_path_or_id="test",
            quantization="Q4_K_M",
        )
        result = BenchmarkResult(
            candidate=candidate,
            total_cases=100,
            correct_count=88,
            accuracy=0.88,
            p99_latency_ms=2500.0,
        )
        report = BenchmarkReport(
            candidates=[result],
            test_set_size=100,
            test_set_name="test_set",
            winner="Qwen2.5-7B",
        )

        d = report.to_dict()
        assert d["test_set_name"] == "test_set"
        assert d["winner"] == "Qwen2.5-7B"
        assert len(d["candidates"]) == 1
        assert d["candidates"][0]["name"] == "Qwen2.5-7B"
        assert d["candidates"][0]["accuracy"] == "0.8800"


class TestModelBenchmark:
    def test_determine_winner_by_accuracy(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.88, p99_latency_ms=2000.0, hallucination_rate=0.003, baseline_hallucination_rate=0.0)

        c2 = ModelCandidate(name="B", engine_type="llama.cpp", model_path_or_id="b")
        r2 = BenchmarkResult(candidate=c2, accuracy=0.90, p99_latency_ms=2500.0, hallucination_rate=0.004, baseline_hallucination_rate=0.0)

        winner = benchmark.determine_winner([r1, r2])
        assert winner == "B"

    def test_determine_winner_latency_disqualified(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.90, p99_latency_ms=2000.0, hallucination_rate=0.003, baseline_hallucination_rate=0.0)

        c2 = ModelCandidate(name="B", engine_type="llama.cpp", model_path_or_id="b")
        r2 = BenchmarkResult(candidate=c2, accuracy=0.92, p99_latency_ms=5000.0, hallucination_rate=0.004, baseline_hallucination_rate=0.0)

        winner = benchmark.determine_winner([r1, r2])
        assert winner == "A"

    def test_determine_winner_all_disqualified(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.70, p99_latency_ms=5000.0, hallucination_rate=0.01, baseline_hallucination_rate=0.0)

        winner = benchmark.determine_winner([r1])
        assert winner == "A"

    def test_generate_report(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.88, p99_latency_ms=2000.0, hallucination_rate=0.003, baseline_hallucination_rate=0.0)

        report = benchmark.generate_report([r1], test_set_name="test", test_set_size=100)
        assert report.test_set_name == "test"
        assert report.test_set_size == 100
        assert len(report.candidates) == 1

    def test_hallucination_detection(self):
        benchmark = ModelBenchmark()

        case = {
            "organ": "thyroid",
            "prompt": "test",
            "check_hallucination": True,
        }

        assert benchmark._detect_hallucination("甲状腺结节TI-RADS 4a", case) is False
        assert benchmark._detect_hallucination("甲状腺结节伴肺癌转移", case) is True

    def test_hallucination_detection_forbidden_terms_by_organ(self):
        benchmark = ModelBenchmark()
        liver_case = {"organ": "liver", "prompt": "test"}
        assert benchmark._detect_hallucination("肝癌伴甲状腺癌转移", liver_case) is True
        assert benchmark._detect_hallucination("肝血管瘤", liver_case) is False


class _MockBenchmarkEngine(InferenceEngine):
    def __init__(self, responses: list[str] | None = None, latency_ms: float = 100.0):
        self._responses = responses or []
        self._call_count = 0
        self._latency_ms = latency_ms

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        if self._call_count < len(self._responses):
            text = self._responses[self._call_count]
        else:
            text = "TI-RADS 3类"
        self._call_count += 1
        return InferenceResponse(
            text=text,
            model="mock",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            finish_reason="stop",
            request_id=request.request_id,
            latency_ms=self._latency_ms,
        )

    async def health_check(self) -> bool:
        return True

    async def get_model_info(self):
        from app.inference.protocols import ModelInfo
        return ModelInfo(model_name="mock")

    async def close(self) -> None:
        pass


class TestBenchmarkCandidate:
    @pytest.mark.asyncio
    async def test_benchmark_candidate_basic(self):
        engine = _MockBenchmarkEngine(
            responses=["TI-RADS 4a类", "TI-RADS 3类", "LI-RADS 4类"],
        )
        candidate = ModelCandidate(
            name="test-model",
            engine_type="llama.cpp",
            model_path_or_id="test",
        )
        benchmark = ModelBenchmark(warmup_rounds=0)

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"},
            {"case_id": "002", "organ": "thyroid", "prompt": "test", "expected_grading": "3"},
            {"case_id": "003", "organ": "liver", "prompt": "test", "expected_grading": "4"},
        ]

        result = await benchmark.benchmark_candidate(engine, candidate, cases)

        assert result.total_cases == 3
        assert result.candidate.name == "test-model"
        assert result.accuracy >= 0.0
        assert result.avg_latency_ms >= 0.0

        await engine.close()

    @pytest.mark.asyncio
    async def test_benchmark_candidate_with_errors(self):
        engine = _MockBenchmarkEngine(responses=["TI-RADS 3类"])
        candidate = ModelCandidate(name="test", engine_type="llama.cpp", model_path_or_id="test")
        benchmark = ModelBenchmark(warmup_rounds=0, request_timeout_seconds=0.001)

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "3"},
        ]

        result = await benchmark.benchmark_candidate(engine, candidate, cases)
        assert result.total_cases == 1

        await engine.close()

    @pytest.mark.asyncio
    async def test_benchmark_candidate_organ_breakdown(self):
        engine = _MockBenchmarkEngine(
            responses=["TI-RADS 4a类", "LI-RADS 3类"],
        )
        candidate = ModelCandidate(name="test", engine_type="llama.cpp", model_path_or_id="test")
        benchmark = ModelBenchmark(warmup_rounds=0)

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"},
            {"case_id": "002", "organ": "liver", "prompt": "test", "expected_grading": "3"},
        ]

        result = await benchmark.benchmark_candidate(engine, candidate, cases)

        assert "thyroid" in result.organ_breakdown
        assert "liver" in result.organ_breakdown
        assert result.organ_breakdown["thyroid"]["total"] == 1
        assert result.organ_breakdown["liver"]["total"] == 1

        await engine.close()

    @pytest.mark.asyncio
    async def test_benchmark_candidate_cardiac_organ_breakdown(self):
        engine = _MockBenchmarkEngine(
            responses=["LVEF 48%，轻度减低", "TI-RADS 3类", "LI-RADS 4类"],
        )
        candidate = ModelCandidate(name="test", engine_type="llama.cpp", model_path_or_id="test")
        benchmark = ModelBenchmark(warmup_rounds=0)

        cases = [
            {"case_id": "001", "organ": "cardiac", "prompt": "test", "expected_grading": "mild"},
            {"case_id": "002", "organ": "thyroid", "prompt": "test", "expected_grading": "3"},
            {"case_id": "003", "organ": "liver", "prompt": "test", "expected_grading": "4"},
        ]

        result = await benchmark.benchmark_candidate(engine, candidate, cases)

        assert "cardiac" in result.organ_breakdown
        assert "thyroid" in result.organ_breakdown
        assert "liver" in result.organ_breakdown
        assert result.organ_breakdown["cardiac"]["total"] == 1

        await engine.close()

    @pytest.mark.asyncio
    async def test_benchmark_candidate_with_hallucination_check(self):
        engine = _MockBenchmarkEngine(
            responses=["甲状腺结节伴肺癌转移"],
        )
        candidate = ModelCandidate(name="test", engine_type="llama.cpp", model_path_or_id="test")
        benchmark = ModelBenchmark(warmup_rounds=0)

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "check_hallucination": True},
        ]

        result = await benchmark.benchmark_candidate(engine, candidate, cases)

        assert result.hallucination_count >= 1
        assert result.hallucination_rate > 0.0

        await engine.close()


class TestAnalyzeThresholdSensitivity:
    def test_accuracy_sensitivity(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.88, p99_latency_ms=2000.0, hallucination_rate=0.003)

        c2 = ModelCandidate(name="B", engine_type="llama.cpp", model_path_or_id="b")
        r2 = BenchmarkResult(candidate=c2, accuracy=0.92, p99_latency_ms=2500.0, hallucination_rate=0.004)

        results = benchmark.analyze_threshold_sensitivity([r1, r2])

        assert "accuracy" in results
        assert "latency_p99" in results
        assert "hallucination_increment" in results

        accuracy_results = results["accuracy"]
        assert len(accuracy_results) > 0
        for ar in accuracy_results:
            assert isinstance(ar, SensitivityAnalysisResult)
            assert ar.metric_name == "accuracy"
            assert 0.0 <= ar.passing_rate <= 1.0

    def test_sensitivity_at_high_threshold(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.88, p99_latency_ms=2000.0, hallucination_rate=0.003)

        results = benchmark.analyze_threshold_sensitivity([r1])

        accuracy_results = results["accuracy"]
        high_threshold = [r for r in accuracy_results if r.threshold >= 0.90]
        assert len(high_threshold) > 0
        for r in high_threshold:
            assert r.passing_rate == 0.0

    def test_sensitivity_at_low_threshold(self):
        benchmark = ModelBenchmark()

        c1 = ModelCandidate(name="A", engine_type="llama.cpp", model_path_or_id="a")
        r1 = BenchmarkResult(candidate=c1, accuracy=0.88, p99_latency_ms=2000.0, hallucination_rate=0.003)

        results = benchmark.analyze_threshold_sensitivity([r1])

        accuracy_results = results["accuracy"]
        low_threshold = [r for r in accuracy_results if r.threshold <= 0.80]
        assert len(low_threshold) > 0
        for r in low_threshold:
            assert r.passing_rate == 1.0
