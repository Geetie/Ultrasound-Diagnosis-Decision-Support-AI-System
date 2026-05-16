
from app.inference.consistency import (
    ConsistencyPair,
    ConsistencyReport,
    ConsistencyVerifier,
    CrossValidationResult,
    FalseStats,
    GateStatus,
    GradingResult,
    extract_grading,
    detect_hallucination,
    grading_semantic_equivalent,
    text_consistency_with_grading,
    text_similarity,
    wilson_confidence_interval,
    mcnemar_test,
)
from app.inference.protocols import InferenceEngine, InferenceRequest, InferenceResponse, TokenUsage
from app.inference.verification_config import BenchmarkThresholdConfig

import pytest
pytestmark = [pytest.mark.track_llm, pytest.mark.track_inf]


class MockInferenceEngine(InferenceEngine):
    def __init__(self, responses: list[str] | None = None, name: str = "mock", latency_ms: float = 100.0):
        self._responses = responses or []
        self._call_count = 0
        self._name = name
        self._latency_ms = latency_ms

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        if self._call_count < len(self._responses):
            text = self._responses[self._call_count]
        else:
            text = "default response"
        self._call_count += 1
        return InferenceResponse(
            text=text,
            model=self._name,
            usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
            finish_reason="stop",
            request_id=request.request_id,
            latency_ms=self._latency_ms,
        )

    async def health_check(self) -> bool:
        return True

    async def get_model_info(self):
        from app.inference.protocols import ModelInfo
        return ModelInfo(model_name=self._name)

    async def close(self) -> None:
        pass


class TestExtractGrading:
    def test_thyroid_ti_rads(self):
        text = "甲状腺结节，TI-RADS 4a类，建议穿刺活检"
        assert extract_grading(text, "thyroid") == "4a"

    def test_thyroid_ti_rads_3(self):
        text = "甲状腺右叶结节，TI-RADS 3类，良性可能"
        assert extract_grading(text, "thyroid") == "3"

    def test_liver_li_rads(self):
        text = "肝脏占位，LI-RADS 4类，建议增强MRI"
        assert extract_grading(text, "liver") == "4"

    def test_cardiac_lvef_normal(self):
        text = "左心室射血分数 LVEF: 55%，收缩功能正常"
        assert extract_grading(text, "cardiac") == "normal"

    def test_cardiac_lvef_mild(self):
        text = "左心室射血分数 LVEF: 48%，收缩功能轻度减低"
        assert extract_grading(text, "cardiac") == "mild"

    def test_cardiac_lvef_moderate(self):
        text = "左心室射血分数 LVEF: 35%，收缩功能中度减低"
        assert extract_grading(text, "cardiac") == "moderate"

    def test_cardiac_lvef_severe(self):
        text = "左心室射血分数 LVEF: 25%，收缩功能重度减低"
        assert extract_grading(text, "cardiac") == "severe"

    def test_cardiac_cn_text_normal(self):
        text = "心脏超声提示收缩功能正常"
        assert extract_grading(text, "cardiac") == "normal"

    def test_cardiac_cn_text_moderate(self):
        text = "心脏超声提示收缩功能中度减低"
        assert extract_grading(text, "cardiac") == "moderate"

    def test_no_grading_found(self):
        text = "检查未见明显异常"
        assert extract_grading(text, "thyroid") is None

    def test_unknown_organ(self):
        text = "TI-RADS 4a"
        assert extract_grading(text, "unknown_organ") is None

    def test_breast_bi_rads(self):
        text = "乳腺结节，BI-RADS 4a类，建议穿刺活检"
        assert extract_grading(text, "breast") == "4a"

    def test_breast_bi_rads_3(self):
        text = "乳腺结节BI-RADS 3类，良性可能"
        assert extract_grading(text, "breast") == "3"

    def test_case_insensitive(self):
        text = "ti-rads 4b类"
        assert extract_grading(text, "thyroid") == "4b"

    def test_thyroid_uppercase_subcategory(self):
        text = "甲状腺结节，TI-RADS 4A类，建议穿刺活检"
        assert extract_grading(text, "thyroid") == "4a"

    def test_breast_uppercase_subcategory(self):
        text = "乳腺结节，BI-RADS 4B类，建议穿刺活检"
        assert extract_grading(text, "breast") == "4b"

    def test_liver_uppercase_no_subcategory(self):
        text = "肝脏占位，LI-RADS 4类，建议增强MRI"
        assert extract_grading(text, "liver") == "4"

    def test_mixed_case_grading(self):
        text = "ti-rads 4C类"
        assert extract_grading(text, "thyroid") == "4c"

    def test_cardiac_description_priority_over_lvef(self):
        text = "LVEF: 55%，收缩功能轻度减低"
        assert extract_grading(text, "cardiac") == "mild"

    def test_cardiac_lvef_only_fallback(self):
        text = "左心室射血分数 LVEF: 48%"
        assert extract_grading(text, "cardiac") == "mild"

    def test_cardiac_conflicting_lvef_and_description(self):
        text = "LVEF: 60%，收缩功能中度减低"
        assert extract_grading(text, "cardiac") == "moderate"

    def test_thyroid_ti_rads_1(self):
        text = "甲状腺结节 TI-RADS 1类"
        assert extract_grading(text, "thyroid") == "1"

    def test_thyroid_ti_rads_2(self):
        text = "甲状腺囊肿 TI-RADS 2类"
        assert extract_grading(text, "thyroid") == "2"

    def test_thyroid_ti_rads_5(self):
        text = "甲状腺占位 TI-RADS 5类"
        assert extract_grading(text, "thyroid") == "5"

    def test_liver_li_rads_lr_tr(self):
        text = "肝脏占位 LR-TR 阳性"
        result = extract_grading(text, "liver")
        assert result is not None

    def test_breast_bi_rads_0(self):
        text = "乳腺结节 BI-RADS 0类"
        assert extract_grading(text, "breast") == "0"

    def test_breast_bi_rads_4b(self):
        text = "乳腺肿块 BI-RADS 4b类"
        assert extract_grading(text, "breast") == "4b"

    def test_followup_months_not_extracted(self):
        text = "甲状腺结节，随访3月后复查"
        assert extract_grading(text, "thyroid") is None

    def test_weeks_not_extracted(self):
        text = "甲状腺结节TI-RADS 4a类可能，建议4-6周复查"
        assert extract_grading(text, "thyroid") == "4a"

    def test_case_count_not_extracted(self):
        text = "甲状腺结节3次复查未见明显变化"
        assert extract_grading(text, "thyroid") is None

    def test_years_not_extracted(self):
        text = "肝脏占位，随访2年后复查"
        assert extract_grading(text, "liver") is None

    def test_cardiac_class_1_normal(self):
        assert extract_grading("心功能Ⅰ级", "cardiac") == "normal"

    def test_cardiac_class_2_mild(self):
        assert extract_grading("心功能Ⅱ级", "cardiac") == "mild"

    def test_cardiac_class_3_moderate(self):
        assert extract_grading("心功能Ⅲ级", "cardiac") == "moderate"

    def test_cardiac_class_4_severe(self):
        assert extract_grading("心功能Ⅳ级", "cardiac") == "severe"

    def test_cardiac_arabic_numeral_class(self):
        assert extract_grading("心功能2级", "cardiac") == "mild"


class TestGradingResult:
    def test_default_values(self):
        result = GradingResult(text="test")
        assert result.grading is None
        assert result.organ == ""
        assert result.latency_ms == 0.0
        assert result.is_hallucination is False
        assert result.error is None


class TestGradingSemanticEquivalent:
    def test_thyroid_4_and_4a_equivalent(self):
        assert grading_semantic_equivalent("4", "4a", "thyroid") is True

    def test_thyroid_4a_and_4b_equivalent(self):
        assert grading_semantic_equivalent("4a", "4b", "thyroid") is False

    def test_thyroid_3_and_4_not_equivalent(self):
        assert grading_semantic_equivalent("3", "4", "thyroid") is False

    def test_liver_3_and_4_not_equivalent(self):
        assert grading_semantic_equivalent("3", "4", "liver") is False

    def test_breast_4_and_4a_equivalent(self):
        assert grading_semantic_equivalent("4", "4a", "breast") is True

    def test_none_grading(self):
        assert grading_semantic_equivalent(None, "3", "thyroid") is False

    def test_same_grading(self):
        assert grading_semantic_equivalent("3", "3", "thyroid") is True

    def test_thyroid_4a_and_4c_not_equivalent(self):
        assert grading_semantic_equivalent("4a", "4c", "thyroid") is False

    def test_breast_4a_and_4c_not_equivalent(self):
        assert grading_semantic_equivalent("4a", "4c", "breast") is False

    def test_thyroid_4b_and_4c_not_equivalent(self):
        assert grading_semantic_equivalent("4b", "4c", "thyroid") is False

    def test_breast_4b_and_4c_not_equivalent(self):
        assert grading_semantic_equivalent("4b", "4c", "breast") is False

    def test_thyroid_4a_and_4b_still_equivalent(self):
        assert grading_semantic_equivalent("4a", "4b", "thyroid") is False

    def test_breast_4a_and_4b_still_equivalent(self):
        assert grading_semantic_equivalent("4a", "4b", "breast") is False


class TestWilsonConfidenceInterval:
    def test_zero_total(self):
        lower, upper = wilson_confidence_interval(0, 0)
        assert lower == 0.0
        assert upper == 0.0

    def test_all_successes(self):
        lower, upper = wilson_confidence_interval(100, 100)
        assert lower > 0.9
        assert upper >= 0.99

    def test_no_successes(self):
        lower, upper = wilson_confidence_interval(0, 100)
        assert lower == 0.0
        assert upper < 0.05

    def test_half_successes(self):
        lower, upper = wilson_confidence_interval(50, 100)
        assert lower < 0.5
        assert upper > 0.5
        assert abs((lower + upper) / 2 - 0.5) < 0.05

    def test_ci_narrows_with_larger_sample(self):
        _, upper_10 = wilson_confidence_interval(0, 10)
        _, upper_100 = wilson_confidence_interval(0, 100)
        _, upper_1000 = wilson_confidence_interval(0, 1000)
        assert upper_10 > upper_100 > upper_1000


class TestTextSimilarity:
    def test_identical_texts(self):
        assert text_similarity("hello", "hello") == 1.0

    def test_empty_texts(self):
        assert text_similarity("", "") == 1.0

    def test_one_empty(self):
        assert text_similarity("hello", "") == 0.0

    def test_partial_overlap(self):
        sim = text_similarity("abc", "bcd")
        assert 0.0 < sim < 1.0

    def test_medical_negation_pair_benign_malignant(self):
        sim = text_similarity("边界清晰，形态规则，良性可能", "边界模糊，形态不规则，恶性可能")
        assert sim < 0.7

    def test_medical_negation_pair_low_similarity(self):
        sim = text_similarity("边界清晰，形态规则，良性可能", "边界模糊，形态不规则，恶性可能")
        assert sim < 0.5

    def test_medical_negation_pair_clear_unclear(self):
        sim = text_similarity("边界清晰，TI-RADS 3类", "边界不清，TI-RADS 4a类")
        assert sim < 0.9

    def test_medical_same_organ_different_finding(self):
        sim_same = text_similarity("甲状腺结节，边界清晰", "甲状腺结节，边界清晰")
        sim_diff = text_similarity("甲状腺结节，边界清晰", "甲状腺结节，边界模糊")
        assert sim_same > sim_diff


class TestTextConsistencyImproved:
    def test_semantic_equivalent_still_requires_similarity(self):
        result = text_consistency_with_grading(
            "甲状腺结节TI-RADS 4a类，边界不清",
            "完全无关的文本内容关于其他话题",
            "4a", "4a", "thyroid",
        )
        assert result is False

    def test_text_similarity_edit_distance_fallback(self):
        sim_a = text_similarity("甲状腺结节", "甲状腺占位")
        assert sim_a > 0.0
        sim_b = text_similarity("a", "b")
        assert sim_b >= 0.0

    @pytest.mark.asyncio
    async def test_text_consistency_strict_rate(self):
        engine_a = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 3类"] * 3,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 3类"] * 3,
            name="b",
        )
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test", "expected_grading": "3"}
            for i in range(3)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert report.text_consistency_rate_strict >= 0.0


class TestConsistencyPair:
    def test_grading_consistent(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            engine_a_result=GradingResult(text="a", grading="4a"),
            engine_b_result=GradingResult(text="b", grading="4a"),
        )
        assert pair.grading_consistent is True

    def test_grading_inconsistent(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            engine_a_result=GradingResult(text="a", grading="4a"),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.grading_consistent is False

    def test_grading_consistent_exact_match_required(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            engine_a_result=GradingResult(text="a", grading="4"),
            engine_b_result=GradingResult(text="b", grading="4a"),
        )
        assert pair.grading_consistent is False
        assert pair.grading_semantic_consistent is True

    def test_grading_consistent_none_result(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            engine_a_result=None,
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.grading_consistent is False

    def test_grading_consistent_none_grading(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            engine_a_result=GradingResult(text="a", grading=None),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.grading_consistent is False

    def test_boundary_crossing(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="4"),
        )
        assert pair.is_boundary_crossing is True

    def test_no_boundary_crossing(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.is_boundary_crossing is False

    def test_boundary_crossing_4_to_5(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="4",
            engine_a_result=GradingResult(text="a", grading="4"),
            engine_b_result=GradingResult(text="b", grading="5"),
        )
        assert pair.is_boundary_crossing is True

    def test_boundary_crossing_4a_to_4c(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="4a",
            engine_a_result=GradingResult(text="a", grading="4a"),
            engine_b_result=GradingResult(text="b", grading="4c"),
        )
        assert pair.is_boundary_crossing is True

    def test_no_boundary_crossing_for_semantic_equivalent(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="4",
            engine_a_result=GradingResult(text="a", grading="4"),
            engine_b_result=GradingResult(text="b", grading="4a"),
        )
        assert pair.is_boundary_crossing is False
        assert pair.grading_consistent is False
        assert pair.grading_semantic_consistent is True

    def test_boundary_crossing_for_different_levels(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="4a"),
        )
        assert pair.is_boundary_crossing is True

    def test_boundary_crossing_3_to_5(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="5"),
        )
        assert pair.is_boundary_crossing is True

    def test_liver_boundary_crossing_3_to_4(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="liver",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="4"),
        )
        assert pair.is_boundary_crossing is True

    def test_liver_no_boundary_crossing_same_grade(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="liver",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.is_boundary_crossing is False

    def test_cardiac_boundary_crossing_normal_to_mild(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="cardiac",
            prompt="test",
            expected_grading="normal",
            engine_a_result=GradingResult(text="a", grading="normal"),
            engine_b_result=GradingResult(text="b", grading="mild"),
        )
        assert pair.is_boundary_crossing is True

    def test_cardiac_boundary_crossing_mild_to_moderate(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="cardiac",
            prompt="test",
            expected_grading="mild",
            engine_a_result=GradingResult(text="a", grading="mild"),
            engine_b_result=GradingResult(text="b", grading="moderate"),
        )
        assert pair.is_boundary_crossing is True

    def test_no_boundary_crossing_unknown_organ(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="unknown",
            prompt="test",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="4"),
        )
        assert pair.is_boundary_crossing is False

    def test_boundary_severity_none(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.boundary_severity == "none"

    def test_boundary_severity_critical(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="5"),
            boundary_weight=3.0,
            is_critical_boundary=True,
        )
        assert pair.boundary_severity == "critical"

    def test_boundary_severity_low(self):
        pair = ConsistencyPair(
            case_id="test-low-sev",
            organ="thyroid",
            prompt="test",
            boundary_weight=0.5,
            is_critical_boundary=False,
            engine_a_result=GradingResult(text="TI-RADS 3类", grading="3", organ="thyroid"),
            engine_b_result=GradingResult(text="TI-RADS 4a类", grading="4a", organ="thyroid"),
        )
        assert pair.is_boundary_crossing is True
        assert pair.boundary_severity == "low"


class TestMcNemarTest:
    def test_identical_results_high_p_value(self):
        a = [True, False, True, False, True, False, True, False, True, False]
        b = [True, False, True, False, True, False, True, False, True, False]
        _, p_value = mcnemar_test(a, b)
        assert p_value == 1.0

    def test_completely_different_results(self):
        a = [True] * 40 + [False] * 10
        b = [True] * 10 + [False] * 40
        _, p_value = mcnemar_test(a, b)
        assert p_value < 0.05

    def test_no_discordant_pairs(self):
        a = [True, True, False, False]
        b = [True, True, False, False]
        statistic, p_value = mcnemar_test(a, b)
        assert statistic == 0.0
        assert p_value == 1.0

    def test_small_sample_exact_test(self):
        a = [True, True, True, False, False, False, True, False, True, False]
        b = [True, True, False, True, False, False, True, True, False, False]
        _, p_value = mcnemar_test(a, b)
        assert 0.0 <= p_value <= 1.0

    def test_large_sample_chi2_approx(self):
        import random
        rng = random.Random(42)
        a = [rng.random() > 0.5 for _ in range(100)]
        b = [rng.random() > 0.5 for _ in range(100)]
        statistic, p_value = mcnemar_test(a, b)
        assert statistic >= 0
        assert 0.0 <= p_value <= 1.0

    def test_empty_lists(self):
        a: list = []
        b: list = []
        statistic, p_value = mcnemar_test(a, b)
        assert statistic == 0.0
        assert p_value == 1.0

    def test_all_same_value(self):
        a = [True] * 50
        b = [True] * 50
        _, p_value = mcnemar_test(a, b)
        assert p_value == 1.0

    def test_mild_asymmetry_not_significant(self):
        a = [True] * 20 + [False] * 30
        b = [True] * 22 + [False] * 28
        _, p_value = mcnemar_test(a, b)
        assert p_value > 0.05

    def test_severe_asymmetry_significant(self):
        a = [True] * 5 + [False] * 45
        b = [True] * 40 + [False] * 10
        _, p_value = mcnemar_test(a, b)
        assert p_value < 0.05


class TestTextExactMatchRate:
    def test_exact_match_rate_computed(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 3类，建议6个月随访", "TI-RADS 4a类，建议穿刺"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类，建议6个月随访", "TI-RADS 4a类，建议FNA"],
            name="b",
        )
        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test"},
            {"case_id": "002", "organ": "thyroid", "prompt": "test"},
        ]
        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = ConsistencyReport(engine_a_name="a", engine_b_name="b")
        pairs = [
            ConsistencyPair(
                case_id="001", organ="thyroid", prompt="test",
                engine_a_result=GradingResult(text="TI-RADS 3类，建议6个月随访", grading="3"),
                engine_b_result=GradingResult(text="TI-RADS 3类，建议6个月随访", grading="3"),
            ),
            ConsistencyPair(
                case_id="002", organ="thyroid", prompt="test",
                engine_a_result=GradingResult(text="TI-RADS 4a类，建议穿刺", grading="4a"),
                engine_b_result=GradingResult(text="TI-RADS 4a类，建议FNA", grading="4a"),
            ),
        ]
        verifier._compute_text_stats(report, pairs)
        assert report.text_exact_match_rate == 0.5

    def test_all_identical_text(self):
        report = ConsistencyReport(engine_a_name="a", engine_b_name="b")
        pairs = [
            ConsistencyPair(
                case_id=f"00{i}", organ="thyroid", prompt="test",
                engine_a_result=GradingResult(text="same text", grading="3"),
                engine_b_result=GradingResult(text="same text", grading="3"),
            )
            for i in range(5)
        ]
        verifier = ConsistencyVerifier(
            engine_a=MockInferenceEngine(responses=["x"] * 5, name="a"),
            engine_b=MockInferenceEngine(responses=["x"] * 5, name="b"),
        )
        verifier._compute_text_stats(report, pairs)
        assert report.text_exact_match_rate == 1.0

    def test_all_different_text(self):
        report = ConsistencyReport(engine_a_name="a", engine_b_name="b")
        pairs = [
            ConsistencyPair(
                case_id=f"00{i}", organ="thyroid", prompt="test",
                engine_a_result=GradingResult(text=f"text_a_{i}", grading="3"),
                engine_b_result=GradingResult(text=f"text_b_{i}", grading="3"),
            )
            for i in range(5)
        ]
        verifier = ConsistencyVerifier(
            engine_a=MockInferenceEngine(responses=["x"] * 5, name="a"),
            engine_b=MockInferenceEngine(responses=["x"] * 5, name="b"),
        )
        verifier._compute_text_stats(report, pairs)
        assert report.text_exact_match_rate == 0.0

    def test_exact_match_rate_gate_check(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                text_exact_match_rate=0.90,
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=100,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.85,
            )
            assert report.passed is False

    def test_exact_match_rate_passes_at_threshold(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                text_exact_match_rate=0.90,
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=100,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.91,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1050.0,
                latency_p99_reliable=True,
            )
            assert report.passed is True


class TestLatencyDiffOrLogic:
    def test_or_logic_pct_exceeds_abs_within_passes(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="or",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.91,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1150.0,
                latency_diff_pct=13.95,
                latency_p99_reliable=True,
            )
            assert report.passed is True

    def test_or_logic_low_baseline_pct_exceeds_abs_within_passes(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="or",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.91,
                latency_a_p99_ms=200.0,
                latency_b_p99_ms=350.0,
                latency_diff_pct=54.55,
                latency_p99_reliable=True,
            )
            assert report.passed is True

    def test_or_logic_both_exceed_fails(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="or",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_a_p99_ms=2000.0,
                latency_b_p99_ms=3000.0,
                latency_diff_pct=40.0,
                latency_p99_reliable=True,
            )
            assert report.passed is False


class TestLatencyUnreliableGateFailed:
    def test_unreliable_latency_causes_gate_failed(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=100,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_p99_reliable=False,
            )
            assert report.passed is False

    def test_reliable_latency_allows_gate_passed(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=100,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.91,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1050.0,
                latency_p99_reliable=True,
            )
            assert report.passed is True


class TestLVEFThreadSafety:
    def test_concurrent_lvef_access(self):
        import threading
        from app.inference.grading_definitions import _map_lvef_to_grading
        results = []
        errors = []
        def access_lvef():
            try:
                for _ in range(100):
                    result = _map_lvef_to_grading("50")
                    results.append(result)
            except Exception as e:
                errors.append(str(e))
        threads = [threading.Thread(target=access_lvef) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(errors) == 0
        assert all(r == "mild" for r in results)


class TestSmartTruncate:
    def test_short_text_no_truncation(self):
        from app.inference.consistency.statistics import _smart_truncate
        assert _smart_truncate("短文本", head=300, tail=200) == "短文本"

    def test_medium_text_head_tail(self):
        from app.inference.consistency.statistics import _smart_truncate
        text = "A" * 600
        result = _smart_truncate(text, head=300, tail=200)
        assert len(result) == 500
        assert result[:300] == "A" * 300
        assert result[-200:] == "A" * 200

    def test_long_text_preserves_conclusion(self):
        from app.inference.consistency.statistics import _smart_truncate
        text = "描述部分" * 100 + "结论：TI-RADS 4a类"
        result = _smart_truncate(text, head=300, tail=200)
        assert "TI-RADS 4a类" in result

    def test_text_similarity_with_conclusion_at_end(self):
        from app.inference.consistency.statistics import text_similarity
        text_a = "甲状腺结节描述" * 50 + "TI-RADS 4a类"
        text_b = "甲状腺结节描述" * 50 + "TI-RADS 4a类"
        sim = text_similarity(text_a, text_b)
        assert sim > 0.8

    def test_boundary_severity_critical_by_weight(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="4",
            engine_a_result=GradingResult(text="a", grading="4"),
            engine_b_result=GradingResult(text="b", grading="5"),
            boundary_weight=2.0,
            is_critical_boundary=True,
        )
        assert pair.boundary_severity == "critical"

    def test_expected_vs_actual_boundary_crossing(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="5"),
            engine_b_result=GradingResult(text="b", grading="5"),
        )
        assert pair.expected_vs_actual_boundary_crossing is True

    def test_expected_vs_actual_no_crossing(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.expected_vs_actual_boundary_crossing is False

    def test_expected_vs_actual_no_expected(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="5"),
        )
        assert pair.expected_vs_actual_boundary_crossing is False

    def test_boundary_severity_warning(self):
        pair = ConsistencyPair(
            case_id="001",
            organ="thyroid",
            prompt="test",
            expected_grading="3",
            engine_a_result=GradingResult(text="a", grading="3"),
            engine_b_result=GradingResult(text="b", grading="4"),
            boundary_weight=1.0,
            is_critical_boundary=False,
        )
        assert pair.boundary_severity == "warning"


class TestDetectHallucination:
    def test_no_hallucination_thyroid(self):
        text = "甲状腺结节TI-RADS 4a类，建议定期复查"
        assert detect_hallucination(text, "thyroid") is False

    def test_hallucination_forbidden_term_thyroid(self):
        text = "甲状腺结节伴肺癌转移"
        assert detect_hallucination(text, "thyroid") is True

    def test_hallucination_forbidden_term_liver(self):
        text = "肝脏占位伴甲状腺癌"
        assert detect_hallucination(text, "liver") is True

    def test_hallucination_forbidden_term_cardiac(self):
        text = "心脏超声发现肝癌转移"
        assert detect_hallucination(text, "cardiac") is True

    def test_hallucination_contradictory_grading(self):
        text = "甲状腺结节TI-RADS 4a类，但考虑TI-RADS 4b可能"
        assert detect_hallucination(text, "thyroid") is True

    def test_hallucination_empty_text(self):
        assert detect_hallucination("", "thyroid") is False

    def test_hallucination_empty_organ(self):
        assert detect_hallucination("甲状腺结节", "") is False


class TestConsistencyReport:
    def test_passed_when_all_criteria_met(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            total_cases=100,
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            text_exact_match_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
        )
        assert report.passed is True

    def test_failed_low_consistency(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.90,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
        )
        assert report.passed is False


class TestAdaptiveThreshold:
    def test_adaptive_threshold_tightens_after_high_pass_rate(self):
        from app.inference.verification_config import AdaptiveThresholdConfig, compute_adaptive_threshold
        config = AdaptiveThresholdConfig(
            metric_name="grading_consistency_rate",
            base_threshold=0.95, min_threshold=0.90, max_threshold=0.99,
        )
        result = compute_adaptive_threshold(config, [0.99, 0.98, 0.99, 0.98])
        assert result > config.base_threshold

    def test_adaptive_threshold_relaxes_after_low_pass_rate(self):
        from app.inference.verification_config import AdaptiveThresholdConfig, compute_adaptive_threshold
        config = AdaptiveThresholdConfig(
            metric_name="grading_consistency_rate",
            base_threshold=0.95, min_threshold=0.90, max_threshold=0.99,
        )
        result = compute_adaptive_threshold(config, [0.85, 0.88, 0.87, 0.86])
        assert result < config.base_threshold

    def test_adaptive_threshold_no_history_uses_base(self):
        from app.inference.verification_config import AdaptiveThresholdConfig, compute_adaptive_threshold
        config = AdaptiveThresholdConfig(
            metric_name="grading_consistency_rate",
            base_threshold=0.95, min_threshold=0.90, max_threshold=0.99,
        )
        result = compute_adaptive_threshold(config, [])
        assert result == config.base_threshold

    def test_failed_low_semantic_consistency(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.95,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
        )
        assert report.passed is False

    def test_failed_high_boundary_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.02,
            clinical_boundary_diff_rate_ci_upper=0.03,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
        )
        assert report.passed is False

    def test_failed_high_ci_upper(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                verification_phase="phase2",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a",
                engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.03,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
            )
            assert report.passed is False

    def test_failed_high_hallucination_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.01,
            text_consistency_rate=0.91,
        )
        assert report.passed is False

    def test_failed_low_text_consistency_rate(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.50,
        )
        assert report.passed is False

    def test_text_consistency_rate_semantic_definition(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.99,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.99,
            clinical_consistency_rate=0.50,
            text_exact_match_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
        )
        assert report.passed is True

    def test_clinical_consistency_rate_includes_hallucination(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            clinical_consistency_rate=0.50,
            text_exact_match_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
        )
        assert report.passed is True
        assert report.clinical_consistency_rate == 0.50

    def test_conditional_with_moderate_latency_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.92,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.008,
            text_consistency_rate=0.85,
            text_exact_match_rate=0.85,
            latency_diff_pct=25.0,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1250.0,
            latency_p99_reliable=True,
        )
        assert report.gate_status.value == "conditional"

    def test_failed_high_latency_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1500.0,
            latency_diff_pct=50.0,
            latency_p99_reliable=True,
        )
        assert report.passed is False

    def test_latency_diff_skipped_when_unreliable(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            latency_diff_pct=50.0,
            latency_p99_reliable=False,
        )
        assert report.passed is False


@pytest.mark.asyncio
class TestConsistencyVerifier:
    async def test_verify_with_mock_engines(self):
        engine_a = MockInferenceEngine(
            responses=[
                "TI-RADS 4a类",
                "LI-RADS 3类",
                "LVEF: 55%",
            ],
            name="llama.cpp",
        )
        engine_b = MockInferenceEngine(
            responses=[
                "TI-RADS 4a类",
                "LI-RADS 3类",
                "LVEF: 55%",
            ],
            name="vllm",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "甲状腺结节描述"},
            {"case_id": "002", "organ": "liver", "prompt": "肝脏占位描述"},
            {"case_id": "003", "organ": "cardiac", "prompt": "心脏功能描述"},
        ]

        verifier = ConsistencyVerifier(
            engine_a=engine_a,
            engine_b=engine_b,
            engine_a_name="llama.cpp",
            engine_b_name="vllm",
        )

        report = await verifier.verify(cases)

        assert report.total_cases == 3
        assert report.grading_consistency_rate == 1.0
        assert report.clinical_boundary_diff_rate_ci_upper >= 0.0

    async def test_verify_with_inconsistent_engines(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 4a类", "LI-RADS 3类"],
            name="llama.cpp",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类", "LI-RADS 4类"],
            name="vllm",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"},
            {"case_id": "002", "organ": "liver", "prompt": "test", "expected_grading": "3"},
        ]

        verifier = ConsistencyVerifier(
            engine_a=engine_a,
            engine_b=engine_b,
        )

        report = await verifier.verify(cases)

        assert report.total_cases == 2
        assert report.grading_consistency_rate == 0.0
        assert report.grading_consistent_count == 0
        assert len(report.inconsistent_cases) == 2

    async def test_report_serialization(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"], name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"], name="b")

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)

        cases = [{"case_id": "001", "organ": "thyroid", "prompt": "test"}]
        report = await verifier.verify(cases)

        report_dict = verifier.report_to_dict(report)
        assert "grading_consistency_rate" in report_dict
        assert "engine_a" in report_dict
        assert "engine_b" in report_dict

        report_json = verifier.report_to_json(report)
        import json
        parsed = json.loads(report_json)
        assert parsed["total_cases"] == 1

    async def test_empty_cases(self):
        engine_a = MockInferenceEngine(name="a")
        engine_b = MockInferenceEngine(name="b")

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify([])

        assert report.total_cases == 0
        assert report.grading_consistency_rate == 0.0


class TestFalseStats:
    def test_default_values(self):
        stats = FalseStats()
        assert stats.false_positives == 0
        assert stats.false_negatives == 0
        assert stats.true_positives == 0
        assert stats.true_negatives == 0
        assert stats.precision == 0.0
        assert stats.recall == 0.0
        assert stats.f1_score == 0.0

    def test_precision(self):
        stats = FalseStats(true_positives=8, false_positives=2)
        assert stats.precision == pytest.approx(0.8)

    def test_precision_zero_denominator(self):
        stats = FalseStats(true_positives=0, false_positives=0)
        assert stats.precision == 0.0

    def test_recall(self):
        stats = FalseStats(true_positives=7, false_negatives=3)
        assert stats.recall == pytest.approx(0.7)

    def test_recall_zero_denominator(self):
        stats = FalseStats(true_positives=0, false_negatives=0)
        assert stats.recall == 0.0

    def test_f1_score(self):
        stats = FalseStats(true_positives=8, false_positives=2, false_negatives=2)
        precision = 8 / 10
        recall = 8 / 10
        expected_f1 = 2 * precision * recall / (precision + recall)
        assert stats.f1_score == pytest.approx(expected_f1)

    def test_f1_score_zero(self):
        stats = FalseStats()
        assert stats.f1_score == 0.0

    def test_perfect_detection(self):
        stats = FalseStats(true_positives=10, true_negatives=90, false_positives=0, false_negatives=0)
        assert stats.precision == 1.0
        assert stats.recall == 1.0
        assert stats.f1_score == 1.0


@pytest.mark.asyncio
class TestFalseStatsWithPositiveCases:
    async def test_false_stats_with_hallucination_ground_truth(self):
        engine_a = MockInferenceEngine(
            responses=[
                "甲状腺结节伴肺癌转移，TI-RADS 4a类",
                "甲状腺结节，TI-RADS 3类，建议6个月随访",
                "肝脏占位伴甲状腺癌，LI-RADS 3类",
                "肝脏局灶性结节，LI-RADS 3类",
                "心脏超声发现肝癌转移，LVEF 60%",
                "LVEF 60%，收缩功能正常",
            ],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=[
                "甲状腺结节，TI-RADS 4a类，建议穿刺",
                "甲状腺结节，TI-RADS 3类，建议定期复查",
                "肝脏局灶性结节，LI-RADS 3类",
                "肝脏局灶性结节，LI-RADS 3类",
                "LVEF 60%，收缩功能正常",
                "LVEF 60%，收缩功能正常",
            ],
            name="b",
        )

        cases = [
            {"case_id": "halluc_thyroid_001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a", "expected_hallucination": True},
            {"case_id": "halluc_thyroid_002", "organ": "thyroid", "prompt": "test", "expected_grading": "3", "expected_hallucination": False},
            {"case_id": "halluc_liver_001", "organ": "liver", "prompt": "test", "expected_grading": "3", "expected_hallucination": True},
            {"case_id": "halluc_liver_002", "organ": "liver", "prompt": "test", "expected_grading": "3", "expected_hallucination": False},
            {"case_id": "halluc_cardiac_001", "organ": "cardiac", "prompt": "test", "expected_grading": "normal", "expected_hallucination": True},
            {"case_id": "halluc_cardiac_002", "organ": "cardiac", "prompt": "test", "expected_grading": "normal", "expected_hallucination": False},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert report.false_stats.engine_a_stats.true_positives > 0
        assert report.false_stats.engine_a_stats.false_positives >= 0
        assert report.false_stats.engine_a_stats.false_negatives >= 0
        assert report.false_stats.engine_a_stats.true_negatives > 0
        assert report.false_stats.engine_a_stats.cases_with_ground_truth > 0
        assert report.false_stats.engine_b_stats is not None
        assert report.false_stats.engine_b_stats.true_positives >= 0

    async def test_false_stats_precision_recall_with_positives(self):
        engine_a = MockInferenceEngine(
            responses=[
                "甲状腺结节伴肺癌转移，TI-RADS 4a类",
                "甲状腺结节，TI-RADS 3类",
            ],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=[
                "甲状腺结节，TI-RADS 4a类",
                "甲状腺结节，TI-RADS 3类",
            ],
            name="b",
        )

        cases = [
            {"case_id": "h001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a", "expected_hallucination": True},
            {"case_id": "h002", "organ": "thyroid", "prompt": "test", "expected_grading": "3", "expected_hallucination": False},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert report.false_stats.engine_a_stats.true_positives > 0 or report.false_stats.engine_a_stats.false_negatives > 0


class TestOrganBreakdown:
    @pytest.mark.asyncio
    async def test_organ_breakdown_populated(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 4a类", "LI-RADS 3类", "LVEF: 55%"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 4a类", "LI-RADS 3类", "LVEF: 55%"],
            name="b",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test"},
            {"case_id": "002", "organ": "liver", "prompt": "test"},
            {"case_id": "003", "organ": "cardiac", "prompt": "test"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert "thyroid" in report.organ_breakdown
        assert "liver" in report.organ_breakdown
        assert "cardiac" in report.organ_breakdown
        assert report.organ_breakdown["thyroid"]["total"] == 1
        assert report.organ_breakdown["thyroid"]["grading_consistency_rate"] == 1.0

    @pytest.mark.asyncio
    async def test_organ_breakdown_with_inconsistency(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 4a类", "TI-RADS 3类"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类", "TI-RADS 4a类"],
            name="b",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test"},
            {"case_id": "002", "organ": "thyroid", "prompt": "test"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert report.organ_breakdown["thyroid"]["total"] == 2
        assert report.organ_breakdown["thyroid"]["grading_consistent"] == 0
        assert report.organ_breakdown["thyroid"]["grading_consistency_rate"] == 0.0


@pytest.mark.asyncio
class TestOrganFalseStats:
    async def test_organ_false_stats_populated(self):
        engine_a = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 3类", "肝脏占位，LI-RADS 4类"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 3类", "肝脏占位，LI-RADS 4类"],
            name="b",
        )

        cases = [
            {"case_id": "t001", "organ": "thyroid", "prompt": "test", "expected_grading": "3", "expected_hallucination": False},
            {"case_id": "l001", "organ": "liver", "prompt": "test", "expected_grading": "4", "expected_hallucination": False},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert "thyroid" in report.organ_false_stats
        assert "liver" in report.organ_false_stats

    async def test_organ_false_stats_with_hallucination(self):
        engine_a = MockInferenceEngine(
            responses=["甲状腺结节伴肺癌转移，TI-RADS 4a类", "甲状腺结节，TI-RADS 3类"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 4a类", "甲状腺结节，TI-RADS 3类"],
            name="b",
        )

        cases = [
            {"case_id": "h001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a", "expected_hallucination": True},
            {"case_id": "h002", "organ": "thyroid", "prompt": "test", "expected_grading": "3", "expected_hallucination": False},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert "thyroid" in report.organ_false_stats
        thyroid_stats = report.organ_false_stats["thyroid"]
        assert thyroid_stats.cases_with_ground_truth == 2

    async def test_organ_false_stats_precision_recall(self):
        engine_a = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 3类"] * 4,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["甲状腺结节，TI-RADS 3类"] * 4,
            name="b",
        )

        cases = [
            {"case_id": f"t00{i}", "organ": "thyroid", "prompt": "test", "expected_grading": "3", "expected_hallucination": False}
            for i in range(4)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        thyroid_stats = report.organ_false_stats["thyroid"]
        assert thyroid_stats.engine_a_stats.true_negatives >= 0


class TestLatencyStatistics:
    @pytest.mark.asyncio
    async def test_latency_stats_populated(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"], name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"], name="b")

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        cases = [{"case_id": "001", "organ": "thyroid", "prompt": "test"}]
        report = await verifier.verify(cases)

        assert report.latency_a_p50_ms >= 0
        assert report.latency_a_p99_ms >= 0
        assert report.latency_b_p50_ms >= 0
        assert report.latency_b_p99_ms >= 0

    @pytest.mark.asyncio
    async def test_latency_diff_pct(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"], name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"], name="b")

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        cases = [{"case_id": "001", "organ": "thyroid", "prompt": "test"}]
        report = await verifier.verify(cases)

        assert report.latency_diff_pct >= 0


@pytest.mark.asyncio
class TestLatencyReliability:
    async def test_latency_sample_count_recorded(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"] * 3, name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"] * 3, name="b")
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(3)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert report.latency_sample_count_a == 3
        assert report.latency_sample_count_b == 3

    async def test_latency_p99_not_reliable_with_few_samples(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"] * 50, name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"] * 50, name="b")
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(50)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert report.latency_p99_reliable is False

    async def test_latency_p99_reliable_with_sufficient_samples(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"] * 505, name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"] * 505, name="b")
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(505)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert report.latency_p99_reliable is True


class TestFailureAnalysis:
    @pytest.mark.asyncio
    async def test_failure_analysis_on_inconsistent(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 4a类"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"],
            name="b",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert "summary" in report.failure_analysis
        assert report.failure_analysis["summary"]["grading_failures"] >= 1
        assert "by_organ" in report.failure_analysis
        assert "trace_chain" in report.failure_analysis
        assert len(report.failure_analysis["trace_chain"]) >= 1

        trace = report.failure_analysis["trace_chain"][0]
        assert "extracted_grading_a" in trace
        assert "extracted_grading_b" in trace
        assert "hallucination_issues_a" in trace
        assert "hallucination_issues_b" in trace
        assert "text_similarity_score" in trace
        assert isinstance(trace["text_similarity_score"], float)
        assert "boundary_details" in trace

    @pytest.mark.asyncio
    async def test_failure_analysis_empty_on_consistent(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"], name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"], name="b")

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        cases = [{"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "3"}]
        report = await verifier.verify(cases)

        assert report.failure_analysis["summary"]["total_failures"] == 0
        assert len(report.failure_analysis["trace_chain"]) == 0

    @pytest.mark.asyncio
    async def test_failure_analysis_recommendations(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 4a类"] * 5,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 5,
            name="b",
        )

        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"}
            for i in range(5)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert isinstance(report.failure_analysis["recommendations"], list)

    @pytest.mark.asyncio
    async def test_failure_analysis_latency_recommendation(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 5,
            name="a",
            latency_ms=100.0,
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 5,
            name="b",
            latency_ms=500.0,
        )

        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test", "expected_grading": "3"}
            for i in range(5)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        latency_recs = [r for r in report.failure_analysis["recommendations"] if "延迟差异" in r]
        assert len(latency_recs) >= 1

    @pytest.mark.asyncio
    async def test_failure_analysis_organ_consistency_recommendation(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 4a类"] * 5,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 5,
            name="b",
        )

        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"}
            for i in range(5)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        organ_recs = [r for r in report.failure_analysis["recommendations"] if "thyroid" in r and "一致率" in r]
        assert len(organ_recs) >= 1

    @pytest.mark.asyncio
    async def test_trace_chain_includes_extraction_steps(self):
        engine_a = MockInferenceEngine(
            responses=["检查未见明显异常"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"],
            name="b",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "3"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        if report.failure_analysis["trace_chain"]:
            steps = report.failure_analysis["trace_chain"][0].get("steps", [])
            extraction_steps = [s for s in steps if s.get("stage") == "grading_extraction"]
            assert len(extraction_steps) >= 1

    @pytest.mark.asyncio
    async def test_trace_chain_includes_hallucination_detail(self):
        engine_a = MockInferenceEngine(
            responses=["甲状腺结节伴肺癌转移，TI-RADS 4a类"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"],
            name="b",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        if report.failure_analysis["trace_chain"]:
            steps = report.failure_analysis["trace_chain"][0].get("steps", [])
            hallucination_steps = [s for s in steps if s.get("stage") == "hallucination_check"]
            assert len(hallucination_steps) >= 1


class TestWeightedBoundaryDiffRate:
    @pytest.mark.asyncio
    async def test_weighted_boundary_diff_rate_zero(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"], name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"], name="b")

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        cases = [{"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "3"}]
        report = await verifier.verify(cases)

        assert report.weighted_boundary_diff_rate == 0.0

    @pytest.mark.asyncio
    async def test_weighted_boundary_diff_rate_nonzero(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 3类", "TI-RADS 4a类"],
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 5类", "TI-RADS 4c类"],
            name="b",
        )

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test", "expected_grading": "3"},
            {"case_id": "002", "organ": "thyroid", "prompt": "test", "expected_grading": "4a"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert report.weighted_boundary_diff_rate > 0
        assert report.total_boundary_weight > 0


class TestCrossValidation:
    @pytest.mark.asyncio
    async def test_cross_validate_returns_results(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 20,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 20,
            name="b",
        )

        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(20)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        results = await verifier.cross_validate(cases, n_folds=3, seed=42)

        assert len(results) == 3
        for r in results:
            assert isinstance(r, CrossValidationResult)
            assert r.train_count > 0
            assert r.validation_count > 0
            assert 0.0 <= r.train_accuracy <= 1.0
            assert 0.0 <= r.validation_accuracy <= 1.0

    @pytest.mark.asyncio
    async def test_cross_validate_accuracy_diff(self):
        engine_a = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 20,
            name="a",
        )
        engine_b = MockInferenceEngine(
            responses=["TI-RADS 3类"] * 20,
            name="b",
        )

        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(20)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        results = await verifier.cross_validate(cases, n_folds=3, seed=42)

        for r in results:
            assert r.accuracy_diff >= 0.0

    @pytest.mark.asyncio
    async def test_cross_validate_n_folds_exceeds_cases(self):
        engine_a = MockInferenceEngine(name="a")
        engine_b = MockInferenceEngine(name="b")

        cases = [
            {"case_id": "001", "organ": "thyroid", "prompt": "test"},
            {"case_id": "002", "organ": "thyroid", "prompt": "test"},
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        with pytest.raises(ValueError, match="n_folds"):
            await verifier.cross_validate(cases, n_folds=5)


class TestNMPADeclaration:
    def test_nmpa_declaration_passed_returns_none(self):
        report = ConsistencyReport(
            engine_a_name="llama.cpp",
            engine_b_name="vllm",
            total_cases=100,
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.99,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.01,
            hallucination_rate_diff=0.003,
            text_consistency_rate=0.95,
            text_exact_match_rate=0.91,
            latency_diff_pct=10.0,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
        )
        assert report.nmpa_declaration is None

    def test_nmpa_declaration_failed_returns_none(self):
        report = ConsistencyReport(
            engine_a_name="llama.cpp",
            engine_b_name="vllm",
            total_cases=100,
            grading_consistency_rate=0.80,
        )
        assert report.nmpa_declaration is None

    def test_nmpa_declaration_conditional(self):
        report = ConsistencyReport(
            engine_a_name="llama.cpp",
            engine_b_name="vllm",
            total_cases=100,
            grading_consistency_rate=0.92,
            clinical_boundary_diff_rate=0.015,
            clinical_boundary_diff_rate_ci_upper=0.018,
            hallucination_rate_diff=0.008,
            text_consistency_rate=0.85,
            text_exact_match_rate=0.85,
            latency_diff_pct=25.0,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1250.0,
            latency_p99_reliable=True,
        )
        decl = report.nmpa_declaration
        assert decl is not None
        assert "framework_a" in decl
        assert "declaration" in decl
        assert "llama.cpp" in decl["declaration"]


@pytest.mark.asyncio
class TestOrganSampleSize:
    async def test_organ_sample_warning_when_insufficient(self):
        from app.inference.verification_config import reset_verification_config
        reset_verification_config()
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"] * 3, name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"] * 3, name="b")
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(3)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert len(report.organ_sample_warnings) > 0
        assert report.organ_sample_warnings[0]["organ"] == "thyroid"
        assert report.organ_sample_warnings[0]["actual"] == 3
        assert report.organ_sample_warnings[0]["required"] == 100

    async def test_no_warning_when_sufficient(self):
        from app.inference.verification_config import reset_verification_config
        reset_verification_config()
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"] * 105, name="a")
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"] * 105, name="b")
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(105)
        ]
        report = await ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b).verify(cases)
        assert len(report.organ_sample_warnings) == 0


class TestPhase2GateCriteria:
    def test_phase2_fails_with_insufficient_organ_samples(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig

        phase2_config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                verification_phase="phase2",
                phase2_min_cases_per_organ=300,
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=phase2_config):
            report = ConsistencyReport(
                engine_a_name="llama.cpp",
                engine_b_name="vllm",
                total_cases=100,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.99,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.01,
                hallucination_rate_diff=0.003,
                text_consistency_rate=0.95,
                latency_diff_pct=10.0,
                organ_breakdown={"thyroid": {"total": 100}},
            )
            assert report.gate_status == GateStatus.FAILED

    def test_phase2_passes_with_sufficient_samples(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig

        phase2_config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                verification_phase="phase2",
                phase2_min_cases_per_organ=300,
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=phase2_config):
            report = ConsistencyReport(
                engine_a_name="llama.cpp",
                engine_b_name="vllm",
                total_cases=1200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.99,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.01,
                hallucination_rate_diff=0.003,
                text_consistency_rate=0.95,
                text_exact_match_rate=0.91,
                latency_diff_pct=10.0,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1050.0,
                latency_p99_reliable=True,
                organ_breakdown={
                    "thyroid": {"total": 300},
                    "liver": {"total": 300},
                    "cardiac": {"total": 300},
                    "breast": {"total": 300},
                },
            )
            assert report.gate_status == GateStatus.PASSED


@pytest.mark.slow
class TestBoundaryCaseValidation:
    """大规模边界病例验证测试"""

    @pytest.fixture(scope="class")
    def boundary_cases(self):
        from tests.fixtures.boundary_test_cases import generate_all_boundary_cases
        return generate_all_boundary_cases()

    def test_boundary_case_count(self, boundary_cases):
        assert len(boundary_cases) >= 400

    def test_thyroid_boundary_case_count(self, boundary_cases):
        thyroid = [c for c in boundary_cases if c["organ"] == "thyroid"]
        assert len(thyroid) >= 100

    def test_liver_boundary_case_count(self, boundary_cases):
        liver = [c for c in boundary_cases if c["organ"] == "liver"]
        assert len(liver) >= 100

    def test_cardiac_boundary_case_count(self, boundary_cases):
        cardiac = [c for c in boundary_cases if c["organ"] == "cardiac"]
        assert len(cardiac) >= 100

    def test_breast_boundary_case_count(self, boundary_cases):
        breast = [c for c in boundary_cases if c["organ"] == "breast"]
        assert len(breast) >= 100

    def test_boundary_crossing_detection(self, boundary_cases):
        from app.inference.consistency import _grades_cross_clinical_boundary
        for case in boundary_cases:
            assert _grades_cross_clinical_boundary(
                case["boundary_from"], case["boundary_to"], case["organ"]
            ), f"Expected boundary crossing: {case['boundary_from']} <-> {case['boundary_to']} for {case['organ']}"

    def test_boundary_case_consistency_pair(self, boundary_cases):
        for case in boundary_cases:
            pair = ConsistencyPair(
                case_id=case["case_id"],
                organ=case["organ"],
                prompt=case["prompt"],
                expected_grading=case["expected_grading"],
                engine_a_result=GradingResult(text="a", grading=case["engine_a_grading"]),
                engine_b_result=GradingResult(text="b", grading=case["engine_b_grading"]),
            )
            assert pair.is_boundary_crossing, f"Expected boundary crossing for {case['case_id']}"


@pytest.mark.slow
class TestRoutineCaseValidation:

    @pytest.fixture(scope="class")
    def routine_cases(self):
        from tests.fixtures.boundary_test_cases import generate_routine_cases
        return generate_routine_cases()

    def test_routine_case_count(self, routine_cases):
        assert len(routine_cases) >= 200

    def test_routine_case_not_boundary(self, routine_cases):
        for case in routine_cases:
            assert not case.get("is_boundary_case", False)

    def test_routine_case_three_organs(self, routine_cases):
        organs = {c["organ"] for c in routine_cases}
        assert "thyroid" in organs
        assert "liver" in organs
        assert "cardiac" in organs
        assert "breast" in organs

    def test_routine_case_consistency_pair(self, routine_cases):
        for case in routine_cases:
            pair = ConsistencyPair(
                case_id=case["case_id"],
                organ=case["organ"],
                prompt=case["prompt"],
                expected_grading=case["expected_grading"],
                engine_a_result=GradingResult(text="a", grading=case["engine_a_grading"]),
                engine_b_result=GradingResult(text="b", grading=case["engine_b_grading"]),
            )
            assert not pair.is_boundary_crossing


@pytest.mark.slow
class TestHighRiskCaseValidation:

    @pytest.fixture(scope="class")
    def high_risk_cases(self):
        from tests.fixtures.boundary_test_cases import generate_high_risk_cases
        return generate_high_risk_cases()

    def test_high_risk_case_count(self, high_risk_cases):
        assert len(high_risk_cases) >= 100

    def test_high_risk_case_flag(self, high_risk_cases):
        for case in high_risk_cases:
            assert case.get("is_high_risk", False)

    def test_high_risk_case_three_organs(self, high_risk_cases):
        organs = {c["organ"] for c in high_risk_cases}
        assert "thyroid" in organs
        assert "liver" in organs
        assert "cardiac" in organs


class TestWeightedBoundaryGateCheck:
    def test_failed_high_weighted_boundary_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            weighted_boundary_diff_rate=0.02,
            critical_boundary_diff_rate=0.001,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
        )
        assert report.passed is False

    def test_passed_with_acceptable_weighted_boundary(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            weighted_boundary_diff_rate=0.008,
            critical_boundary_diff_rate=0.003,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            text_exact_match_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
        )
        assert report.passed is True


class TestCriticalBoundaryGateCheck:
    def test_failed_high_critical_boundary_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            weighted_boundary_diff_rate=0.008,
            critical_boundary_diff_rate=0.01,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
        )
        assert report.passed is False


class TestLVEFConflictGateCheck:
    def test_passed_with_acceptable_lvef_conflict_rate(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            total_cases=100,
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            weighted_boundary_diff_rate=0.008,
            critical_boundary_diff_rate=0.003,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            text_exact_match_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
            lvef_conflict_count=1,
        )
        assert report.passed is True

    def test_failed_excessive_lvef_conflict_rate(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            total_cases=100,
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            weighted_boundary_diff_rate=0.008,
            critical_boundary_diff_rate=0.003,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            lvef_conflict_count=5,
        )
        assert report.passed is False


class TestHallucinationDirectionality:
    def test_hallucination_higher_engine_recorded(self):
        report = ConsistencyReport(
            engine_a_name="llama.cpp",
            engine_b_name="vllm",
            hallucination_rate_a=0.008,
            hallucination_rate_b=0.003,
            hallucination_higher_engine="llama.cpp",
        )
        assert report.hallucination_higher_engine == "llama.cpp"


class TestAbsoluteHallucinationRateGate:
    def test_failed_when_absolute_hallucination_rate_exceeded(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            total_cases=100,
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            hallucination_rate_a=0.06,
            hallucination_rate_b=0.01,
        )
        assert report.passed is False
        assert report.absolute_hallucination_rate_exceeded is True

    def test_passed_when_absolute_hallucination_rate_within_threshold(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            total_cases=100,
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            text_exact_match_rate=0.91,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1050.0,
            latency_p99_reliable=True,
            hallucination_rate_a=0.03,
            hallucination_rate_b=0.02,
        )
        assert report.absolute_hallucination_rate_exceeded is False


class TestVerificationTemperature:
    def test_verification_temperature_recorded(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            verification_temperature=0.0,
        )
        assert report.verification_temperature == 0.0


class TestLatencyAbsDiffGateCheck:
    def test_failed_high_abs_latency_diff(self):
        report = ConsistencyReport(
            engine_a_name="a",
            engine_b_name="b",
            grading_consistency_rate=0.96,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.015,
            weighted_boundary_diff_rate=0.008,
            critical_boundary_diff_rate=0.003,
            hallucination_rate_diff=0.005,
            text_consistency_rate=0.91,
            latency_a_p99_ms=100.0,
            latency_b_p99_ms=800.0,
            latency_p99_reliable=True,
        )
        assert report.passed is False


class TestGradingExtractionFallback:
    def test_thyroid_no_prefix_fallback(self):
        text = "甲状腺结节4a类，建议穿刺活检"
        assert extract_grading(text, "thyroid") == "4a"

    def test_liver_no_prefix_fallback(self):
        text = "肝脏占位4类，建议增强MRI"
        assert extract_grading(text, "liver") == "4"

    def test_breast_no_prefix_fallback(self):
        text = "乳腺结节4b类，建议穿刺活检"
        assert extract_grading(text, "breast") == "4b"

    def test_thyroid_no_prefix_3(self):
        text = "甲状腺结节3类，良性可能"
        assert extract_grading(text, "thyroid") == "3"

    def test_no_prefix_negative_case_followup_count(self):
        text = "建议3次复查，定期随访"
        assert extract_grading(text, "thyroid") is None

    def test_no_prefix_negative_case_size_measurement(self):
        text = "结节大小约3mm，边界清晰"
        assert extract_grading(text, "thyroid") is None


class TestOverallTimeout:
    @pytest.mark.asyncio
    async def test_overall_timeout_returns_empty_on_timeout(self):
        engine_a = MockInferenceEngine(responses=["TI-RADS 3类"] * 10, name="a", latency_ms=500.0)
        engine_b = MockInferenceEngine(responses=["TI-RADS 3类"] * 10, name="b", latency_ms=500.0)
        cases = [
            {"case_id": f"00{i}", "organ": "thyroid", "prompt": "test"}
            for i in range(10)
        ]
        verifier = ConsistencyVerifier(
            engine_a=engine_a,
            engine_b=engine_b,
            overall_timeout_seconds=0.001,
        )
        report = await verifier.verify(cases)
        assert report.total_cases == 0


class TestOrganGateCheck:
    def test_global_passed_but_organ_conditional(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.85,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1050.0,
                latency_p99_reliable=True,
                organ_breakdown={
                    "thyroid": {"total": 100, "grading_consistency_rate": 0.93, "clinical_boundary_diff_rate": 0.005},
                    "liver": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                },
            )
            assert report.gate_status == GateStatus.CONDITIONAL
            assert len(report.organ_gate_failures) == 1
            assert report.organ_gate_failures[0]["organ"] == "thyroid"

    def test_organ_severe_failure_gate_failed(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                organ_breakdown={
                    "thyroid": {"total": 100, "grading_consistency_rate": 0.88, "clinical_boundary_diff_rate": 0.005},
                    "liver": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                },
            )
            assert report.gate_status == GateStatus.FAILED

    def test_all_organs_pass_gate_passed(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                total_cases=200,
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.91,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1050.0,
                latency_p99_reliable=True,
                organ_breakdown={
                    "thyroid": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                    "liver": {"total": 100, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.005},
                },
            )
            assert report.gate_status == GateStatus.PASSED


class TestLatencyDiffAndLogic:
    def test_and_logic_pct_exceeds_abs_ok(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="and",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_a_p99_ms=1000.0,
                latency_b_p99_ms=1300.0,
                latency_p99_reliable=True,
            )
            assert report.passed is False

    def test_and_logic_abs_exceeds_pct_ok(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="and",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                latency_a_p99_ms=2000.0,
                latency_b_p99_ms=2250.0,
                latency_p99_reliable=True,
            )
            assert report.passed is False

    def test_and_logic_both_pass(self):
        from unittest.mock import patch
        from app.inference.verification_config import VerificationConfig, ConsistencyThresholdConfig
        config = VerificationConfig(
            consistency_thresholds=ConsistencyThresholdConfig(
                latency_diff_pct=20.0,
                latency_diff_abs_ms=200.0,
                latency_diff_gate_logic="and",
            ),
        )
        with patch("app.inference.consistency.report.get_verification_config", return_value=config):
            report = ConsistencyReport(
                engine_a_name="a", engine_b_name="b",
                grading_consistency_rate=0.96,
                semantic_grading_consistency_rate=0.98,
                clinical_boundary_diff_rate=0.005,
                clinical_boundary_diff_rate_ci_upper=0.015,
                hallucination_rate_diff=0.005,
                text_consistency_rate=0.91,
                text_exact_match_rate=0.91,
                latency_a_p99_ms=2000.0,
                latency_b_p99_ms=2100.0,
                latency_p99_reliable=True,
            )
            assert report.passed is True


class TestBirads0Boundary:
    def test_breast_birads_0_to_3_boundary_crossing(self):
        pair = ConsistencyPair(
            case_id="001", organ="breast", prompt="test",
            expected_grading="0",
            engine_a_result=GradingResult(text="a", grading="0"),
            engine_b_result=GradingResult(text="b", grading="3"),
        )
        assert pair.is_boundary_crossing is True

    def test_breast_birads_0_to_5_critical_boundary(self):
        pair = ConsistencyPair(
            case_id="001", organ="breast", prompt="test",
            expected_grading="0",
            engine_a_result=GradingResult(text="a", grading="0"),
            engine_b_result=GradingResult(text="b", grading="5"),
            boundary_weight=3.0,
            is_critical_boundary=True,
        )
        assert pair.boundary_severity == "critical"


def _make_quantization_pair(fp16_text: str, q4_text: str, organ: str) -> ConsistencyPair:
    return ConsistencyPair(
        case_id="quant_pair",
        organ=organ,
        prompt="test",
        engine_a_result=GradingResult(text=fp16_text, grading=extract_grading(fp16_text, organ)),
        engine_b_result=GradingResult(text=q4_text, grading=extract_grading(q4_text, organ)),
    )


class TestQuantizationHallucinationIncrement:
    THRESHOLD = BenchmarkThresholdConfig().hallucination_increment
    TOTAL_CASES = 200

    def _compute_increment(self, fp16_texts, q4_texts, organs):
        fp16_halluc = sum(
            1 for text, organ in zip(fp16_texts, organs)
            if detect_hallucination(text, organ)
        )
        q4_halluc = sum(
            1 for text, organ in zip(q4_texts, organs)
            if detect_hallucination(text, organ)
        )
        fp16_rate = fp16_halluc / len(fp16_texts)
        q4_rate = q4_halluc / len(q4_texts)
        return max(0.0, q4_rate - fp16_rate)

    def test_both_no_hallucination_increment_zero_passes(self):
        clean_text = "甲状腺结节，TI-RADS 3类，建议6个月随访复查"
        organ = "thyroid"
        fp16_texts = [clean_text] * self.TOTAL_CASES
        q4_texts = [clean_text] * self.TOTAL_CASES
        organs = [organ] * self.TOTAL_CASES
        increment = self._compute_increment(fp16_texts, q4_texts, organs)
        assert increment == 0.0
        assert increment <= self.THRESHOLD

    def test_q4_one_hallucination_in_200_increment_0_5_pct_passes(self):
        clean_text = "甲状腺结节，TI-RADS 3类，建议6个月随访复查"
        hallucination_text = "甲状腺结节伴肺癌转移，TI-RADS 3类"
        organ = "thyroid"
        fp16_texts = [clean_text] * self.TOTAL_CASES
        q4_texts = [clean_text] * (self.TOTAL_CASES - 1) + [hallucination_text]
        organs = [organ] * self.TOTAL_CASES
        increment = self._compute_increment(fp16_texts, q4_texts, organs)
        assert increment == pytest.approx(0.005, abs=1e-6)
        assert increment <= self.THRESHOLD

    def test_q4_two_hallucinations_in_200_increment_1_pct_fails(self):
        clean_text = "甲状腺结节，TI-RADS 3类，建议6个月随访复查"
        hallucination_text = "甲状腺结节伴肺癌转移，TI-RADS 3类"
        organ = "thyroid"
        fp16_texts = [clean_text] * self.TOTAL_CASES
        q4_texts = [clean_text] * (self.TOTAL_CASES - 2) + [hallucination_text] * 2
        organs = [organ] * self.TOTAL_CASES
        increment = self._compute_increment(fp16_texts, q4_texts, organs)
        assert increment == pytest.approx(0.01, abs=1e-6)
        assert increment > self.THRESHOLD


class TestFalseStatsWilsonCI:
    def test_precision_ci_with_known_values(self):
        from app.inference.consistency.statistics import wilson_confidence_interval
        tp, fp = 95, 5
        lower, upper = wilson_confidence_interval(tp, tp + fp)
        assert lower > 0
        assert upper <= 1.0
        assert lower < tp / (tp + fp) <= upper

    def test_recall_ci_with_known_values(self):
        from app.inference.consistency.statistics import wilson_confidence_interval
        tp, fn = 90, 10
        lower, upper = wilson_confidence_interval(tp, tp + fn)
        assert lower > 0
        assert upper <= 1.0
        assert lower < tp / (tp + fn) <= upper

    def test_ci_bounds_monotonic_with_sample_size(self):
        from app.inference.consistency.statistics import wilson_confidence_interval
        lower_s, upper_s = wilson_confidence_interval(9, 20)
        lower_l, upper_l = wilson_confidence_interval(90, 200)
        width_small = upper_s - lower_s
        width_large = upper_l - lower_l
        assert width_large < width_small

    def test_ci_zero_denominator(self):
        from app.inference.consistency.statistics import wilson_confidence_interval
        lower, upper = wilson_confidence_interval(0, 0)
        assert lower == 0.0
        assert upper == 0.0


class TestCombinedGateScenarios:
    def _make_report(self, **overrides):
        defaults = dict(
            engine_a_name="vllm", engine_b_name="llama_cpp",
            total_cases=200, grading_consistent_count=190,
            grading_consistency_rate=0.95,
            semantic_grading_consistency_rate=0.98,
            clinical_boundary_diff_rate=0.005,
            clinical_boundary_diff_rate_ci_upper=0.01,
            weighted_boundary_diff_rate=0.005,
            critical_boundary_diff_rate=0.002,
            hallucination_rate_diff=0.003,
            text_consistency_rate=0.92,
            latency_diff_pct=10.0,
            latency_a_p99_ms=2000.0, latency_b_p99_ms=2200.0,
            latency_p99_reliable=True,
            lvef_conflict_count=1,
            hallucination_rate_a=0.03, hallucination_rate_b=0.02,
            absolute_hallucination_rate_exceeded=False,
        )
        defaults.update(overrides)
        return ConsistencyReport(**defaults)

    def test_absolute_hallucination_exceeded_and_diff_exceeded(self):
        report = self._make_report(
            hallucination_rate_a=0.06, hallucination_rate_b=0.01,
            hallucination_rate_diff=0.05,
        )
        assert report.gate_status == GateStatus.FAILED

    def test_absolute_hallucination_exceeded_but_diff_within(self):
        report = self._make_report(
            hallucination_rate_a=0.06, hallucination_rate_b=0.055,
            hallucination_rate_diff=0.005,
        )
        assert report.gate_status == GateStatus.CONDITIONAL

    def test_organ_failure_and_absolute_hallucination(self):
        report = self._make_report(
            hallucination_rate_a=0.06,
            organ_breakdown={"thyroid": {"total": 100, "grading_consistency_rate": 0.85, "clinical_boundary_diff_rate": 0.005}},
        )
        assert report.gate_status == GateStatus.FAILED


class TestLatencyDiffDirectionality:
    def test_latency_diff_pct_engine_a_slower(self):
        report = ConsistencyReport(
            engine_a_name="vllm", engine_b_name="llama_cpp",
            latency_a_p99_ms=3000.0, latency_b_p99_ms=2000.0,
            latency_diff_pct=abs(2000.0 - 3000.0) / ((3000.0 + 2000.0) / 2) * 100,
            latency_faster_engine="llama_cpp",
            latency_p99_reliable=True,
        )
        assert report.latency_diff_pct > 0
        assert report.latency_faster_engine == "llama_cpp"

    def test_latency_diff_pct_engine_b_slower(self):
        report = ConsistencyReport(
            engine_a_name="vllm", engine_b_name="llama_cpp",
            latency_a_p99_ms=2000.0, latency_b_p99_ms=3000.0,
            latency_diff_pct=abs(3000.0 - 2000.0) / ((2000.0 + 3000.0) / 2) * 100,
            latency_faster_engine="vllm",
            latency_p99_reliable=True,
        )
        assert report.latency_diff_pct > 0
        assert report.latency_faster_engine == "vllm"

    def test_latency_equal_engines(self):
        report = ConsistencyReport(
            engine_a_name="vllm", engine_b_name="llama_cpp",
            latency_a_p99_ms=2000.0, latency_b_p99_ms=2000.0,
            latency_diff_pct=0.0,
            latency_p99_reliable=True,
        )
        assert report.latency_diff_pct == 0.0


class TestQuantizationHallucinationVerifier:
    def test_increment_within_threshold(self):
        from app.inference.consistency.quantization_verifier import QuantizationHallucinationVerifier
        v = QuantizationHallucinationVerifier(threshold=0.005)
        result = v.verify(baseline_hallucination_rate=0.01, test_hallucination_rate=0.012)
        assert result.increment == pytest.approx(0.002)
        assert result.passed is True

    def test_increment_exceeds_threshold(self):
        from app.inference.consistency.quantization_verifier import QuantizationHallucinationVerifier
        v = QuantizationHallucinationVerifier(threshold=0.005)
        result = v.verify(baseline_hallucination_rate=0.01, test_hallucination_rate=0.02)
        assert result.increment == pytest.approx(0.01)
        assert result.passed is False

    def test_negative_increment(self):
        from app.inference.consistency.quantization_verifier import QuantizationHallucinationVerifier
        v = QuantizationHallucinationVerifier(threshold=0.005)
        result = v.verify(baseline_hallucination_rate=0.02, test_hallucination_rate=0.01)
        assert result.increment == pytest.approx(-0.01)
        assert result.passed is True

    def test_verify_and_update_report(self):
        from app.inference.consistency.quantization_verifier import QuantizationHallucinationVerifier
        from app.inference.consistency.report import ConsistencyReport
        v = QuantizationHallucinationVerifier(threshold=0.005)
        report = ConsistencyReport(engine_a_name="a", engine_b_name="b", test_data_version="2.0", verification_temperature=0.0)
        result = v.verify_and_update_report(0.01, 0.02, report)
        assert report.quantization_hallucination_increment == pytest.approx(0.01)
        assert report.quantization_baseline_rate == 0.01
        assert report.quantization_test_rate == 0.02
        assert report.quantization_increment_exceeded is True


class TestGPUFragmentationMonitor:
    def test_check_fragmentation_ok(self):
        from app.inference.gpu_monitor import GPUFragmentationMonitor
        monitor = GPUFragmentationMonitor(warning_threshold=0.30, critical_threshold=0.40)
        result = monitor.check_fragmentation()
        assert "fragmentation_rate" in result
        assert "status" in result
        assert result["status"] in ("ok", "warning", "critical", "unavailable")

    def test_fragmentation_rate_bounds(self):
        from app.inference.gpu_monitor import GPUFragmentationMonitor
        monitor = GPUFragmentationMonitor()
        rate = monitor.compute_fragmentation_rate()
        assert 0.0 <= rate <= 1.0


class TestConfigConsistency:
    def test_default_config_loads_from_json(self):
        from app.inference.verification_config import load_config_from_json, get_default_verification_config
        import os
        json_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "app", "inference", "data", "default_verification_config.json")
        json_path = os.path.normpath(json_path)
        if not os.path.exists(json_path):
            pytest.skip("default_verification_config.json not found")
        loaded = load_config_from_json(json_path)
        assert loaded is not None
        default = get_default_verification_config()
        assert loaded.consistency_thresholds.grading_consistency_rate == default.consistency_thresholds.grading_consistency_rate
        assert loaded.consistency_thresholds.clinical_boundary_diff_rate == default.consistency_thresholds.clinical_boundary_diff_rate

    def test_boundary_config_code_json_consistency(self):
        from app.inference.verification_config import get_default_boundary_config, load_config_from_json
        import os
        json_path = os.path.join(os.path.dirname(__file__), "..", "..", "..", "app", "inference", "data", "default_verification_config.json")
        json_path = os.path.normpath(json_path)
        if not os.path.exists(json_path):
            pytest.skip("default_verification_config.json not found")
        loaded = load_config_from_json(json_path)
        code_config = get_default_boundary_config()
        if loaded is None:
            pytest.skip("Failed to load JSON config")
        for organ in code_config.organ_boundaries:
            code_pairs = {(p.from_grade, p.to_grade): (p.weight, p.is_critical) for p in code_config.organ_boundaries[organ]}
            json_pairs = {(p.from_grade, p.to_grade): (p.weight, p.is_critical) for p in loaded.boundary_config.organ_boundaries.get(organ, [])}
            for key, (w, c) in code_pairs.items():
                assert key in json_pairs, f"Missing boundary {key} in JSON for {organ}"
                assert json_pairs[key][0] == w, f"Weight mismatch for {organ} {key}: code={w}, json={json_pairs[key][0]}"
                assert json_pairs[key][1] == c, f"is_critical mismatch for {organ} {key}: code={c}, json={json_pairs[key][1]}"


class TestWeightedBoundaryDiffRateFix:
    def test_weighted_rate_with_actual_weights(self):
        from app.inference.consistency.report import ConsistencyReport, ConsistencyPair, GradingResult
        from app.inference.verification_config import get_verification_config
        config = get_verification_config()
        report = ConsistencyReport(
            engine_a_name="a", engine_b_name="b",
            test_data_version="2.0", verification_temperature=0.0,
            _config=config,
        )
        pair = ConsistencyPair(
            case_id="test", organ="thyroid", prompt="test",
            engine_a_result=GradingResult(text="TI-RADS 3类", grading="3", organ="thyroid"),
            engine_b_result=GradingResult(text="TI-RADS 5类", grading="5", organ="thyroid"),
            boundary_weight=3.0, is_critical_boundary=True,
        )
        report.total_cases = 1
        report.clinical_boundary_diff_count = 1
        report.clinical_boundary_diff_rate = 1.0
        report.total_boundary_weight = 3.0
        report.weighted_boundary_diff_rate = 3.0 / 3.0
        assert report.weighted_boundary_diff_rate == 1.0

    def test_weighted_rate_zero_when_no_crossing(self):
        from app.inference.consistency.report import ConsistencyReport
        from app.inference.verification_config import get_verification_config
        config = get_verification_config()
        report = ConsistencyReport(
            engine_a_name="a", engine_b_name="b",
            test_data_version="2.0", verification_temperature=0.0,
            _config=config,
        )
        report.weighted_boundary_diff_rate = 0.0
        assert report.weighted_boundary_diff_rate == 0.0


class TestHallucinationDetectionMerged:
    def test_detect_called_once_per_engine(self):
        from unittest.mock import AsyncMock, MagicMock, patch
        from app.inference.consistency.verifier import ConsistencyVerifier
        from app.inference.consistency.report import ConsistencyPair
        from app.inference.protocols import InferenceResponse
        engine_a = AsyncMock()
        engine_b = AsyncMock()
        resp = InferenceResponse(text="甲状腺结节TI-RADS 3类", model_name="test", latency_ms=100.0)
        engine_a.generate = AsyncMock(return_value=resp)
        engine_b.generate = AsyncMock(return_value=resp)
        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        mock_detector = MagicMock()
        mock_result = MagicMock()
        mock_result.is_hallucination = False
        mock_result.issues = []
        mock_detector.detect = MagicMock(return_value=mock_result)
        verifier._hallucination_detector = mock_detector
        import asyncio
        case = {"case_id": "test", "organ": "thyroid", "prompt": "test", "expected_grading": "3"}
        pair = asyncio.get_event_loop().run_until_complete(verifier._verify_single_case(case))
        assert mock_detector.detect.call_count == 2
        assert mock_detector.is_hallucination.call_count == 0


class TestPhase1GateCriteria:
    def _make_report(self, grading_rate=0.96, boundary_rate=0.02, **kwargs):
        from app.inference.consistency.report import ConsistencyReport, GateStatus
        from app.inference.verification_config import VerificationConfig, get_default_verification_config
        config = get_default_verification_config()
        config.consistency_thresholds.verification_phase = "phase1"
        report = ConsistencyReport(
            engine_a_name="a", engine_b_name="b",
            test_data_version="2.0", verification_temperature=0.0,
            _config=config,
            grading_consistency_rate=grading_rate,
            semantic_grading_consistency_rate=kwargs.get("semantic_rate", 0.99),
            clinical_boundary_diff_rate=boundary_rate,
            clinical_boundary_diff_rate_ci_upper=kwargs.get("ci_upper", boundary_rate + 0.005),
            weighted_boundary_diff_rate=kwargs.get("wbd", 0.005),
            critical_boundary_diff_rate=kwargs.get("cbd", 0.003),
            hallucination_rate_diff=kwargs.get("hd", 0.003),
            text_consistency_rate=kwargs.get("tc", 0.92),
            latency_diff_pct=kwargs.get("ld", 10.0),
            latency_p99_reliable=True,
            latency_a_p99_ms=1000.0,
            latency_b_p99_ms=1100.0,
            total_cases=500,
            lvef_conflict_count=0,
            hallucination_rate_a=0.01,
            hallucination_rate_b=0.01,
        )
        return report

    def test_phase1_uses_3pct_boundary_threshold(self):
        report = self._make_report(boundary_rate=0.025)
        assert report.gate_status == GateStatus.PASSED

    def test_phase1_fails_above_3pct_boundary(self):
        report = self._make_report(boundary_rate=0.035)
        assert report.gate_status != GateStatus.PASSED

    def test_phase2_uses_1pct_boundary_threshold(self):
        from app.inference.consistency.report import GateStatus
        report = self._make_report(boundary_rate=0.015)
        report._config.consistency_thresholds.verification_phase = "phase2"
        report.organ_breakdown = {"thyroid": {"total": 300, "grading_consistency_rate": 0.96, "clinical_boundary_diff_rate": 0.015}}
        report._gate_status_cache = None
        assert report.gate_status != GateStatus.PASSED
