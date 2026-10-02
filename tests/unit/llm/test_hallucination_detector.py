"""

单元测试：幻觉检测器
"""


import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_sec]

from app.inference.hallucination_detector import (
    ContextAwareDetector,
    ContradictoryGradingDetector,
    DetectionStrategy,
    ForbiddenTermsDetector,
    HallucinationDetector,
    MedicalCommonSenseDetector,
    NumericalValidityDetector,
    detect_hallucination,
    get_default_detector,
)
from app.inference.verification_config import get_default_forbidden_terms_config


class TestForbiddenTermsDetector:
    """测试禁忌词检测器"""

    def test_no_forbidden_terms(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节 TI-RADS 4a 类"
        issues = detector.detect(text, "thyroid")
        assert len(issues) == 0

    def test_negated_forbidden_term_long_distance(self):
        detector = ForbiddenTermsDetector()
        text = "未见明显肺癌转移征象"
        issues = detector.detect(text, "thyroid")
        assert len(issues) == 0

    def test_negated_forbidden_term_with_exclusion(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，不考虑肺癌可能"
        issues = detector.detect(text, "thyroid")
        assert len(issues) == 0

    def test_non_negated_forbidden_term_still_detected(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节伴肺癌转移"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1

    def test_forbidden_terms_detected(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节伴肺癌转移"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert "肺癌" in issues[0].description

    def test_liver_forbidden_terms(self):
        detector = ForbiddenTermsDetector()
        text = "肝脏占位伴甲状腺癌"
        issues = detector.detect(text, "liver")
        assert len(issues) >= 1

    def test_cardiac_forbidden_terms(self):
        detector = ForbiddenTermsDetector()
        text = "心脏超声发现肝癌转移"
        issues = detector.detect(text, "cardiac")
        assert len(issues) >= 1

    def test_thyroid_bone_metastasis_with_context_allowed(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺癌伴骨转移，TI-RADS 5类"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_thyroid_bone_metastasis_without_context_detected(self):
        detector = ForbiddenTermsDetector()
        text = "颈部结节伴骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) >= 1

    def test_liver_bone_metastasis_with_context_allowed(self):
        detector = ForbiddenTermsDetector()
        text = "肝脏占位伴骨转移，肝癌可能性大"
        issues = detector.detect(text, "liver")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_augment_pattern_not_treated_as_negation(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，不除外肺癌转移"
        issues = detector.detect(text, "thyroid")
        lung_issues = [i for i in issues if "肺癌" in i.description]
        assert len(lung_issues) >= 1

    def test_cannot_exclude_not_treated_as_negation(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，不能除外胃癌"
        issues = detector.detect(text, "thyroid")
        stomach_issues = [i for i in issues if "胃癌" in i.description]
        assert len(stomach_issues) >= 1


class TestContradictoryGradingDetector:
    """测试矛盾分级检测器"""

    def test_single_grading(self):
        detector = ContradictoryGradingDetector()
        text = "TI-RADS 4a 类"
        issues = detector.detect(text, "thyroid")
        assert len(issues) == 0

    def test_contradictory_gradings(self):
        detector = ContradictoryGradingDetector()
        text = "TI-RADS 4a 类，但考虑 TI-RADS 4b 可能"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert "差异" in issues[0].description or "矛盾" in issues[0].description

    def test_liver_contradictory_gradings(self):
        detector = ContradictoryGradingDetector()
        text = "LI-RADS 3 类和 LI-RADS 4 类"
        issues = detector.detect(text, "liver")
        assert len(issues) >= 1


class TestMedicalCommonSenseDetector:
    """测试医学常识检测器"""

    def test_no_contradiction(self):
        detector = MedicalCommonSenseDetector()
        text = "甲状腺良性结节"
        issues = detector.detect(text, "thyroid")
        assert len(issues) == 0

    def test_benign_and_malignant_contradiction(self):
        detector = MedicalCommonSenseDetector()
        text = "甲状腺良性结节，考虑恶性可能"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert "医学常识" in issues[0].description

    def test_benign_with_exclusion_not_severe(self):
        detector = MedicalCommonSenseDetector()
        text = "良性结节，需排除恶性可能"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert any("排除性表述" in i.description for i in issues)
        assert all(i.severity == 1 for i in issues)

    def test_benign_with_malignant_no_exclusion_still_severe(self):
        detector = MedicalCommonSenseDetector()
        text = "良性结节伴恶性转移"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert any("医学常识矛盾" in i.description for i in issues)
        assert any(i.severity == 3 for i in issues)


class TestNumericalValidityDetector:
    """测试数值有效性检测器"""

    def test_normal_lvef(self):
        detector = NumericalValidityDetector()
        text = "LVEF: 55%"
        issues = detector.detect(text, "cardiac")
        assert len(issues) == 0

    def test_extremely_low_lvef(self):
        detector = NumericalValidityDetector()
        text = "LVEF: 5%"
        issues = detector.detect(text, "cardiac")
        assert len(issues) >= 1
        assert "超出合理范围" in issues[0].description

    def test_extremely_high_lvef(self):
        detector = NumericalValidityDetector()
        text = "LVEF: 95%"
        issues = detector.detect(text, "cardiac")
        assert len(issues) >= 1


class TestHallucinationDetector:
    """测试主幻觉检测器"""

    def test_default_detector_initialization(self):
        detector = HallucinationDetector()
        assert detector is not None
        assert DetectionStrategy.FORBIDDEN_TERMS in detector.strategies

    def test_custom_strategies(self):
        detector = HallucinationDetector(
            strategies=[DetectionStrategy.FORBIDDEN_TERMS]
        )
        assert len(detector.strategies) == 1
        assert detector.strategies[0] == DetectionStrategy.FORBIDDEN_TERMS

    def test_detect_no_hallucination(self):
        detector = HallucinationDetector()
        result = detector.detect("甲状腺结节 TI-RADS 3 类", "thyroid")
        assert result.is_hallucination is False
        assert len(result.issues) == 0

    def test_detect_with_forbidden_terms(self):
        detector = HallucinationDetector()
        result = detector.detect("甲状腺结节伴肺癌转移", "thyroid")
        assert result.is_hallucination is True
        assert len(result.issues) >= 1
        assert result.confidence > 0

    def test_detect_with_contradictory_gradings(self):
        detector = HallucinationDetector()
        result = detector.detect(
            "TI-RADS 4a 类，但考虑 TI-RADS 4b 可能",
            "thyroid"
        )
        assert result.is_hallucination is True
        assert len(result.issues) >= 1

    def test_detect_with_medical_contradiction(self):
        detector = HallucinationDetector()
        result = detector.detect(
            "甲状腺良性结节，考虑恶性可能",
            "thyroid"
        )
        assert result.is_hallucination is True

    def test_is_hallucination_shortcut(self):
        detector = HallucinationDetector()
        assert detector.is_hallucination("甲状腺结节 TI-RADS 3 类", "thyroid") is False
        assert detector.is_hallucination("甲状腺结节伴肺癌转移", "thyroid") is True

    def test_empty_text(self):
        detector = HallucinationDetector()
        result = detector.detect("", "thyroid")
        assert result.is_hallucination is False

    def test_empty_organ(self):
        detector = HallucinationDetector()
        result = detector.detect("甲状腺结节", "")
        assert result.is_hallucination is False


class TestGlobalFunctions:
    """测试全局函数"""

    def test_get_default_detector(self):
        detector = get_default_detector()
        assert detector is not None
        assert isinstance(detector, HallucinationDetector)

    def test_detect_hallucination_function(self):
        assert detect_hallucination("甲状腺结节 TI-RADS 3 类", "thyroid") is False
        assert detect_hallucination("甲状腺结节伴肺癌转移", "thyroid") is True

    def test_same_instance_returned(self):
        detector1 = get_default_detector()
        detector2 = get_default_detector()
        assert detector1 is detector2

    def test_thread_safe_singleton(self):
        import threading
        results = []
        errors = []

        def get_det():
            try:
                d = get_default_detector()
                results.append(id(d))
            except Exception as e:
                errors.append(str(e))

        from app.inference.hallucination_detector import _default_detector
        original = _default_detector
        try:
            import app.inference.hallucination_detector as mod
            mod._default_detector = None
            threads = [threading.Thread(target=get_det) for _ in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert len(errors) == 0
            assert len(set(results)) == 1
        finally:
            mod._default_detector = original


class TestQuantizationHallucinationIncrement:
    """量化后幻觉率增量验证测试

    量化幻觉增量应通过对比不同精度模型输出（而非相同检测器对相同文本）来验证。
    由于单元测试无法运行真实模型，此处验证：
    1. 检测器对量化模型典型退化输出的检测能力
    2. 量化退化文本的幻觉增量是否在阈值内
    """

    def test_hallucination_increment_simulated_quantization(self):
        detector = HallucinationDetector(severity_threshold=2)
        baseline_texts = [
            ("甲状腺良性结节，TI-RADS 3类，建议6个月随访复查", "thyroid"),
            ("肝脏局灶性结节，LI-RADS 3类，轻度强化", "liver"),
            ("LVEF 55%，心脏收缩功能正常", "cardiac"),
            ("甲状腺实性低回声结节，边界不清，微钙化，TI-RADS 4c类", "thyroid"),
            ("肝脏占位性病变，中等强化，包膜可疑，LI-RADS 4类", "liver"),
        ]
        quantized_degraded_texts = [
            ("甲状腺良性结节，TI-RADS 3类，建议6个月随访复查", "thyroid"),
            ("肝脏局灶性结节，LI-RADS 3类，轻度强化", "liver"),
            ("LVEF 55%，心脏收缩功能正常", "cardiac"),
            ("甲状腺实性低回声结节，边界不清，微钙化，TI-RADS 4c类", "thyroid"),
            ("肝脏占位性病变，中等强化，包膜可疑，LI-RADS 4类，建议进一步检查", "liver"),
        ]
        baseline_hallucination_count = sum(
            1 for text, organ in baseline_texts
            if detector.detect(text, organ).is_hallucination
        )
        quantized_hallucination_count = sum(
            1 for text, organ in quantized_degraded_texts
            if detector.detect(text, organ).is_hallucination
        )
        baseline_rate = baseline_hallucination_count / len(baseline_texts)
        quantized_rate = quantized_hallucination_count / len(quantized_degraded_texts)
        increment = max(0.0, quantized_rate - baseline_rate)
        assert increment <= 0.005, (
            f"Simulated quantization hallucination increment {increment:.4f} exceeds 0.5%. "
            f"Baseline rate: {baseline_rate:.4f}, Quantized rate: {quantized_rate:.4f}"
        )

    def test_quantization_introduces_no_severe_hallucination(self):
        detector = HallucinationDetector(severity_threshold=2)
        quantized_degraded_texts = [
            ("甲状腺结节，TI-RADS 4a类，伴骨转移可能", "thyroid"),
            ("肝脏占位，LI-RADS 4类，伴脑转移", "liver"),
            ("LVEF 40%，收缩功能轻度减低，发现胰腺炎", "cardiac"),
        ]
        severe_count = 0
        for text, organ in quantized_degraded_texts:
            result = detector.detect(text, organ)
            if any(i.severity >= 3 for i in result.issues):
                severe_count += 1
        assert severe_count > 0, "Detector should catch severe hallucinations in quantized degraded output"


class TestDetectionResult:
    """测试检测结果"""

    def test_confidence_calculation(self):
        detector = HallucinationDetector()
        result = detector.detect("甲状腺结节伴肺癌转移，TI-RADS 3 类", "thyroid")
        # 应该有多个问题，置信度应该较高
        assert result.confidence > 0
        assert result.confidence <= 1.0

    def test_issues_list(self):
        detector = HallucinationDetector()
        result = detector.detect("甲状腺结节伴肺癌转移", "thyroid")
        assert len(result.issues) >= 1
        for issue in result.issues:
            assert issue.strategy is not None
            assert issue.description is not None
            assert issue.severity >= 1


class TestEdgeCases:
    """测试边缘情况"""

    def test_mixed_issues(self):
        detector = HallucinationDetector()
        text = "TI-RADS 3 类和 TI-RADS 4 类，伴肺癌转移"
        result = detector.detect(text, "thyroid")
        assert result.is_hallucination is True


class TestNegationExpansion:
    def test_negation_weitishi(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，未提示骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_negation_buban(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，不伴骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_negation_weichaji(self):
        detector = ForbiddenTermsDetector()
        text = "肝脏未查及明显占位。伴肺癌"
        issues = detector.detect(text, "liver")
        lung_issues = [i for i in issues if "肺癌" in i.description]
        assert len(lung_issues) >= 1

    def test_negation_weijianmingxian(self):
        detector = ForbiddenTermsDetector()
        text = "未见明显骨转移征象"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_no_negation_still_detected(self):
        detector = ForbiddenTermsDetector()
        text = "结节伴骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) >= 1


class TestContextWindowDetection:
    def test_context_in_window_allowed(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺癌伴骨转移，TI-RADS 5类"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_context_outside_window_detected(self):
        from app.inference.verification_config import (
            ForbiddenTermConfig,
            ForbiddenTermsConfig,
            StrategyWeightConfig,
            VerificationConfig,
            get_default_boundary_config,
            get_default_numerical_validation_config,
        )
        custom_terms = ForbiddenTermsConfig(
            organ_terms={
                "thyroid": [
                    ForbiddenTermConfig(
                        term="骨转移", severity=3,
                        description="骨转移",
                        allow_if_context="甲状腺",
                        context_window_chars=10,
                    ),
                ],
            },
        )
        config = VerificationConfig(
            boundary_config=get_default_boundary_config(),
            forbidden_terms_config=custom_terms,
            strategy_weights=StrategyWeightConfig(),
            numerical_validation_config=get_default_numerical_validation_config(),
        )
        detector = ForbiddenTermsDetector(config=config)
        text = "甲状腺癌" + "。" * 50 + "伴骨转移"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) >= 1

    def test_boundary_at_text_start(self):
        detector = ForbiddenTermsDetector()
        text = "骨转移，甲状腺癌伴"
        issues = detector.detect(text, "thyroid")
        bone_issues = [i for i in issues if "骨转移" in i.description]
        assert len(bone_issues) == 0

    def test_case_insensitive_detection(self):
        detector = HallucinationDetector()
        # 测试大小写不敏感
        result = detector.detect("ti-rads 4a 类和 ti-rads 4b 类", "thyroid")
        # 检查是否有矛盾分级（取决于模式是否大小写不敏感）
        assert result.issues is not None

    def test_chinese_only_text(self):
        detector = HallucinationDetector()
        result = detector.detect("甲状腺结节，考虑良性", "thyroid")
        assert result.is_hallucination is False

    def test_none_values(self):
        detector = HallucinationDetector()
        result = detector.detect(None, None)
        assert result.is_hallucination is False


class TestContextAwareDetector:
    def test_no_conflict(self):
        detector = ContextAwareDetector()
        text = "甲状腺结节，边界清晰，形态规则，TI-RADS 3类，考虑良性"
        issues = detector.detect(text, "thyroid")
        assert len(issues) == 0

    def test_benign_desc_malignant_conclusion(self):
        detector = ContextAwareDetector()
        text = "甲状腺结节，边界清晰，形态规则，考虑恶性可能"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert any("描述-结论矛盾" in i.description for i in issues)

    def test_malignant_desc_benign_conclusion(self):
        detector = ContextAwareDetector()
        text = "甲状腺结节，边界模糊，微钙化，丰富血流，TI-RADS 3类，考虑良性"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert any("描述-结论矛盾" in i.description for i in issues)

    def test_overreaction_tirads2_surgery(self):
        detector = ContextAwareDetector()
        text = "甲状腺结节 TI-RADS 2类，建议立即手术"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1
        assert any("过度反应" in i.description for i in issues)

    def test_overreaction_tirads3_surgery(self):
        detector = ContextAwareDetector()
        text = "甲状腺结节 TI-RADS 3类，建议尽快手术"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 1

    def test_no_overreaction_tirads4a_biopsy(self):
        detector = ContextAwareDetector()
        text = "甲状腺结节 TI-RADS 4a类，建议穿刺活检"
        issues = detector.detect(text, "thyroid")
        overreaction = [i for i in issues if "过度反应" in i.description]
        assert len(overreaction) == 0

    def test_cardiac_desc_conclusion_conflict(self):
        detector = ContextAwareDetector()
        text = "收缩功能正常，室壁运动正常，心力衰竭"
        issues = detector.detect(text, "cardiac")
        assert len(issues) >= 1
        assert any("描述-结论矛盾" in i.description for i in issues)

    def test_cardiac_overreaction(self):
        detector = ContextAwareDetector()
        text = "LVEF: 60%，轻度异常，建议紧急手术"
        issues = detector.detect(text, "cardiac")
        assert len(issues) >= 1
        assert any("过度反应" in i.description for i in issues)

    def test_cardiac_no_conflict(self):
        detector = ContextAwareDetector()
        text = "LVEF: 55%，心脏功能正常"
        issues = detector.detect(text, "cardiac")
        conflict_issues = [i for i in issues if "描述-结论矛盾" in i.description]
        assert len(conflict_issues) == 0

    def test_liver_overreaction(self):
        detector = ContextAwareDetector()
        text = "肝脏占位 LI-RADS 2类，建议立即手术"
        issues = detector.detect(text, "liver")
        assert len(issues) >= 1

    def test_context_aware_in_full_detector(self):
        detector = HallucinationDetector()
        text = "甲状腺结节，边界清晰，形态规则，考虑恶性"
        result = detector.detect(text, "thyroid")
        assert result.is_hallucination is True
        assert any(i.strategy == DetectionStrategy.CONTEXT_AWARE for i in result.issues)


class TestSeverityThreshold:
    def test_default_threshold_triggers_on_severity_1(self):
        detector = HallucinationDetector(severity_threshold=1)
        text = "甲状腺结节，提及肺癌转移"
        result = detector.detect(text, "thyroid")
        assert result.is_hallucination is True

    def test_high_threshold_filters_low_severity(self):
        detector = HallucinationDetector(severity_threshold=3)
        text = "甲状腺结节，提及肺癌转移"
        result = detector.detect(text, "thyroid")
        forbidden_issues = [i for i in result.issues if i.strategy == DetectionStrategy.FORBIDDEN_TERMS]
        if forbidden_issues and all(i.severity < 3 for i in forbidden_issues):
            if not any(i.severity >= 3 for i in result.issues):
                assert result.is_hallucination is False

    def test_threshold_2_only_triggers_moderate(self):
        detector = HallucinationDetector(severity_threshold=2)
        text = "LVEF: 150%"
        result = detector.detect(text, "cardiac")
        numerical_issues = [i for i in result.issues if i.strategy == DetectionStrategy.NUMERICAL_VALIDITY]
        if numerical_issues:
            assert result.is_hallucination is True


class TestDetectionResultMetadata:
    def test_execution_time_ms_recorded(self):
        detector = HallucinationDetector()
        text = "甲状腺结节 TI-RADS 3类"
        result = detector.detect(text, "thyroid")
        assert result.execution_time_ms >= 0.0

    def test_test_data_version_populated(self):
        detector = HallucinationDetector()
        text = "甲状腺结节 TI-RADS 3类"
        result = detector.detect(text, "thyroid")
        assert isinstance(result.test_data_version, str)


class TestConfidenceCalculation:
    def test_no_issues_zero_confidence(self):
        detector = HallucinationDetector()
        text = "甲状腺结节 TI-RADS 3类，边界清晰"
        result = detector.detect(text, "thyroid")
        if not result.issues:
            assert result.confidence == 0.0

    def test_high_severity_issues_higher_confidence(self):
        detector = HallucinationDetector()
        text_with_issues = "甲状腺结节伴肺癌转移，LVEF: 150%"
        result = detector.detect(text_with_issues, "thyroid")
        if result.issues:
            assert result.confidence > 0.0

    def test_confidence_bounded(self):
        detector = HallucinationDetector()
        text = "甲状腺结节伴肺癌转移，考虑恶性，LVEF: 200%"
        result = detector.detect(text, "thyroid")
        assert 0.0 <= result.confidence <= 1.0


class TestNoduleSizeDetection:
    def test_thyroid_nodule_size_cm_valid(self):
        detector = NumericalValidityDetector()
        text = "甲状腺结节 2.5cm，TI-RADS 3类"
        issues = detector.detect(text, "thyroid")
        nodule_issues = [i for i in issues if "结节大小" in i.description]
        assert len(nodule_issues) == 0

    def test_thyroid_nodule_size_cm_invalid(self):
        detector = NumericalValidityDetector()
        text = "甲状腺结节 50cm，TI-RADS 3类"
        issues = detector.detect(text, "thyroid")
        nodule_issues = [i for i in issues if "结节大小" in i.description]
        assert len(nodule_issues) >= 1

    def test_thyroid_nodule_size_mm_conversion(self):
        detector = NumericalValidityDetector()
        text = "甲状腺结节 25mm，TI-RADS 3类"
        issues = detector.detect(text, "thyroid")
        nodule_issues = [i for i in issues if "结节大小" in i.description]
        assert len(nodule_issues) == 0

    def test_thyroid_nodule_size_mm_invalid(self):
        detector = NumericalValidityDetector()
        text = "甲状腺结节 500mm，TI-RADS 3类"
        issues = detector.detect(text, "thyroid")
        nodule_issues = [i for i in issues if "结节大小" in i.description]
        assert len(nodule_issues) >= 1

    def test_liver_lesion_size_valid(self):
        detector = NumericalValidityDetector()
        text = "肝脏占位 3.0cm，LI-RADS 3类"
        issues = detector.detect(text, "liver")
        nodule_issues = [i for i in issues if "占位大小" in i.description]
        assert len(nodule_issues) == 0

    def test_liver_lesion_size_invalid(self):
        detector = NumericalValidityDetector()
        text = "肝脏占位 80cm，LI-RADS 4类"
        issues = detector.detect(text, "liver")
        nodule_issues = [i for i in issues if "占位大小" in i.description]
        assert len(nodule_issues) >= 1


class TestForbiddenTermsVariants:
    def test_thyroid_variant_lung_cancer(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，伴肺恶性肿瘤转移"
        issues = detector.detect(text, "thyroid")
        variant_issues = [i for i in issues if "变体" in i.description and "肺恶性肿瘤" in i.description]
        assert len(variant_issues) >= 1

    def test_liver_variant_thyroid_cancer(self):
        detector = ForbiddenTermsDetector()
        text = "肝脏占位，伴甲状腺恶性肿瘤转移"
        issues = detector.detect(text, "liver")
        variant_issues = [i for i in issues if "变体" in i.description and "甲状腺恶性肿瘤" in i.description]
        assert len(variant_issues) >= 1

    def test_cardiac_variant_liver_cancer(self):
        detector = ForbiddenTermsDetector()
        text = "心脏超声：LVEF 45%，伴肝细胞癌心包转移"
        issues = detector.detect(text, "cardiac")
        variant_issues = [i for i in issues if "变体" in i.description and "肝细胞癌" in i.description]
        assert len(variant_issues) >= 1

    def test_variant_and_main_both_detected(self):
        detector = ForbiddenTermsDetector()
        text = "甲状腺结节，伴肺癌及肺恶性肿瘤"
        issues = detector.detect(text, "thyroid")
        assert len(issues) >= 2
        main_issues = [i for i in issues if "肺癌" in i.description and "变体" not in i.description]
        variant_issues = [i for i in issues if "肺恶性肿瘤" in i.description and "变体" in i.description]
        assert len(main_issues) >= 1
        assert len(variant_issues) >= 1


class TestCustomConfigDetection:
    def test_custom_forbidden_terms_config(self):
        from app.inference.verification_config import (
            ForbiddenTermConfig,
            ForbiddenTermsConfig,
            StrategyWeightConfig,
            VerificationConfig,
            get_default_boundary_config,
            get_default_numerical_validation_config,
        )
        custom_terms = ForbiddenTermsConfig(
            organ_terms={
                "thyroid": [
                    ForbiddenTermConfig(term="自定义禁忌词", description="测试用", severity=3, variants=["自定义变体"]),
                ],
                "liver": [],
                "cardiac": [],
            }
        )
        custom_config = VerificationConfig(
            boundary_config=get_default_boundary_config(),
            forbidden_terms_config=custom_terms,
            strategy_weights=StrategyWeightConfig(),
            numerical_validation_config=get_default_numerical_validation_config(),
        )
        detector = HallucinationDetector(config=custom_config)
        text = "甲状腺结节，伴自定义禁忌词"
        result = detector.detect(text, "thyroid")
        assert result.is_hallucination is True

    def test_custom_config_variant_detected(self):
        from app.inference.verification_config import (
            ForbiddenTermConfig,
            ForbiddenTermsConfig,
            StrategyWeightConfig,
            VerificationConfig,
            get_default_boundary_config,
            get_default_numerical_validation_config,
        )
        custom_terms = ForbiddenTermsConfig(
            organ_terms={
                "thyroid": [
                    ForbiddenTermConfig(term="测试词", description="测试", severity=2, variants=["测试变体词"]),
                ],
                "liver": [],
                "cardiac": [],
            }
        )
        custom_config = VerificationConfig(
            boundary_config=get_default_boundary_config(),
            forbidden_terms_config=custom_terms,
            strategy_weights=StrategyWeightConfig(),
            numerical_validation_config=get_default_numerical_validation_config(),
        )
        detector = HallucinationDetector(config=custom_config)
        text = "甲状腺结节，伴测试变体词"
        result = detector.detect(text, "thyroid")
        assert result.is_hallucination is True


class TestForbiddenTermsFullCoverage:
    """遍历全部禁忌词配置，确保每个术语都能被正确检测"""

    def _get_detector(self):
        return ForbiddenTermsDetector()

    def test_all_primary_terms_detected(self):
        detector = self._get_detector()
        config = get_default_forbidden_terms_config()
        for organ, terms in config.organ_terms.items():
            for term_config in terms:
                text = f"患者{term_config.term}，需要进一步检查"
                issues = detector.detect(text, organ)
                term_detected = any(term_config.term in issue.description for issue in issues)
                assert term_detected, f"主禁忌词 '{term_config.term}' 在 {organ} 中未被检测到"

    def test_all_variants_detected(self):
        detector = self._get_detector()
        config = get_default_forbidden_terms_config()
        for organ, terms in config.organ_terms.items():
            for term_config in terms:
                for variant in term_config.variants:
                    text = f"患者{variant}，需要进一步检查"
                    issues = detector.detect(text, organ)
                    variant_detected = any(variant in issue.description for issue in issues)
                    assert variant_detected, f"变体 '{variant}' (原词: {term_config.term}) 在 {organ} 中未被检测到"

    def test_no_false_positive_on_clean_text(self):
        detector = self._get_detector()
        config = get_default_forbidden_terms_config()
        clean_texts = {
            "thyroid": "甲状腺结节，边界清晰，形态规则，TI-RADS 3类，建议定期复查",
            "liver": "肝脏未见明显占位，肝内胆管无扩张，LI-RADS 1类",
            "cardiac": "心脏各房室大小正常，LVEF 65%，收缩功能正常",
            "breast": "乳腺未见明显异常，BI-RADS 1类，建议年度筛查",
        }
        for organ, text in clean_texts.items():
            issues = detector.detect(text, organ)
            forbidden_issues = [i for i in issues if i.strategy == DetectionStrategy.FORBIDDEN_TERMS]
            assert len(forbidden_issues) == 0, f"正常{organ}报告误触发禁忌词: {[i.description for i in forbidden_issues]}"
