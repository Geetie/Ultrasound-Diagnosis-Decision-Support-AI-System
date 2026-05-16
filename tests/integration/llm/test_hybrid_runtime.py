import os

import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_inf, pytest.mark.slow]

_HAS_HYBRID_ENV = (
    os.environ.get("VLLM_PORT") is not None
    and os.environ.get("LLAMA_CPP_PORT") is not None
    and os.environ.get("TENSORRT_ENGINE_PATH") is not None
)


@pytest.mark.skipif(not _HAS_HYBRID_ENV, reason="需要FunASR+vLLM+TensorRT混合运行环境")
class TestHybridRuntimeMemoryFragmentation:
    async def test_hybrid_runtime_4h_fragmentation_below_warning(self):
        from app.inference.gpu_memory import GPUMemoryMonitor

        monitor = GPUMemoryMonitor()
        fragmentation = await monitor.measure_fragmentation_after_duration(
            duration_seconds=14400,
            request_rate_per_hour=10,
        )
        assert fragmentation < 0.30

    async def test_hybrid_runtime_4h_fragmentation_below_critical(self):
        from app.inference.gpu_memory import GPUMemoryMonitor

        monitor = GPUMemoryMonitor()
        fragmentation = await monitor.measure_fragmentation_after_duration(
            duration_seconds=14400,
            request_rate_per_hour=10,
        )
        assert fragmentation < 0.40
