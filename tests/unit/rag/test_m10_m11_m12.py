"""M10 患者解释话术 / M11 科研指标 / M12 跨模态对照单测。"""
import pytest

from app.knowledge.cross_modality import MAX_HINTS, hints_for
from app.knowledge.patient_explanation import parse_patient_explanation
from app.knowledge.research_metrics import (
    HASH_LENGTH,
    build_confidence_distribution,
    build_feature_vector_hash,
    build_research_metrics,
    parse_blood_flow,
    parse_elasticity_score,
)
from app.models.schemas import Measurement

pytestmark = [pytest.mark.track_rag, pytest.mark.track_data]

BLOCK = """分级: TR4
患者解释:
解释: 结节多数为良性，本次超声未见明显恶性征象
安抚: 不必过度担心，规律复查即可
下一步: 建议 6 个月后复查超声
"""

FLOW = "PSV 45cm/s，EDV 12cm/s，RI 0.72，弹性评分 3"


class TestPatientExplanation:
    def test_parses_block(self):
        result = parse_patient_explanation(BLOCK)
        assert result.explanation == '结节多数为良性，本次超声未见明显恶性征象'
        assert result.reassurance == '不必过度担心，规律复查即可'
        assert result.next_step == '建议 6 个月后复查超声'
        assert result.source_reference

    def test_does_not_swallow_prefix_from_block_header(self):
        """块头 "患者解释:" 后面换行，不能把下一行整段（含"解释: "前缀）当值。"""
        result = parse_patient_explanation(BLOCK)
        assert not result.explanation.startswith('解释')

    def test_parses_inline_form(self):
        result = parse_patient_explanation('患者解释: 结节为良性，建议复查')
        assert result.explanation == '结节为良性，建议复查'
        assert result.reassurance is None

    def test_only_explanation_required(self):
        result = parse_patient_explanation('患者解释:\n解释: 需要复查\n')
        assert result.explanation == '需要复查'
        assert result.next_step is None

    @pytest.mark.parametrize("text", [None, "", "   ", "分级: TR4", "患者解释:"])
    def test_returns_none_without_content(self, text):
        """没有话术就返回 None——绝不拼凑一句看似合理的安抚话。"""
        assert parse_patient_explanation(text) is None


class TestBloodFlowAndElasticity:
    def test_parses_all_parameters(self):
        flow = parse_blood_flow(FLOW)
        assert flow.psv == 45.0
        assert flow.edv == 12.0
        assert flow.ri == 0.72
        assert flow.unit == 'cm/s'

    def test_converts_meter_per_second(self):
        flow = parse_blood_flow("PSV 0.45m/s")
        assert flow.psv == 45.0

    def test_requires_unit(self):
        """没有单位的数字不取（避免把无关数值当成测量值）。"""
        assert parse_blood_flow("PSV 45") is None

    @pytest.mark.parametrize("text", [None, "", "血流信号丰富", "未见明显血流"])
    def test_no_explicit_numbers(self, text):
        assert parse_blood_flow(text) is None

    def test_elasticity_score(self):
        assert parse_elasticity_score(FLOW) == (3.0, 'score')
        assert parse_elasticity_score("未见异常") == (None, None)


class TestFeatureVectorHash:
    def test_deterministic_and_order_insensitive(self):
        first = build_feature_vector_hash(['micro', 'hypoechoic'])
        second = build_feature_vector_hash(['hypoechoic', 'micro'])
        assert first == second
        assert len(first) == HASH_LENGTH

    def test_changes_with_input(self):
        assert build_feature_vector_hash(['micro']) != build_feature_vector_hash(['clear'])

    def test_includes_model_and_kb_version(self):
        base = build_feature_vector_hash(['micro'], model_version='m1', kb_version='kb1')
        other = build_feature_vector_hash(['micro'], model_version='m2', kb_version='kb1')
        assert base != other

    def test_includes_measurement(self):
        without = build_feature_vector_hash(['micro'])
        with_measurement = build_feature_vector_hash(
            ['micro'], Measurement(long_mm=12.0)
        )
        assert without != with_measurement

    def test_empty_features_returns_none(self):
        assert build_feature_vector_hash([]) is None


class TestConfidenceDistribution:
    def test_sums_to_one(self):
        dist = build_confidence_distribution(0.87, 0.72)
        assert round(dist.benign + dist.malignant + dist.indeterminate, 4) == 1.0

    def test_malignant_equals_probability(self):
        assert build_confidence_distribution(0.6, 1.0).malignant == 0.6

    def test_low_confidence_raises_indeterminate(self):
        confident = build_confidence_distribution(0.5, 1.0)
        unsure = build_confidence_distribution(0.5, 0.2)
        assert unsure.indeterminate > confident.indeterminate
        assert confident.indeterminate == 0.0

    def test_non_negative(self):
        dist = build_confidence_distribution(1.0, 0.0)
        assert dist.benign >= 0.0 and dist.indeterminate >= 0.0 and dist.malignant >= 0.0

    def test_both_missing_returns_none(self):
        assert build_confidence_distribution(None, None) is None

    def test_clamps_out_of_range_inputs(self):
        dist = build_confidence_distribution(1.5, -0.5)
        assert dist.malignant == 1.0


class TestBuildResearchMetrics:
    def test_builds_hash_and_distribution_without_extra_modalities(self):
        metrics = build_research_metrics(['micro'], probability=0.8, confidence=0.7)
        assert metrics.ai_features.feature_vector_hash
        assert metrics.confidence_distribution is not None
        # 未提供弹性/血流数据时不得凭空虚造
        assert metrics.elasticity_score is None
        assert metrics.blood_flow is None

    def test_includes_explicit_flow_parameters(self):
        metrics = build_research_metrics(['micro'], text=FLOW, probability=0.8)
        assert metrics.blood_flow.psv == 45.0
        assert metrics.elasticity_score == 3.0

    def test_no_content_returns_none(self):
        assert build_research_metrics([]) is None

    def test_failure_is_swallowed(self, monkeypatch):
        import app.knowledge.research_metrics as module

        def _boom(*_args, **_kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(module, "build_feature_vector_hash", _boom)
        assert build_research_metrics(['micro']) is None


class TestCrossModalityHints:
    def test_thyroid_microcalcification_hints(self):
        hints = hints_for('thyroid', ['micro'])
        assert hints
        assert {hint.modality for hint in hints} >= {'CT', 'pathology'}
        assert all(hint.finding and hint.source_reference for hint in hints)

    def test_liver_hints(self):
        hints = hints_for('liver', ['internal'])
        assert {hint.modality for hint in hints} >= {'CT', 'MRI'}

    def test_cardiac_hints(self):
        hints = hints_for('cardiac', ['reduced_systolic_function'])
        assert hints[0].modality == 'MRI'

    def test_deduplicates_and_caps(self):
        hints = hints_for('thyroid', ['micro', 'micro', 'posterior_attenuation'])
        assert len(hints) <= MAX_HINTS
        keys = [(hint.modality, hint.finding) for hint in hints]
        assert len(keys) == len(set(keys))

    @pytest.mark.parametrize("organ,features", [
        (None, ['micro']),
        ('thyroid', []),
        ('spleen', ['micro']),
        ('thyroid', ['unknown_feature']),
    ])
    def test_no_match_returns_empty(self, organ, features):
        assert hints_for(organ, features) == []
