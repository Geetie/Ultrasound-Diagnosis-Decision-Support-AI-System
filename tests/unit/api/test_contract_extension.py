"""M1~M12 契约扩展回归测试（契约先行批次的守卫）。

三处共享契约被一次性扩展了可选字段（智能体输出 / 诊断结果 / 导航标注）。
本文件锁定两个不变量：

1. **向后兼容**：既有构造点（生产中 20 余处、测试中 30 余处，全部使用关键字
   参数）不传新字段时行为与扩展前完全一致——新字段必须是"带默认值的可选"。
2. **字段可承载**：新字段能真正装下 M1~M12 需要的数据，并随 ``model_dump()``
   正确序列化（导航链路与 API 响应都依赖整体序列化）。
"""
import pytest
from pydantic import ValidationError

from app.agents.protocols import AgentResponse
from app.models.schemas import (
    AIExtractedFeatures,
    ClinicalAction,
    ClinicalActionUrgency,
    ConsentStatus,
    CrossModalityHint,
    DesensitizationStatus,
    DiagnosisResult,
    DifferentialDiagnosis,
    EdgeNavLabel,
    EdgeNavResultMessage,
    FusionStatus,
    ImageQuality,
    ImageQualityImpact,
    MalignancyRisk,
    MalignancyRiskBasis,
    MalignancyRiskLevel,
    Measurement,
    NavigationAnnotation,
    NavigationMessage,
    PatientExplanation,
    PlaneAssessment,
    PlaneType,
    PosteriorAcoustic,
    ResearchMetrics,
    RiskFlag,
    SuggestedAction,
)

pytestmark = pytest.mark.track_api


def _minimal_diagnosis_result(**overrides) -> DiagnosisResult:
    payload = {
        "request_id": "req-1",
        "status": "completed",
        "desensitization_status": DesensitizationStatus.FULL,
        "consent_status_at_output": ConsentStatus.GRANTED,
        "fusion_status": FusionStatus.NORMAL,
        "diagnosis": {"conclusion": "良性"},
        "processing_time_ms": 1234,
    }
    payload.update(overrides)
    return DiagnosisResult(**payload)


class TestAgentResponseBackwardCompatibility:
    def test_minimal_construction_still_works(self):
        resp = AgentResponse(agent_name="thyroid_specialist")
        assert resp.agent_name == "thyroid_specialist"
        assert resp.content == ""
        assert resp.confidence == 0.0

    def test_new_fields_default_to_empty(self):
        resp = AgentResponse(agent_name="thyroid_specialist")
        assert resp.measurement is None
        assert resp.malignancy_risk is None
        assert resp.patient_explanation is None
        assert resp.research_metrics is None
        assert resp.posterior_acoustic is None
        assert resp.differential_diagnoses == []
        assert resp.clinical_actions == []
        assert resp.cross_modality_hints == []

    def test_existing_positional_free_construction_unaffected(self):
        """既有调用点全用关键字参数；抽查最典型的一处写法。"""
        resp = AgentResponse(
            agent_name="liver_specialist",
            content="[liver] LI-RADS 4",
            confidence=0.72,
            evidence_chunk_ids=["c1"],
            reasoning_trace=["step"],
            latency_ms=12.5,
            needs_review=False,
            disease_code="UDT-LIVER-HCC",
            used_evidence_ids=["c1"],
            deviation_severity="none",
        )
        assert resp.confidence == 0.72
        assert resp.deviation_severity == "none"
        assert resp.measurement is None

    def test_mutable_defaults_are_not_shared(self):
        a = AgentResponse(agent_name="a")
        b = AgentResponse(agent_name="b")
        a.differential_diagnoses.append(
            DifferentialDiagnosis(rank=1, disease_name="x", confidence=0.5)
        )
        assert b.differential_diagnoses == []


class TestAgentResponseStructuredFields:
    def test_accepts_full_m_series_payload(self):
        resp = AgentResponse(
            agent_name="thyroid_specialist",
            measurement=Measurement(
                long_mm=12.0,
                short_mm=8.0,
                aspect_ratio=1.5,
                volume_mm3=402.0,
                pixel_spacing_source="dicom",
            ),
            differential_diagnoses=[
                DifferentialDiagnosis(rank=2, disease_name="结节性甲状腺肿", confidence=0.3),
                DifferentialDiagnosis(rank=1, disease_name="乳头状癌", confidence=0.6),
            ],
            malignancy_risk=MalignancyRisk(
                probability=0.62,
                level=MalignancyRiskLevel.MEDIUM,
                basis=[MalignancyRiskBasis(feature="micro_calcification", contribution=0.2)],
            ),
            clinical_actions=[
                ClinicalAction(
                    action_type="fine_needle_aspiration",
                    target_department="甲状腺外科",
                    urgency=ClinicalActionUrgency.SOON,
                )
            ],
            patient_explanation=PatientExplanation(explanation="多数结节为良性"),
            research_metrics=ResearchMetrics(
                elasticity_score=3.1,
                ai_features=AIExtractedFeatures(texture_homogeneity=0.8),
            ),
            cross_modality_hints=[
                CrossModalityHint(modality="CT", finding="等密度结节")
            ],
            posterior_acoustic=PosteriorAcoustic.ATTENUATION,
        )
        dumped = resp.model_dump()
        assert dumped["measurement"]["aspect_ratio"] == 1.5
        assert dumped["posterior_acoustic"] == "attenuation"
        assert dumped["malignancy_risk"]["level"] == "medium"
        assert len(dumped["differential_diagnoses"]) == 2


class TestDiagnosisResultContract:
    def test_minimal_construction_still_works(self):
        result = _minimal_diagnosis_result()
        assert result.version_compatibility == "compatible"
        assert result.measurement is None
        assert result.differential_diagnoses == []
        assert result.clinical_actions == []
        assert result.cross_modality_hints == []

    def test_new_fields_round_trip_through_model_dump(self):
        """API 响应走 ``response_model`` 序列化，新字段必须进入 JSON。"""
        result = _minimal_diagnosis_result(
            measurement=Measurement(volume_mm3=1500.0),
            malignancy_risk=MalignancyRisk(
                probability=0.15, level=MalignancyRiskLevel.LOW
            ),
            posterior_acoustic=PosteriorAcoustic.ENHANCEMENT,
            cross_modality_hints=[CrossModalityHint(modality="MRI", finding="T2 高信号")],
        )
        dumped = result.model_dump()
        assert dumped["measurement"]["volume_mm3"] == 1500.0
        assert dumped["malignancy_risk"]["probability"] == 0.15
        assert dumped["posterior_acoustic"] == "enhancement"
        assert dumped["cross_modality_hints"][0]["modality"] == "MRI"

    def test_measurement_pixel_spacing_source_defaults_to_default(self):
        assert Measurement().pixel_spacing_source == "default"
        assert Measurement(pixel_spacing_source="dicom").pixel_spacing_source == "dicom"


class TestPlaneAssessmentValidation:
    def test_plane_score_range_enforced(self):
        with pytest.raises(ValidationError):
            PlaneAssessment(plane_type=PlaneType.TRANSVERSE, is_standard=True, plane_score=1.5)

    def test_valid_assessment(self):
        assessment = PlaneAssessment(
            plane_type=PlaneType.LONGITUDINAL,
            is_standard=False,
            plane_score=0.4,
            organ_code="thyroid",
            issues=["关键结构不完整"],
            required_structures_visible=["左叶"],
        )
        assert assessment.plane_type == "longitudinal"
        assert assessment.is_standard is False


class TestNavigationContracts:
    def test_annotation_backward_compatible(self):
        annotation = NavigationAnnotation(label="nodule", confidence=0.9, bbox=[0.1, 0.1, 0.2, 0.2])
        assert annotation.risk_flag is None
        assert annotation.suggested_action is None

    def test_annotation_carries_bplus_mark(self):
        annotation = NavigationAnnotation(
            label="malignant_suspected",
            confidence=0.95,
            risk_flag=RiskFlag.HIGH,
            suggested_action=SuggestedAction.START_STANDARD_EVALUATION,
        )
        dumped = annotation.model_dump()
        assert dumped["risk_flag"] == "high"
        assert dumped["suggested_action"] == "start_standard_evaluation"

    def test_navigation_message_carries_frame_level_attributes(self):
        message = NavigationMessage(
            frame_id="f1",
            annotations=[NavigationAnnotation(label="nodule", confidence=0.8)],
            plane_assessment=PlaneAssessment(
                plane_type=PlaneType.TRANSVERSE, is_standard=True, plane_score=0.9
            ),
            image_quality=ImageQuality(
                overall_score=0.55,
                blur_score=0.4,
                issues=["轻微模糊"],
                diagnostic_impact=ImageQualityImpact.MINOR,
            ),
        )
        dumped = message.model_dump()
        assert dumped["plane_assessment"]["is_standard"] is True
        assert dumped["image_quality"]["overall_score"] == 0.55
        assert dumped["image_quality"]["diagnostic_impact"] == "minor"

    def test_navigation_message_defaults_without_frame_attributes(self):
        message = NavigationMessage(frame_id="f1")
        dumped = message.model_dump()
        assert dumped["plane_assessment"] is None
        assert dumped["image_quality"] is None

    def test_edge_nav_label_optional_extensions(self):
        label = EdgeNavLabel(label="nodule", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9)
        assert label.risk_flag is None
        assert label.suggested_action is None

        marked = EdgeNavLabel(
            label="malignant_suspected",
            x=0.1,
            y=0.1,
            width=0.2,
            height=0.2,
            confidence=0.95,
            risk_flag="high",
            suggested_action="start_standard_evaluation",
        )
        assert marked.risk_flag == "high"

    def test_edge_nav_result_message_frame_attributes(self):
        message = EdgeNavResultMessage(
            frame_id="f1",
            labels=[
                EdgeNavLabel(
                    label="nodule", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9
                )
            ],
            plane_assessment=PlaneAssessment(
                plane_type=PlaneType.LONGITUDINAL, is_standard=False, plane_score=0.3
            ),
        )
        dumped = message.model_dump()
        assert dumped["plane_assessment"]["plane_type"] == "longitudinal"
        assert dumped["image_quality"] is None
        # 旧边缘端缺字段时必须能反序列化（向后兼容）
        legacy = EdgeNavResultMessage.model_validate(
            {
                "type": "nav_result",
                "frame_id": "f2",
                "labels": [
                    {
                        "label": "nodule",
                        "x": 0.1,
                        "y": 0.1,
                        "width": 0.2,
                        "height": 0.2,
                        "confidence": 0.9,
                    }
                ],
            }
        )
        assert legacy.plane_assessment is None
        assert legacy.labels[0].risk_flag is None
