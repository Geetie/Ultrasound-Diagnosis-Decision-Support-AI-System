"""02-5 链B：CUSUM 熔断读者（用户拍板语义 (a) = 降级 needs_review，不停服）。

判据（02-5⑤ B-V2 / ⑥）：
1. 4 次 severe 触发 pending、**3 次不触发**（门槛 h=3.0 未被改小，BR2）；
2. PENDING_CONFIRMATION / SUSPENDED 时 _write_result_to_db 落
   status="needs_review"，review_reason 以 ai_safety_circuit_breaker_ 开头；
3. 结论正文（confidence / final_response）不受影响 —— 降级不阻断（B-V3 判据③）；
4. findings["safety_gate"] 记录真实状态（B-V3 判据①）；
5. ACTIVE 时行为与基线完全一致（0 漂移）；
6. review_reason 不覆盖融合层已写的更具体原因。
"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.observability.cusum import AISafetyCircuitBreaker
from app.observability.cusum.circuit_breaker import CircuitBreakerState
from app.observability.cusum.monitor import CUSUMConfig, CUSUMMonitor
from tests.integration.int.int_test_helpers import patch_write_deps


def _pending_breaker() -> SimpleNamespace:
    """suspension_info 返回 PENDING_CONFIRMATION 的 breaker 替身。"""
    return SimpleNamespace(
        suspension_info={
            "state": CircuitBreakerState.PENDING_CONFIRMATION,
            "pending_reason": "cusum_s_plus_exceeded:3.97",
            "pending_since": time.time(),
        }
    )


def _suspended_breaker() -> SimpleNamespace:
    return SimpleNamespace(
        suspension_info={
            "state": CircuitBreakerState.SUSPENDED,
            "reason": "cusum_s_plus_exceeded:3.97",
            "suspended_at": time.time(),
        }
    )


def _active_breaker() -> SimpleNamespace:
    return SimpleNamespace(suspension_info={"state": CircuitBreakerState.ACTIVE})


def _make_repo() -> MagicMock:
    repo = MagicMock()
    repo.update_result = AsyncMock(return_value=MagicMock())
    return repo


def _fusion_ok():
    from app.agents.protocols import AgentResponse
    from app.fusion.protocols import FusionResult

    fusion = FusionResult(needs_review=False, review_reason=None)
    fusion.final_response = AgentResponse(
        agent_name="thyroid", content="TI-RADS 3 类结节", confidence=0.82
    )
    return fusion


async def _write(monkeypatch, breaker_stub, fusion=None, sg_ok=True):
    from app.services.diagnosis_pipeline import _write_result_to_db

    monkeypatch.setattr(
        AISafetyCircuitBreaker, "get_instance", classmethod(lambda cls: breaker_stub)
    )
    repo = _make_repo()
    patch_write_deps(monkeypatch, repo)
    request = MagicMock()
    request.request_id = "req_b4_gate"
    request.organ = "thyroid"
    ok = await _write_result_to_db(
        request=request,
        fusion_result=fusion if fusion is not None else _fusion_ok(),
        sg_result={"passed": True, "consent_status_at_output": "granted"},
        sg_ok=sg_ok,
        timings={},
    )
    assert ok is True
    return repo.update_result.call_args.kwargs


class TestCusumThresholdUnchanged:
    """B-V2 前置：触发门槛没有被改小（4 触发 / 3 不触发）。"""

    def test_four_severe_trigger_pending(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)
        for _ in range(4):
            monitor.update(is_severe_hallucination=True)
        assert breaker.is_pending_confirmation is True
        assert breaker.is_suspended is False

    def test_three_severe_do_not_trigger(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)
        for _ in range(3):
            monitor.update(is_severe_hallucination=True)
        assert breaker.is_suspended is False
        assert breaker.is_pending_confirmation is False


class TestSafetyGateDegrade:
    @pytest.mark.asyncio
    async def test_pending_downgrades_to_needs_review(self, monkeypatch):
        kwargs = await _write(monkeypatch, _pending_breaker())
        assert kwargs["status"] == "needs_review"
        assert kwargs["review_reason"].startswith("ai_safety_circuit_breaker_")
        # 降级不阻断：结论正文照出
        assert kwargs["confidence"] == 0.82
        assert kwargs["findings"]["safety_gate"]["state"] == "pending_confirmation"
        assert kwargs["findings"]["safety_gate"]["pending"] is True

    @pytest.mark.asyncio
    async def test_suspended_downgrades_to_needs_review(self, monkeypatch):
        kwargs = await _write(monkeypatch, _suspended_breaker())
        assert kwargs["status"] == "needs_review"
        assert kwargs["review_reason"] == "ai_safety_circuit_breaker_suspended"

    @pytest.mark.asyncio
    async def test_active_keeps_baseline(self, monkeypatch):
        kwargs = await _write(monkeypatch, _active_breaker())
        assert kwargs["status"] == "completed"
        assert kwargs["review_reason"] is None
        assert kwargs["findings"]["safety_gate"]["state"] == "active"

    @pytest.mark.asyncio
    async def test_does_not_overwrite_fusion_review_reason(self, monkeypatch):
        from app.agents.protocols import AgentResponse
        from app.fusion.protocols import FusionResult

        fusion = FusionResult(needs_review=True, review_reason="self_rag_failed")
        fusion.final_response = AgentResponse(
            agent_name="thyroid", content="...", confidence=0.4
        )
        kwargs = await _write(monkeypatch, _pending_breaker(), fusion=fusion)
        assert kwargs["status"] == "needs_review"
        assert kwargs["review_reason"] == "self_rag_failed"

    @pytest.mark.asyncio
    async def test_sg_failure_stays_failed(self, monkeypatch):
        """failed 是更差终态，不因熔断被改写（熔断只降级"能出结论"的路径）。"""
        kwargs = await _write(monkeypatch, _suspended_breaker(), fusion=None, sg_ok=False)
        assert kwargs["status"] == "failed"
        assert kwargs["review_reason"] == "safety_gateway_unavailable"
