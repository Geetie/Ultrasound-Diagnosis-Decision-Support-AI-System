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
    _lift_clinical_attributes,
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

    def test_self_rag_score_passthrough(self):
        """A.105：只有 bool 时无法回答"为什么不过"，量化分必须落到 findings。"""
        fr = self._fr()  # _fr 的 **kw 进的是 final_response，量化分在外层
        fr.self_rag_score = 4.2
        d = _fusion_result_to_dict(fr)
        assert d["self_rag_score"] == 4.2

    def test_self_rag_score_none_means_not_run(self):
        """A.105：未执行时不得写成 0.0 分（0.0=质检过且全不达标，None=没质检）。"""
        d = _fusion_result_to_dict(self._fr())
        assert d["self_rag_score"] is None


class TestFailedHelpers:
    def test_make_failed_response(self):
        r = _make_failed_response("boom")
        assert r.confidence == 0.0
        # A.59：正文给可读中文（原始原因只进 trace —— 它可能含内部标识/英文异常串），
        # 并**必须转人工**（零静默：失败不许静默照出）
        assert "判读流程未能启动" in r.content
        assert r.needs_review is True
        assert "pipeline_dispatch_failed: boom" in r.reasoning_trace

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


class TestLiftClinicalAttributes:
    """A.107 / G1：contract 字段读 findings 顶层，写侧却在 fusion_result 里。

    这组守卫钉的是**真实形状**（写侧产出的 payload 原样塞进 findings，再提升），
    不是手写的"顶层夹具"——G1 之所以长期假绿，正是因为既有单测直接手写顶层键，
    绕过了写侧结构。
    """

    def _findings_with_attrs(self):
        final = SimpleNamespace(
            confidence=0.8,
            content="TI-RADS 4a",
            measurement=SimpleNamespace(model_dump=lambda: {"long_mm": 12.0}),
            malignancy_risk=SimpleNamespace(model_dump=lambda: {"risk": 0.31}),
            posterior_acoustic=None,
            differential_diagnoses=[SimpleNamespace(model_dump=lambda: {"dx": "腺瘤"})],
            clinical_actions=[],
            cross_modality_hints=[],
            patient_explanation=None,
            research_metrics=None,
        )
        fr = SimpleNamespace(
            needs_review=False,
            review_reason=None,
            self_rag_passed=True,
            crag_triggered=False,
            crag_recovered=False,
            total_latency_ms=10.0,
            self_rag_score=None,
            final_response=final,
        )
        return {"organ": "thyroid", "fusion_result": _fusion_result_to_dict(fr)}

    def test_real_shape_lifts_to_top_level(self):
        findings = self._findings_with_attrs()
        nested = findings["fusion_result"]["clinical_attributes"]
        assert nested  # 写侧确实产出了
        _lift_clinical_attributes(findings)
        # 顶层与嵌套同值，且嵌套仍在（既有读嵌套的代码不受影响）
        assert findings["clinical_attributes"] == nested
        assert findings["fusion_result"]["clinical_attributes"] == nested

    def test_contract_reader_now_sees_fields(self):
        """契约读侧（api/v1/diagnosis.py:140 的写法）必须能取到 measurement。"""
        findings = self._findings_with_attrs()
        _lift_clinical_attributes(findings)
        clinical = findings.get("clinical_attributes") or {}
        assert clinical["measurement"] == {"long_mm": 12.0}
        assert clinical["differential_diagnoses"] == [{"dx": "腺瘤"}]

    def test_noop_when_nested_missing(self):
        findings = {"fusion_result": {"content": "x"}}
        _lift_clinical_attributes(findings)
        assert "clinical_attributes" not in findings

    def test_noop_when_nested_empty(self):
        findings = {"fusion_result": {"clinical_attributes": {}}}
        _lift_clinical_attributes(findings)
        assert "clinical_attributes" not in findings

    def test_noop_when_not_dict(self):
        findings = {"fusion_result": None}
        assert _lift_clinical_attributes(findings) is findings

    def test_write_path_actually_calls_it(self):
        """防回退：findings 的**组装处**必须真的调用提升函数。

        组装 findings 的是 `_write_result_to_db()`（不是 `run_diagnosis_pipeline`），
        所以钉这个函数——只在别处定义 helper 而不调用，本组纯函数测试照样全绿。
        """
        import inspect

        from app.services import diagnosis_pipeline

        src = inspect.getsource(diagnosis_pipeline._write_result_to_db)
        assert "_lift_clinical_attributes(findings)" in src
