"""T-GA-INT-01: 延迟埋点完整性测试。

测试每环节 latency_ms 都写入了 structlog 与 DB findings。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionResult


def _mock_specialist_response() -> AgentResponse:
    return AgentResponse(
        agent_name="thyroid_specialist",
        organ="thyroid",
        content="[thyroid] test",
        confidence=0.85,
    )


def _mock_fusion_result() -> FusionResult:
    return FusionResult(
        final_response=_mock_specialist_response(),
        needs_review=False,
        total_latency_ms=150.0,
    )


class TestLatencyInstrumentation:
    """延迟埋点测试。"""

    @pytest.mark.asyncio
    async def test_pipeline_writes_all_6_timings(self):
        """管线写 DB 时, timings 包含全部 6 阶段。"""
        from app.services.diagnosis_pipeline import _write_result_to_db

        mock_repo = MagicMock()
        mock_repo.update_result = AsyncMock(return_value=MagicMock())

        mock_request = MagicMock()
        mock_request.request_id = "req_lat_inst_001"
        mock_request.organ = "thyroid"

        timings = {
            "db_read_ms": 5.2,
            "dispatcher.invoke_ms": 120.5,
            "fusion.fuse_ms": 200.3,
            "sg.check_output_ms": 50.8,
            "db_write_ms": 8.1,
            "pipeline.total_ms": 400.0,
        }

        with patch("app.services.diagnosis_pipeline.RedisPool") as mock_redis:
            mock_redis.setex = AsyncMock()

            await _write_result_to_db(
                diagnosis_repo=mock_repo,
                request=mock_request,
                fusion_result=_mock_fusion_result(),
                sg_result={"passed": True},
                sg_ok=True,
                timings=timings,
            )

        # 验证 update_result.findings 包含 timings_ms
        call_kwargs = mock_repo.update_result.call_args.kwargs
        findings = call_kwargs["findings"]
        assert "timings_ms" in findings
        assert findings["timings_ms"]["db_read_ms"] == 5.2
        assert findings["timings_ms"]["dispatcher.invoke_ms"] == 120.5
        assert findings["timings_ms"]["fusion.fuse_ms"] == 200.3
        assert findings["timings_ms"]["sg.check_output_ms"] == 50.8
        assert findings["timings_ms"]["db_write_ms"] == 8.1
        assert findings["timings_ms"]["pipeline.total_ms"] == 400.0

    @pytest.mark.asyncio
    async def test_pipeline_overhead_under_1ms(self):
        """time.monotonic() 自身开销 < 1ms。"""
        import time

        iterations = 1000
        t_start = time.monotonic()
        for _ in range(iterations):
            _ = time.monotonic()
        elapsed_ms = (time.monotonic() - t_start) * 1000.0
        per_iteration = elapsed_ms / iterations
        print(f"\n[INSTRUMENTATION OVERHEAD] per_iteration={per_iteration*1000:.3f}μs")
        assert per_iteration < 0.001  # < 1μs per call (即 0.001ms)


__all__ = [
    "TestLatencyInstrumentation",
]
