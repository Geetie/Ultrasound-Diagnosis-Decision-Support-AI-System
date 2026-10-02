"""diagnosis_pipeline 纯函数与轻依赖函数的分支覆盖（⑦ ⑧ ④ 高性价比块）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_inf]

from app.services.diagnosis_pipeline import (
    _clinical_attributes_to_dict,
    _extract_findings,
    _extract_recommendation,
    _fusion_result_to_dict,
    _make_failed_fusion_result,
    _make_failed_response,
    _resolve_patient_id,
)


class TestClinicalAttributes:
    def test_none_response(self):
        assert _clinical_attributes_to_dict(None) == {}

    def test_full_response_with_pydantic_like_fields(self):
        resp = SimpleNamespace(
            measurement=SimpleNamespace(model_dump=lambda: {"len": 12}),
            malignancy_risk=SimpleNamespace(model_dump=lambda: {"risk": 0.3}),
            posterior_acoustic=SimpleNamespace(value="enhanced"),
            differential_diagnoses=[
                SimpleNamespace(model_dump=lambda: {"dx": "腺瘤"})
            ],
            clinical_actions=["随访"],
            cross_modality_hints=[],
            patient_explanation=SimpleNamespace(model_dump=lambda: {"text": "解释"}),
            research_metrics={"auc": 0.9},
        )
        d = _clinical_attributes_to_dict(resp)
        assert d["measurement"] == {"len": 12}
        assert d["malignancy_risk"] == {"risk": 0.3}
        assert d["posterior_acoustic"] == "enhanced"
        assert d["differential_diagnoses"] == [{"dx": "腺瘤"}]
        assert d["clinical_actions"] == ["随访"]
        # cross_modality_hints 为空列表 → 不写
        assert "cross_modality_hints" not in d
        assert d["patient_explanation"] == {"text": "解释"}
        assert d["research_metrics"] == {"auc": 0.9}

    def test_plaintext_fields_without_model_dump(self):
        resp = SimpleNamespace(
            measurement={"raw": True},
            malignancy_risk=0.5,
            posterior_acoustic="posterior",
        )
        d = _clinical_attributes_to_dict(resp)
        assert d["measurement"] == {"raw": True}
        assert d["malignancy_risk"] == 0.5
        assert d["posterior_acoustic"] == "posterior"

    def test_empty_response(self):
        assert _clinical_attributes_to_dict(SimpleNamespace()) == {}


class TestFusionResultToDict:
    def _fr(self, **kw):
        final = SimpleNamespace(
            confidence=0.77,
            content="TI-RADS 4a，建议穿刺",
            measurement=SimpleNamespace(model_dump=lambda: {"len": 12}),
            **kw,
        )
        return SimpleNamespace(
            needs_review=True,
            review_reason="sg_degraded",
            self_rag_passed=False,
            crag_triggered=True,
            crag_recovered=False,
            total_latency_ms=123.4,
            final_response=final,
        )

    def test_none(self):
        assert _fusion_result_to_dict(None) == {}

    def test_full_payload(self):
        d = _fusion_result_to_dict(self._fr())
        assert d["needs_review"] is True
        assert d["review_reason"] == "sg_degraded"
        assert d["self_rag_passed"] is False
        assert d["crag_triggered"] is True
        assert d["final_confidence"] == 0.77
        assert "TI-RADS 4a" in d["content"]
        assert "clinical_attributes" in d

    def test_no_final_response(self):
        fr = SimpleNamespace(
            needs_review=False,
            review_reason=None,
            self_rag_passed=True,
            crag_triggered=False,
            crag_recovered=False,
            total_latency_ms=1.0,
            final_response=None,
        )
        d = _fusion_result_to_dict(fr)
        assert d["final_confidence"] == 0.0
        assert "content" not in d


class TestFailedHelpers:
    def test_make_failed_response(self):
        r = _make_failed_response("boom")
        assert r.confidence == 0.0
        assert "boom" in r.content

    def test_make_failed_fusion_result_with_candidates(self):
        from app.agents.protocols import AgentResponse

        cands = [
            AgentResponse(agent_name="a", content="a", confidence=0.2),
            AgentResponse(agent_name="b", content="b", confidence=0.9),
        ]
        fr = _make_failed_fusion_result(cands)
        assert fr.needs_review is True
        assert fr.review_reason == "fusion_layer_failed"
        assert fr.final_response.confidence == 0.9

    def test_make_failed_fusion_result_empty(self):
        fr = _make_failed_fusion_result([])
        assert fr.final_response.agent_name == "pipeline_fallback"


class TestExtractHelpers:
    def test_extract_findings_dict_and_object(self):
        assert (
            _extract_findings({"final_response": {"content": "报告文本"}})
            == "报告文本"
        )
        assert _extract_findings({"final_response": "纯文本"}) == "纯文本"
        assert _extract_findings({"final_response": None}) == ""
        assert _extract_findings(SimpleNamespace(final_response=None)) == ""

    def test_extract_recommendation_truncates(self):
        fr = SimpleNamespace(
            final_response=SimpleNamespace(content="x" * 600)
        )
        rec = _extract_recommendation(fr)
        assert rec is not None and len(rec) == 500
        assert _extract_recommendation(SimpleNamespace(final_response=SimpleNamespace(content=""))) is None


class TestResolvePatientId:
    async def test_returns_patient_id(self):
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: "P001")
        )
        assert await _resolve_patient_id(db, "sess-1") == "P001"

    async def test_returns_none_when_missing(self):
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        )
        assert await _resolve_patient_id(db, "sess-2") is None

    async def test_db_error_returns_none(self):
        db = MagicMock()
        db.execute = AsyncMock(side_effect=RuntimeError("db down"))
        assert await _resolve_patient_id(db, "sess-3") is None
