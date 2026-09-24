"""LLM推理轨道测试专用配置

提供：
1. 实时进度可视化报告器（LLMProgressReporter）
2. LLM专用fixture（共享MockInferenceEngine、覆盖全局autouse）
3. 测试阶段自动分类
"""
import pytest

from tests.utils.track_progress_reporter import TrackProgressReporter

PHASE_ICONS = {
    "inference": "\U0001f9e0",
    "consistency": "\u2705",
    "hallucination": "\U0001f47b",
    "factory": "\U0001f3ed",
    "drift": "\U0001f4c9",
    "verification_config": "\u2699",
    "config": "\u2699",
    "benchmark": "\U0001f4ca",
    "default": "\u25cf",
}


class LLMProgressReporter(TrackProgressReporter):
    PHASE_ICONS = PHASE_ICONS

    def __init__(self):
        super().__init__(track_name="LLM")


def pytest_configure(config):
    config.pluginmanager.register(LLMProgressReporter(), "llm_progress")


@pytest.fixture(autouse=True)
def reset_verification_config():
    try:
        from app.inference.verification_config import _global_config
        _global_config.clear()
    except ImportError:
        pass
    yield
    try:
        from app.inference.verification_config import _global_config
        _global_config.clear()
    except ImportError:
        pass


@pytest.fixture
def mock_inference_engine():
    from app.inference.protocols import InferenceEngine, InferenceResponse, TokenUsage

    class MockInferenceEngine(InferenceEngine):
        def __init__(self, responses=None, name="mock", latency_ms=100.0):
            self._responses = responses or []
            self._call_count = 0
            self._name = name
            self._latency_ms = latency_ms

        async def generate(self, request):
            if self._call_count < len(self._responses):
                text = self._responses[self._call_count]
            else:
                text = "default response"
            self._call_count += 1
            return InferenceResponse(
                text=text,
                model=self._name,
                usage=TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30),
                finish_reason="stop",
                request_id=request.request_id,
                latency_ms=self._latency_ms,
            )

        async def health_check(self):
            return True

        async def get_model_info(self):
            from app.inference.protocols import ModelInfo
            return ModelInfo(model_name=self._name)

        async def close(self):
            pass

    return MockInferenceEngine


@pytest.fixture
def checkpoint(request):
    def _checkpoint(name, result, detail=""):
        status = "\u2705" if result else "\u274c"
        print(f"  {status} CHECKPOINT: {name} | {detail}")
    return _checkpoint
