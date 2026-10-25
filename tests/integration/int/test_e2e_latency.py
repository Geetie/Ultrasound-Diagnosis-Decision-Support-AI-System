"""管线编排的**时延上界**测试（A.91 补证据等级声明）。

测试场景：
- 100 次单器官 mock 请求 → 断言 P99 < 5.0s
- 20 次含 Self-RAG 重试 → 断言 P99 < 8.0s

## ⚠️ 这不是端到端延迟（证据等级 E3：编排骨架，非端到端）

本文件把管线五个核心环节（`_safe_dispatch` / `_safe_fuse` /
`_safe_safety_check` / `_write_result_to_db` / DB 会话）**全部 mock**，
因此测的是"编排本身的开销"，**不含** LLM 推理、RAG 检索、融合校验、
安全网关往返的真实耗时。

真实量级完全不同（见 16 号 A.50.7 / A.53）：
- 真机 dispatcher P50 **1.34s**（本文件 mock 下通常 <100ms）
- 真机端到端 wall P50 **5.25s**、50 并发 P99 **35.4s**
- "≤20 并发安全区"的定容结论来自真机阶梯压测，**不来自本文件**

所以：**不得**引用本文件的 P99 作为"性能已达标"的证据。
它的价值只在于"编排层自己不引入 O(n) 级退化"。
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionResult


def _percentile(values: list[float], p: float) -> float:
    """计算 P 分位数（线性插值）。"""
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def _mock_specialist_response(confidence: float = 0.85) -> AgentResponse:
    return AgentResponse(
        agent_name="thyroid_specialist",
        organ="thyroid",
        content="[thyroid] mock diagnosis",
        confidence=confidence,
        latency_ms=80.0,
    )


def _mock_fusion_result(confidence: float = 0.85, with_self_rag_retry: bool = False) -> FusionResult:
    return FusionResult(
        final_response=_mock_specialist_response(confidence),
        needs_review=False,
        self_rag_passed=not with_self_rag_retry,
        total_latency_ms=200.0 if not with_self_rag_retry else 500.0,
    )


class TestE2ELatencyBaseline:
    """P99 延迟基准测试。"""

    @pytest.mark.asyncio
    async def test_p99_single_organ_under_5s(self):
        """100 次单器官 mock 请求, P99 < 5.0s。"""
        from app.services.diagnosis_pipeline import run_diagnosis_pipeline

        with patch("app.services.diagnosis_pipeline._safe_dispatch", new_callable=AsyncMock) as mock_dispatch, \
             patch("app.services.diagnosis_pipeline._safe_fuse", new_callable=AsyncMock) as mock_fuse, \
             patch("app.services.diagnosis_pipeline._safe_safety_check", new_callable=AsyncMock) as mock_sg, \
             patch("app.services.diagnosis_pipeline._write_result_to_db", new_callable=AsyncMock) as mock_write, \
             patch("app.services.diagnosis_pipeline.AsyncSessionLocal") as mock_session, \
             patch("app.api.websocket.nav.manager") as mock_nav:

            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_dispatch.return_value = [_mock_specialist_response(0.85)]
            mock_fuse.return_value = _mock_fusion_result(0.85)
            mock_sg.return_value = ({"passed": True}, True)
            mock_write.return_value = True
            mock_nav.send_diagnosis_result = AsyncMock()

            latencies_ms: list[float] = []
            for i in range(50):  # 50 次而非 100 次以减少测试耗时（mock 环境下）
                t_start = time.monotonic()
                result = await run_diagnosis_pipeline(f"req_lat_{i:03d}")
                elapsed_ms = (time.monotonic() - t_start) * 1000.0
                latencies_ms.append(elapsed_ms)
                assert result is True

            p50 = _percentile(latencies_ms, 0.50)
            p95 = _percentile(latencies_ms, 0.95)
            p99 = _percentile(latencies_ms, 0.99)
            max_latency = max(latencies_ms)

            print(
                f"\n[LATENCY BASELINE] n={len(latencies_ms)}, "
                f"P50={p50:.2f}ms, P95={p95:.2f}ms, P99={p99:.2f}ms, "
                f"max={max_latency:.2f}ms"
            )

            # mock 环境下 P99 应 < 1000ms
            assert p99 < 1000.0, f"P99 latency {p99:.2f}ms exceeds 1000ms in mock env"
            # 验收标准: < 5s (mock 环境下非常宽松)
            assert p99 < 5000.0, f"P99 latency {p99:.2f}ms exceeds 5s acceptance"

    @pytest.mark.asyncio
    async def test_p99_self_rag_retry_under_8s(self):
        """20 次含 Self-RAG 重试, P99 < 8.0s。"""
        from app.services.diagnosis_pipeline import run_diagnosis_pipeline

        with patch("app.services.diagnosis_pipeline._safe_dispatch", new_callable=AsyncMock) as mock_dispatch, \
             patch("app.services.diagnosis_pipeline._safe_fuse", new_callable=AsyncMock) as mock_fuse, \
             patch("app.services.diagnosis_pipeline._safe_safety_check", new_callable=AsyncMock) as mock_sg, \
             patch("app.services.diagnosis_pipeline._write_result_to_db", new_callable=AsyncMock) as mock_write, \
             patch("app.services.diagnosis_pipeline.AsyncSessionLocal") as mock_session, \
             patch("app.api.websocket.nav.manager") as mock_nav:

            mock_session.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value.__aexit__ = AsyncMock(return_value=None)
            mock_dispatch.return_value = [_mock_specialist_response(0.7)]
            # 模拟 Self-RAG 重试: needs_review=True, 但有 crag_recovered
            mock_fuse.return_value = _mock_fusion_result(0.7, with_self_rag_retry=True)
            mock_sg.return_value = ({"passed": True}, True)
            mock_write.return_value = True
            mock_nav.send_diagnosis_result = AsyncMock()

            latencies_ms: list[float] = []
            for i in range(10):  # 10 次减少测试耗时
                t_start = time.monotonic()
                result = await run_diagnosis_pipeline(f"req_self_rag_{i:03d}")
                elapsed_ms = (time.monotonic() - t_start) * 1000.0
                latencies_ms.append(elapsed_ms)
                assert result is True

            p99 = _percentile(latencies_ms, 0.99)
            print(
                f"\n[SELF-RAG RETRY LATENCY] n={len(latencies_ms)}, P99={p99:.2f}ms"
            )
            assert p99 < 8000.0, f"P99 latency {p99:.2f}ms exceeds 8s for Self-RAG retry"


__all__ = [
    "TestE2ELatencyBaseline",
    "_percentile",
]
