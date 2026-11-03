"""LLM 上游断流的契约（用户目标原话："省得 API 上游断了，让你停止工作"）。

三件事必须成立：
1. **可重试错误会被重试**（不是一碰失败就放弃）；
2. **连续失败达阈值 → 熔断打开**（不无限打上游）；
3. **熔断期间错误标记为不可重试**（否则调用方白白再打一轮，熔断失去意义）。
"""

from __future__ import annotations

import httpx
import pytest

from app.inference.base import BaseHttpInferenceEngine
from app.inference.protocols import InferenceErrorCode, InferenceRequest

pytestmark = pytest.mark.track_inference


class _DummyEngine(BaseHttpInferenceEngine):
    """最小可测引擎：把每次请求变成一次"连接失败"。"""

    def __init__(self, **kwargs):
        super().__init__(
            base_url="http://127.0.0.1:59999", logger_name="outage-probe", **kwargs
        )
        self.attempts = 0

    @property
    def _generate_endpoint(self) -> str:
        return "/v1/chat/completions"

    def _build_payload(self, request):  # pragma: no cover - 不会走到
        return {}

    def _parse_response(self, data, request):  # pragma: no cover
        raise NotImplementedError

    async def _execute_single_request(self, payload, request, start_time, attempt):
        self.attempts += 1
        raise httpx.ConnectError("connection refused")

    async def close(self) -> None:
        return None


def _engine(**overrides):
    kwargs = dict(
        timeout_seconds=1.0,
        connect_timeout_seconds=0.5,
        default_max_tokens=16,
        default_temperature=0.1,
        default_top_p=0.95,
        max_retries=2,
        retry_delay_base=0.0,
        retry_delay_max=0.0,
        circuit_breaker_timeout=30.0,
        circuit_breaker_failure_threshold=3,
        circuit_breaker_success_threshold=1,
        pool_max_connections=2,
        pool_keepalive_expiry=10.0,
    )
    kwargs.update(overrides)
    return _DummyEngine(**kwargs)


@pytest.mark.asyncio
async def test_connect_failure_is_retried():
    """断流时按 max_retries 重试（max_retries=2 → 共 3 次尝试）。"""
    engine = _engine()
    with pytest.raises(Exception):
        await engine.generate(InferenceRequest(prompt="x", request_id="r1"))
    assert engine.attempts == 3


@pytest.mark.asyncio
async def test_failure_reported_as_retryable_connection_error():
    engine = _engine()
    with pytest.raises(Exception) as ei:
        await engine.generate(InferenceRequest(prompt="x", request_id="r2"))
    assert ei.value.error_code == InferenceErrorCode.CONNECTION_FAILED
    assert ei.value.retryable is True


@pytest.mark.asyncio
async def test_circuit_opens_after_threshold():
    engine = _engine(circuit_breaker_failure_threshold=2, max_retries=0)
    with pytest.raises(Exception):
        await engine.generate(InferenceRequest(prompt="x", request_id="r3"))
    with pytest.raises(Exception):
        await engine.generate(InferenceRequest(prompt="x", request_id="r4"))
    assert engine._circuit_breaker_state.value == "open"


@pytest.mark.asyncio
async def test_open_circuit_rejects_without_retry_flag():
    """熔断期间**不可重试**：调用方应走降级，而不是再打一轮。"""
    engine = _engine(circuit_breaker_failure_threshold=1, max_retries=0)
    with pytest.raises(Exception):
        await engine.generate(InferenceRequest(prompt="x", request_id="r5"))
    assert engine._circuit_breaker_state.value == "open"

    attempts_before = engine.attempts
    with pytest.raises(Exception) as ei:
        await engine.generate(InferenceRequest(prompt="x", request_id="r6"))
    assert ei.value.retryable is False, "熔断期间标记可重试会导致无效重打"
    assert engine.attempts == attempts_before, "熔断期间不应再发请求"
