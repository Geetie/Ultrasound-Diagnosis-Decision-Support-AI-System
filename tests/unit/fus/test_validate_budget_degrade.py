"""A.51 fusion-validate 降载：validate 级预算超时 → 优雅降级（不失败、不挂死）。

背景（p14b 真机基线）：validate P50 1.85s / P99 2.30s，历史最坏 ~14s。
降载设计：
- validate 外包 `asyncio.wait_for(budget)`，超时返回
  `needs_review=True + review_reason="fusion_validate_budget_exceeded" +
  validate_degraded=True`（结果照常返回，只是转人工复核）；
- 预算 <=0 视为不设（保持旧行为）；
- 常态（未超时）不得被误伤。
另覆盖 `_safe_fuse` 的档位门控透传（非 FULL 档 use_self_rag=False）。
"""
import asyncio
from unittest.mock import MagicMock

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionRequest, FusionResult
from app.fusion.service import FusionEngineImpl


class _SlowValidator:
    """可控时延的校验器替身（模拟 Self-RAG LLM 慢调用）。"""

    def __init__(self, delay_s: float, result: FusionResult | None = None):
        self.delay_s = delay_s
        self.result = result if result is not None else FusionResult()
        self.calls = 0

    async def validate(self, request: FusionRequest) -> FusionResult:
        self.calls += 1
        await asyncio.sleep(self.delay_s)
        return self.result


def _engine(validate_budget_s: float) -> FusionEngineImpl:
    return FusionEngineImpl(
        conflict_detector=MagicMock(),
        consistency_checker=MagicMock(),
        validate_budget_s=validate_budget_s,
    )


def _request() -> FusionRequest:
    return FusionRequest(
        request_id="test-validate-budget",
        agent_responses=[
            AgentResponse(agent_name="a", content="c", confidence=0.8)
        ],
    )


class TestValidateBudget:
    async def test_timeout_degrades_instead_of_failing(self):
        """预算超时 → needs_review + 专属原因 + validate_degraded，且结果仍可用。"""
        engine = _engine(validate_budget_s=0.05)
        engine._layered_validator = _SlowValidator(delay_s=1.0)

        result = await engine.fuse(_request())

        assert result.validate_degraded is True
        assert result.needs_review is True
        assert result.review_reason == "fusion_validate_budget_exceeded"
        # 关键：不是抛异常、也不是空结果——final_response 仍被选出
        assert result.final_response is not None
        assert result.validate_ms > 0.0

    async def test_within_budget_not_affected(self):
        """未超预算：正常校验路径，不得被误标降级。"""
        engine = _engine(validate_budget_s=5.0)
        validator = _SlowValidator(delay_s=0.0)
        engine._layered_validator = validator

        result = await engine.fuse(_request())

        assert validator.calls == 1
        assert result.validate_degraded is False
        assert result.review_reason != "fusion_validate_budget_exceeded"

    async def test_zero_budget_keeps_legacy_behavior(self):
        """预算 <=0 → 不启用 wait_for（旧行为），慢校验也能跑完。"""
        engine = _engine(validate_budget_s=0.0)
        validator = _SlowValidator(delay_s=0.2)
        engine._layered_validator = validator

        result = await engine.fuse(_request())

        assert validator.calls == 1
        assert result.validate_degraded is False


class TestSelfRagTierGating:
    async def test_safe_fuse_passes_use_self_rag_flag(self, monkeypatch):
        """`_safe_fuse(use_self_rag=False)` 必须落到 FusionRequest.use_self_rag。"""
        import app.main as main_module
        from app.services.diagnosis_pipeline import _safe_fuse

        captured: dict = {}

        class _Engine:
            async def fuse(self, req):
                captured["use_self_rag"] = req.use_self_rag
                return FusionResult()

        monkeypatch.setattr(main_module.app.state, "fusion_engine", _Engine(), raising=False)

        request = MagicMock()
        request.organ = "thyroid"
        await _safe_fuse(request, [], "rid", skip_self_rag=False, use_self_rag=False)
        assert captured["use_self_rag"] is False

        await _safe_fuse(request, [], "rid", skip_self_rag=False, use_self_rag=True)
        assert captured["use_self_rag"] is True

        # 未传参回退旧语义：skip_self_rag=True → 跳过
        await _safe_fuse(request, [], "rid", skip_self_rag=True)
        assert captured["use_self_rag"] is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
