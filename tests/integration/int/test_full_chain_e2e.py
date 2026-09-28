"""T-GB-INT-01 全链路集成：前端 → API → 调度 → 专科 → 融合 → SG → 前端。

与 ``test_e2e_diagnosis.py`` 的差别：那里把 ``_safe_dispatch`` /
``_safe_fuse`` / ``_safe_safety_check`` / ``_write_result_to_db`` 全部 patch
掉，只验证"管线调用了这些步骤"；本文件**只 mock 外部边界**（专科智能体、
融合引擎、SafetyGateway 客户端、DB 会话、WS 管理器），因此真正穿过管线的
容错与组装逻辑，可验证：

- Adaptive RAG 档位（T-GB-AGT-02）经 ``AgentContext`` 送达专科智能体；
- 融合请求确实带 ``use_crag=True``（CRAG 补充检索在链路中启用）；
- SafetyGateway 收到的是融合结论而非空载荷；
- 落库 findings 含 organ / fusion_result / routing / timings_ms；
- 诊断完成后向前端推送 ``diagnosis_result``。
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.protocols import AgentResponse
from app.agents.routing import (
    QueryComplexity,
    RetrievalProfile,
    RoutingDecision,
    RoutingReason,
)
from app.fusion.protocols import FusionResult
from app.models.schemas import ResponseTier


class _AsyncCtx:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info):
        return False


@pytest.mark.asyncio
async def test_full_chain_and_adaptive_profile_reaches_specialist():
    from app.services import diagnosis_pipeline as pipeline

    # ---- 外部边界替身 ----
    captured: dict[str, Any] = {}

    async def _specialist_invoke(context):
        captured["agent_context"] = context
        return AgentResponse(
            agent_name="thyroid_specialist",
            organ=context.organ,
            content="TI-RADS 4a，建议细针穿刺",
            confidence=0.91,
            evidence_chunk_ids=["kb_1", "kb_2"],
        )

    dispatcher = MagicMock()
    dispatcher.invoke = AsyncMock(side_effect=_specialist_invoke)

    async def _fuse(fusion_req):
        captured["fusion_request"] = fusion_req
        return FusionResult(
            final_response=AgentResponse(
                agent_name="fusion",
                content="TI-RADS 4a，建议细针穿刺",
                confidence=0.9,
                evidence_chunk_ids=["kb_1", "kb_2"],
            ),
            needs_review=False,
            self_rag_passed=True,
            crag_triggered=True,
        )

    fusion_engine = MagicMock()
    fusion_engine.fuse = AsyncMock(side_effect=_fuse)

    sg_client = MagicMock()
    # 2026-09-28：管线改为真实查询同意状态（拿不到即 fail-closed），替身必须提供该入口
    sg_client.get_consent_status = AsyncMock(return_value="granted")
    sg_client.check_output = AsyncMock(
        return_value={
            "passed": True,
            "allowed": True,
            "consent_status_at_output": "granted",
        }
    )

    request_row = MagicMock()
    request_row.request_id = "req_chain_1"
    request_row.session_id = "sess_chain_1"
    request_row.patient_id = "P001"
    request_row.organ = "thyroid"
    request_row.tier = ResponseTier.STANDARD.value
    request_row.clinical_text = "低回声结节，边界不清，微钙化"

    diagnosis_repo = MagicMock()
    diagnosis_repo.get_result = AsyncMock(return_value=request_row)
    diagnosis_repo.update_result = AsyncMock(return_value=request_row)
    diagnosis_repo.db = MagicMock()

    # 固定路由判定：只替换"判定"这一步（判定本身由单元测试覆盖），
    # 保留 _assess_routing → _safe_dispatch 的接线在测试范围内。
    decision = RoutingDecision(
        requested_tier=ResponseTier.STANDARD,
        effective_tier=ResponseTier.STANDARD,
        target_tier=ResponseTier.FULL,
        complexity=QueryComplexity.MULTI_FEATURE,
        requires_confirmation=True,
        reasons=(RoutingReason.MULTI_FEATURE_NEEDS_CONFIRMATION,),
        retrieval_profile=RetrievalProfile.standard(top_k=10),
        feature_categories=("echogenicity", "margin", "calcification"),
        detected_organs=("thyroid",),
    )

    nav_manager = MagicMock()
    nav_manager.send_diagnosis_result = AsyncMock()

    with patch.object(
        pipeline, "AsyncSessionLocal", MagicMock(return_value=_AsyncCtx(MagicMock()))
    ), patch.object(
        pipeline, "DiagnosisRepository", MagicMock(return_value=diagnosis_repo)
    ), patch.object(
        pipeline, "get_routing_router"
    ) as router_factory, patch.object(
        pipeline, "safety_gateway_client", sg_client
    ), patch.object(
        pipeline, "RedisPool"
    ) as redis_pool, patch(
        "app.main.app"
    ) as mock_app, patch(
        "app.api.websocket.nav.manager", nav_manager
    ):
        router_factory.return_value.assess = MagicMock(return_value=decision)
        redis_pool.setex = AsyncMock()
        mock_app.state.dispatcher = dispatcher
        mock_app.state.fusion_engine = fusion_engine

        ok = await pipeline.run_diagnosis_pipeline("req_chain_1")

    assert ok is True

    # 1) 档位经 AgentContext 送达专科智能体（T-GB-AGT-02 的注入链路）
    agent_context = captured["agent_context"]
    assert agent_context.retrieval_profile == decision.retrieval_profile.as_retrieval_kwargs()
    assert agent_context.organ.value == "thyroid"
    assert agent_context.user_query == request_row.clinical_text

    # 2) 融合请求带 CRAG，且拿到专科响应
    fusion_request = captured["fusion_request"]
    assert fusion_request.use_crag is True
    assert len(fusion_request.agent_responses) == 1
    assert fusion_request.agent_responses[0].confidence == 0.91

    # 3) SafetyGateway 收到的是融合结论
    sg_kwargs = sg_client.check_output.await_args.kwargs
    assert sg_kwargs["session_id"] == "sess_chain_1"
    assert "TI-RADS 4a" in sg_kwargs["output_data"]["findings"]

    # 4) 落库 findings 结构完整（前端与观测依赖）
    write_kwargs = diagnosis_repo.update_result.await_args.kwargs
    findings = write_kwargs["findings"]
    assert write_kwargs["status"] == "completed"
    assert findings["organ"] == "thyroid"
    assert findings["fusion_result"]["crag_triggered"] is True
    assert findings["routing"]["requires_confirmation"] is True
    assert findings["timings_ms"]["pipeline.total_ms"] > 0
    assert write_kwargs["knowledge_ids"] == ["kb_1", "kb_2"]

    # 5) 推送前端
    nav_manager.send_diagnosis_result.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_chain_sg_unavailable_marks_failed_but_completes():
    """SG 不可达 → status=failed + service_unavailable，但管线自身不抛异常。"""
    from app.services import diagnosis_pipeline as pipeline

    dispatcher = MagicMock()
    dispatcher.invoke = AsyncMock(
        return_value=AgentResponse(agent_name="s", organ=None, content="ok", confidence=0.5)
    )
    fusion_engine = MagicMock()
    fusion_engine.fuse = AsyncMock(return_value=FusionResult(needs_review=False))

    sg_client = MagicMock()
    # 2026-09-28：管线改为真实查询同意状态（拿不到即 fail-closed），替身必须提供该入口
    sg_client.get_consent_status = AsyncMock(return_value="granted")
    sg_client.check_output = AsyncMock(side_effect=RuntimeError("sg down"))

    request_row = MagicMock()
    request_row.request_id = "req_chain_2"
    request_row.session_id = "sess_chain_2"
    request_row.organ = "liver"
    request_row.tier = ResponseTier.FULL.value
    request_row.clinical_text = "肝内低回声灶"
    request_row.patient_id = "P002"

    diagnosis_repo = MagicMock()
    diagnosis_repo.get_result = AsyncMock(return_value=request_row)
    diagnosis_repo.update_result = AsyncMock(return_value=request_row)
    diagnosis_repo.db = MagicMock()

    with patch.object(
        pipeline, "AsyncSessionLocal", MagicMock(return_value=_AsyncCtx(MagicMock()))
    ), patch.object(
        pipeline, "DiagnosisRepository", MagicMock(return_value=diagnosis_repo)
    ), patch.object(
        pipeline, "get_routing_router"
    ) as router_factory, patch.object(
        pipeline, "safety_gateway_client", sg_client
    ), patch.object(
        pipeline, "RedisPool"
    ) as redis_pool, patch(
        "app.main.app"
    ) as mock_app, patch(
        "app.api.websocket.nav.manager", MagicMock(send_diagnosis_result=AsyncMock())
    ):
        router_factory.return_value.assess = MagicMock(side_effect=RuntimeError("no routing"))
        redis_pool.setex = AsyncMock()
        mock_app.state.dispatcher = dispatcher
        mock_app.state.fusion_engine = fusion_engine

        ok = await pipeline.run_diagnosis_pipeline("req_chain_2")

    assert ok is True  # 管线成功返回（失败已被降级矩阵吸收）
    write_kwargs = diagnosis_repo.update_result.await_args.kwargs
    assert write_kwargs["status"] == "failed"
    assert write_kwargs["confidence"] == 0.0
    assert write_kwargs["findings"]["sg_result"] == {"service_unavailable": True}


@pytest.mark.asyncio
async def test_full_chain_request_missing_returns_false():
    from app.services import diagnosis_pipeline as pipeline

    diagnosis_repo = MagicMock()
    diagnosis_repo.get_result = AsyncMock(return_value=None)

    with patch.object(
        pipeline, "AsyncSessionLocal", MagicMock(return_value=_AsyncCtx(MagicMock()))
    ), patch.object(
        pipeline, "DiagnosisRepository", MagicMock(return_value=diagnosis_repo)
    ):
        ok = await pipeline.run_diagnosis_pipeline("req_missing")

    assert ok is False
