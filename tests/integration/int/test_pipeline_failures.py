"""T-GA-INT-01: 诊断管线 6 种失败降级路径测试。

测试场景（来自 :file:`.trae/documents/plan-T-GA-INT-01-e2e-integration.md` 失败降级矩阵）：

| 环节 | 失败现象 | 降级策略 |
|------|---------|---------|
| 调度 | dispatcher.invoke 抛错 | confidence=0.0 占位 |
| 知识查询 | RAG 返回空 | BM25 fallback（由 RAG 内部处理）|
| 专科推理 | LLM 抛错 | specialist 内部重试，仍失败 needs_review |
| 融合层 | fuse 抛错 | 使用 specialist 最高 confidence 响应 |
| SafetyGateway | sg.check_output 失败 | service_unavailable=True, status="failed" |
| 同意撤回 | SG 返回 consent_status=withdrawn | 强阻断 |
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.protocols import AgentResponse


class TestPipelineFailurePaths:
    """测试管线在每个环节失败时的降级行为。"""

    @pytest.mark.asyncio
    async def test_dispatcher_failure_returns_zero_confidence(self):
        """调度失败时，specialist_responses 包含 confidence=0.0 占位。"""
        from app.services.diagnosis_pipeline import _safe_dispatch

        # Mock request with all required attrs
        mock_request = MagicMock()
        mock_request.organ = "thyroid"
        mock_request.session_id = "sess_test"
        mock_request.clinical_text = "test"

        # Mock app.state.dispatcher to raise
        with patch("app.main.app") as mock_app:
            mock_app.state.dispatcher.invoke = AsyncMock(
                side_effect=RuntimeError("dispatcher crashed")
            )
            responses = await _safe_dispatch(mock_request, "req_test_d1")

        assert len(responses) == 1
        assert responses[0].confidence == 0.0
        assert "dispatcher_failed" in responses[0].content

    @pytest.mark.asyncio
    async def test_dispatcher_unavailable_returns_placeholder(self):
        """dispatcher 为 None 时返回占位响应。"""
        from app.services.diagnosis_pipeline import _safe_dispatch

        mock_request = MagicMock()
        mock_request.organ = "thyroid"
        mock_request.session_id = "sess_test"
        mock_request.clinical_text = "test"

        with patch("app.main.app") as mock_app:
            mock_app.state.dispatcher = None
            responses = await _safe_dispatch(mock_request, "req_test_d2")

        assert len(responses) == 1
        assert responses[0].confidence == 0.0
        assert "dispatcher_unavailable" in responses[0].content

    @pytest.mark.asyncio
    async def test_fusion_failure_uses_max_confidence_specialist(self):
        """融合层失败时，使用 specialist 最高 confidence 响应。"""
        from app.services.diagnosis_pipeline import _make_failed_fusion_result

        # 构造 3 个 specialist 响应，confidence 不同
        responses = [
            AgentResponse(agent_name="thyroid", content="thyroid result", confidence=0.6),
            AgentResponse(agent_name="liver", content="liver result", confidence=0.85),
            AgentResponse(agent_name="cardiac", content="cardiac result", confidence=0.3),
        ]
        result = _make_failed_fusion_result(responses)

        assert result.final_response.confidence == 0.85
        assert result.final_response.agent_name == "liver"
        assert result.needs_review is True
        assert result.review_reason == "fusion_layer_failed"

    @pytest.mark.asyncio
    async def test_fusion_failure_with_empty_responses(self):
        """融合层失败且无 specialist 响应时，返回占位。"""
        from app.services.diagnosis_pipeline import _make_failed_fusion_result

        result = _make_failed_fusion_result([])
        assert result.final_response.confidence == 0.0
        assert result.needs_review is True

    @pytest.mark.asyncio
    async def test_safety_gateway_failure_returns_none(self):
        """sg.check_output 抛异常时返回 (None, False)。"""
        from app.services.diagnosis_pipeline import _safe_safety_check

        with patch("app.services.diagnosis_pipeline.safety_gateway_client") as mock_sg:
            mock_sg.get_consent_status = AsyncMock(return_value="granted")
            mock_sg.check_output = AsyncMock(side_effect=RuntimeError("SG down"))
            sg_result, sg_ok = await _safe_safety_check(
                request=MagicMock(),
                fusion_result=MagicMock(),
                request_id="req_test_sg1",
                # 2026-09-29：`patient_id` 解析不出时管线**先** fail-closed
                # （不拿 "unknown" 去查同意记录），不提供它就会在到达 SG 之前返回，
                # 使这条用例"因错误的原因通过"（测的是患者守卫，不是 SG 异常）。
                patient_id="P001",
            )

        assert sg_result is None
        assert sg_ok is False

    @pytest.mark.asyncio
    async def test_safety_gateway_success_returns_result(self):
        """sg.check_output 正常时返回 (result, True)。"""
        from app.services.diagnosis_pipeline import _safe_safety_check

        with patch("app.services.diagnosis_pipeline.safety_gateway_client") as mock_sg:
            mock_sg.get_consent_status = AsyncMock(return_value="granted")
            mock_sg.check_output = AsyncMock(
                return_value={"passed": True, "consent_status_at_output": "granted"}
            )
            sg_result, sg_ok = await _safe_safety_check(
                request=MagicMock(),
                fusion_result=MagicMock(),
                request_id="req_test_sg2",
                patient_id="P001",
            )

        assert sg_result is not None
        assert sg_ok is True
        assert sg_result["passed"] is True


class TestWriteResultFallback:
    """测试 _write_result_to_db 在各场景下的写库行为。"""

    @pytest.mark.asyncio
    async def test_write_result_with_sg_unavailable(self):
        """SG 不可用时 status='failed'。"""
        from app.services.diagnosis_pipeline import _write_result_to_db

        mock_repo = MagicMock()
        mock_repo.update_result = AsyncMock(return_value=MagicMock())

        mock_request = MagicMock()
        mock_request.request_id = "req_test_w1"
        mock_request.organ = "thyroid"

        result = await _write_result_to_db(
            diagnosis_repo=mock_repo,
            request=mock_request,
            fusion_result=None,
            sg_result=None,
            sg_ok=False,
            timings={},
        )

        assert result is True
        call_kwargs = mock_repo.update_result.call_args.kwargs
        assert call_kwargs["status"] == "failed"
        assert call_kwargs["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_write_result_with_needs_review(self):
        """fusion_result.needs_review=True 时 status='needs_review'。"""
        from app.fusion.protocols import FusionResult
        from app.services.diagnosis_pipeline import _write_result_to_db

        mock_repo = MagicMock()
        mock_repo.update_result = AsyncMock(return_value=MagicMock())

        mock_request = MagicMock()
        mock_request.request_id = "req_test_w2"
        mock_request.organ = "thyroid"

        mock_fusion = FusionResult(
            needs_review=True,
            review_reason="self_rag_failed",
        )
        mock_fusion.final_response = AgentResponse(
            agent_name="thyroid", content="test", confidence=0.5
        )

        result = await _write_result_to_db(
            diagnosis_repo=mock_repo,
            request=mock_request,
            fusion_result=mock_fusion,
            sg_result={"passed": True, "consent_status_at_output": "granted"},
            sg_ok=True,
            timings={},
        )

        assert result is True
        call_kwargs = mock_repo.update_result.call_args.kwargs
        assert call_kwargs["status"] == "needs_review"
        assert call_kwargs["confidence"] == 0.5


__all__ = [
    "TestPipelineFailurePaths",
    "TestWriteResultFallback",
]
