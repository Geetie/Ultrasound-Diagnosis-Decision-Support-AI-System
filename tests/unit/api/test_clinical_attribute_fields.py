"""M1~M12 临床属性从 findings 到对外契约的映射单测。

覆盖两段：
1. 管线侧 ``_fusion_result_to_dict``：只取 final_response、只写有值的字段；
2. 接口侧 ``clinical_attribute_fields``：逐字段容错（单字段结构异常不能让
   整个结果接口失败——那是医生正在看的页面）。
"""
from types import SimpleNamespace

import pytest

from app.api.v1.diagnosis import clinical_attribute_fields
from app.models.schemas import (
    ClinicalAction,
    DifferentialDiagnosis,
    MalignancyRisk,
    MalignancyRiskLevel,
    Measurement,
    PosteriorAcoustic,
)
from app.services.diagnosis_pipeline import (
    _clinical_attributes_to_dict,
    _fusion_result_to_dict,
)

pytestmark = pytest.mark.track_api


def _measurement_payload() -> dict:
    return {
        "long_mm": 12.0,
        "short_mm": 8.0,
        "aspect_ratio": 1.5,
        "unit": "mm",
        "pixel_spacing_source": "text",
    }


class TestPipelineExtraction:
    def test_extracts_measurement_risk_and_posterior(self):
        response = SimpleNamespace(
            measurement=Measurement(**_measurement_payload()),
            malignancy_risk=MalignancyRisk(
                probability=0.42, level=MalignancyRiskLevel.MEDIUM
            ),
            posterior_acoustic=PosteriorAcoustic.ATTENUATION,
            differential_diagnoses=[
                DifferentialDiagnosis(rank=1, disease_name="乳头状癌", confidence=0.5)
            ],
            clinical_actions=[
                ClinicalAction(action_type="fine_needle_aspiration")
            ],
            cross_modality_hints=[],
            patient_explanation=None,
            research_metrics=None,
        )

        attributes = _clinical_attributes_to_dict(response)

        assert attributes["measurement"]["long_mm"] == 12.0
        assert attributes["malignancy_risk"]["probability"] == 0.42
        assert attributes["posterior_acoustic"] == "attenuation"
        assert attributes["differential_diagnoses"][0]["disease_name"] == "乳头状癌"
        assert attributes["clinical_actions"][0]["action_type"] == "fine_needle_aspiration"

    def test_omits_empty_fields(self):
        response = SimpleNamespace(
            measurement=None,
            malignancy_risk=None,
            posterior_acoustic=None,
            differential_diagnoses=[],
            clinical_actions=[],
            cross_modality_hints=[],
            patient_explanation=None,
            research_metrics=None,
        )
        assert _clinical_attributes_to_dict(response) == {}

    def test_fusion_dict_uses_final_response(self):
        final = SimpleNamespace(
            confidence=0.77,
            measurement=Measurement(**_measurement_payload()),
            malignancy_risk=None,
            posterior_acoustic=None,
            differential_diagnoses=[],
            clinical_actions=[],
            cross_modality_hints=[],
            patient_explanation=None,
            research_metrics=None,
        )
        fusion = SimpleNamespace(
            needs_review=False,
            review_reason=None,
            self_rag_passed=True,
            crag_triggered=False,
            crag_recovered=False,
            total_latency_ms=123.0,
            final_response=final,
        )

        payload = _fusion_result_to_dict(fusion)

        assert payload["final_confidence"] == 0.77
        assert payload["clinical_attributes"]["measurement"]["short_mm"] == 8.0

    def test_fusion_dict_without_attributes_has_no_key(self):
        fusion = SimpleNamespace(
            needs_review=False,
            review_reason=None,
            self_rag_passed=None,
            crag_triggered=False,
            crag_recovered=False,
            total_latency_ms=0.0,
            final_response=SimpleNamespace(
                confidence=0.1,
                measurement=None,
                malignancy_risk=None,
                posterior_acoustic=None,
                differential_diagnoses=[],
                clinical_actions=[],
                cross_modality_hints=[],
                patient_explanation=None,
                research_metrics=None,
            ),
        )
        assert "clinical_attributes" not in _fusion_result_to_dict(fusion)

    def test_fusion_dict_none(self):
        assert _fusion_result_to_dict(None) == {}


class TestApiMapping:
    def test_maps_full_payload(self):
        fields = clinical_attribute_fields(
            {
                "clinical_attributes": {
                    "measurement": _measurement_payload(),
                    "malignancy_risk": {
                        "probability": 0.42,
                        "level": "medium",
                        "basis": [{"feature": "micro", "contribution": 0.3}],
                    },
                    "posterior_acoustic": "attenuation",
                    "differential_diagnoses": [
                        {"rank": 1, "disease_name": "乳头状癌", "confidence": 0.5}
                    ],
                }
            }
        )

        assert isinstance(fields["measurement"], Measurement)
        assert fields["malignancy_risk"].probability == 0.42
        assert fields["posterior_acoustic"] == "attenuation"
        assert fields["differential_diagnoses"][0].rank == 1
        assert fields["clinical_actions"] == []
        assert fields["research_metrics"] is None

    def test_no_payload_returns_empty_defaults(self):
        fields = clinical_attribute_fields(None)
        assert fields["measurement"] is None
        assert fields["malignancy_risk"] is None
        assert fields["differential_diagnoses"] == []

    def test_missing_clinical_attributes_key(self):
        assert clinical_attribute_fields({"findings": "x"})["measurement"] is None

    def test_non_dict_clinical_attributes(self):
        assert clinical_attribute_fields({"clinical_attributes": "oops"})["measurement"] is None

    def test_malformed_field_does_not_break_others(self):
        """单个字段结构异常不能拖垮整个映射（避免结果页 500）。"""
        fields = clinical_attribute_fields(
            {
                "clinical_attributes": {
                    "measurement": {"long_mm": "not-a-number"},
                    "malignancy_risk": {
                        "probability": 0.9,
                        "level": "high",
                    },
                }
            }
        )

        assert fields["measurement"] is None
        assert fields["malignancy_risk"].probability == 0.9

    def test_malformed_list_item_is_skipped(self):
        fields = clinical_attribute_fields(
            {
                "clinical_attributes": {
                    "differential_diagnoses": [
                        {"rank": 1, "disease_name": "ok", "confidence": 0.5},
                        {"rank": "bad"},
                    ]
                }
            }
        )
        assert len(fields["differential_diagnoses"]) == 1

    def test_non_list_collection_treated_as_empty(self):
        fields = clinical_attribute_fields(
            {"clinical_attributes": {"clinical_actions": {"not": "a list"}}}
        )
        assert fields["clinical_actions"] == []
