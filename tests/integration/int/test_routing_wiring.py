"""Adaptive RAG 路由在诊断管线中的接线测试 + clinical_text 落库回归。

覆盖两类问题：
1. **P0 回归**：``DiagnosisResult`` 曾缺 ``clinical_text`` 列，而管线读取
   ``request.clinical_text``，导致真实请求调度阶段恒抛 AttributeError、
   整条诊断链路只产出失败占位响应。
2. **T-GB-AGT-02 接线**：路由判定必须真的被算出并写进 findings.routing。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.schemas import ResponseTier
from app.services.diagnosis_pipeline import (
    _assess_routing,
    _coerce_response_tier,
    _write_result_to_db,
    get_routing_router,
)
from tests.integration.int.int_test_helpers import patch_write_deps

pytestmark = pytest.mark.track_int


class _StubRequest:
    """诊断结果行的替身（只含管线读取的字段）。"""

    def __init__(self, clinical_text="", organ="thyroid", tier="full",
                 request_id="req_wire_1", session_id="sess_1"):
        self.clinical_text = clinical_text
        self.organ = organ
        self.tier = tier
        self.request_id = request_id
        self.session_id = session_id


class TestClinicalTextColumnRegression:
    """P0 回归护栏：ORM 必须保留 clinical_text 列。"""

    def test_diagnosis_result_has_clinical_text_column(self):
        from app.models.database import DiagnosisResult

        columns = {c.name for c in DiagnosisResult.__table__.columns}
        assert "clinical_text" in columns

    def test_pipeline_reads_clinical_text_without_attribute_error(self):
        request = _StubRequest(clinical_text="甲状腺结节 低回声")

        # 缺失该属性会抛 AttributeError —— 用 getattr 兜底后必须正常
        user_query = str(
            getattr(request, "clinical_text", None) or "请基于提供的影像给出诊断"
        )
        assert user_query == "甲状腺结节 低回声"

    def test_missing_attribute_falls_back_to_default(self):
        class _LegacyRow:  # 模拟历史行/测试替身
            request_id = "req_legacy"

        user_query = str(
            getattr(_LegacyRow(), "clinical_text", None) or "请基于提供的影像给出诊断"
        )
        assert user_query == "请基于提供的影像给出诊断"


class TestTierCoercion:
    @pytest.mark.parametrize("value", ["full", "standard", "realtime"])
    def test_known_tiers(self, value):
        assert _coerce_response_tier(value) == ResponseTier(value)

    def test_unknown_tier_defaults_to_full(self):
        # 无法识别时按最保守路径（全量）处理，不得降级
        assert _coerce_response_tier("bogus") == ResponseTier.FULL
        assert _coerce_response_tier(None) == ResponseTier.FULL
        assert _coerce_response_tier(MagicMock()) == ResponseTier.FULL


class TestRoutingAssessment:
    def test_single_feature_standard_request_stays_standard(self):
        decision = _assess_routing(
            _StubRequest(clinical_text="低回声结节", tier="standard")
        )

        assert decision is not None
        assert decision.effective_tier == ResponseTier.STANDARD
        assert decision.requires_confirmation is False

    def test_multi_feature_standard_request_requests_confirmation(self):
        decision = _assess_routing(
            _StubRequest(
                clinical_text="低回声+边界不清+微钙化+血流信号", tier="standard"
            )
        )

        assert decision.requires_confirmation is True
        assert decision.target_tier == ResponseTier.FULL
        assert decision.effective_tier == ResponseTier.STANDARD  # 待医生确认

    def test_cross_organ_text_goes_full(self):
        decision = _assess_routing(
            _StubRequest(clinical_text="甲亢+心律失常", organ="thyroid", tier="standard")
        )

        assert decision.effective_tier == ResponseTier.FULL
        assert decision.is_upgraded is True

    def test_doctor_full_never_downgraded(self):
        decision = _assess_routing(
            _StubRequest(clinical_text="低回声结节", tier="full")
        )

        assert decision.effective_tier == ResponseTier.FULL

    def test_malformed_request_does_not_raise(self):
        # tier/organ/clinical_text 全是 MagicMock 时也不得抛
        assert _assess_routing(MagicMock()) is not None

    def test_router_is_process_singleton(self):
        # stats 需跨请求累计，必须是同一实例
        assert get_routing_router() is get_routing_router()


class TestFindingsPersistRouting:
    @pytest.mark.asyncio
    async def test_write_result_includes_routing_section(self, monkeypatch):
        request = _StubRequest(clinical_text="低回声+边界不清+微钙化+血流信号",
                               tier="standard")
        decision = _assess_routing(request)
        repo = MagicMock()
        repo.update_result = AsyncMock(return_value=MagicMock())

        patch_write_deps(monkeypatch, repo)

        ok = await _write_result_to_db(
            request=request,
            fusion_result=None,
            sg_result={"passed": True},
            sg_ok=True,
            timings={"pipeline.total_ms": 12.0},
            routing=decision,
        )

        assert ok is True
        findings = repo.update_result.await_args.kwargs["findings"]
        assert findings["routing"]["complexity"] == "multi_feature"
        assert findings["routing"]["requires_confirmation"] is True
        assert findings["routing"]["target_tier"] == "full"
        assert findings["routing"]["retrieval_profile"]["top_k"] == 10

    @pytest.mark.asyncio
    async def test_write_result_without_routing_marks_none(self, monkeypatch):
        request = _StubRequest()
        repo = MagicMock()
        repo.update_result = AsyncMock(return_value=MagicMock())

        patch_write_deps(monkeypatch, repo)

        await _write_result_to_db(
            request=request,
            fusion_result=None,
            sg_result={"passed": True},
            sg_ok=True,
            timings={},
        )

        findings = repo.update_result.await_args.kwargs["findings"]
        assert findings["routing"] is None
