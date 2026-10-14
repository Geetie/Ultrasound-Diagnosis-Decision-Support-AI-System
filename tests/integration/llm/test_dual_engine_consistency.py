import logging
import os

import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_int]

import httpx

from app.inference.consistency import (
    ConsistencyReport,
    ConsistencyVerifier,
    HallucinationDetector,
)
from app.inference.llama_cpp import LlamaCppEngine
from app.inference.vllm import VLLMEngine

logger = logging.getLogger(__name__)

LLAMA_CPP_URL = os.environ.get("LLAMA_CPP_URL", "http://localhost:8001")
VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:8002")
VLLM_MODEL = os.environ.get("VLLM_MODEL", "Qwen2.5-1.5B-Instruct")


def _is_service_available(url: str, timeout: float = 3.0) -> bool:
    try:
        with httpx.Client() as client:
            resp = client.get(f"{url}/health", timeout=timeout)
            return resp.status_code == 200
    except Exception:
        logger.warning("_is_service_available: 未预期异常，按原逻辑降级", exc_info=True)
        return False


skip_no_llama_cpp = pytest.mark.skipif(
    not _is_service_available(LLAMA_CPP_URL),
    reason="llama.cpp service not available at " + LLAMA_CPP_URL,
)

skip_no_vllm = pytest.mark.skipif(
    not _is_service_available(VLLM_URL),
    reason="vLLM service not available at " + VLLM_URL,
)


@pytest.mark.integration
class TestDualEngineConsistency:
    """双引擎一致性集成测试"""

    @skip_no_llama_cpp
    @skip_no_vllm
    @pytest.mark.asyncio
    async def test_basic_consistency_verification(self):
        engine_a = LlamaCppEngine(base_url=LLAMA_CPP_URL)
        engine_b = VLLMEngine(base_url=VLLM_URL, default_model=VLLM_MODEL)

        cases = [
            {
                "case_id": "int_001",
                "organ": "thyroid",
                "prompt": "请对以下甲状腺超声描述进行TI-RADS分级：甲状腺右叶结节，边界清晰，形态规则，无钙化，无血流",
                "expected_grading": "3",
            },
            {
                "case_id": "int_002",
                "organ": "liver",
                "prompt": "请对以下肝脏超声描述进行LI-RADS分级：肝脏右叶占位，边界不清，中等强化",
                "expected_grading": "4",
            },
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert isinstance(report, ConsistencyReport)
        assert report.total_cases == len(cases)
        assert 0 <= report.grading_consistency_rate <= 1.0

    @skip_no_llama_cpp
    @pytest.mark.asyncio
    async def test_llama_cpp_engine_basic(self):
        engine = LlamaCppEngine(base_url=LLAMA_CPP_URL)
        from app.inference.protocols import InferenceRequest

        request = InferenceRequest(
            prompt="请对以下甲状腺超声描述进行TI-RADS分级：甲状腺右叶囊性结节",
            max_tokens=256,
        )
        response = await engine.generate(request)
        assert response.text is not None
        assert len(response.text) > 0

    @skip_no_vllm
    @pytest.mark.asyncio
    async def test_vllm_engine_basic(self):
        engine = VLLMEngine(base_url=VLLM_URL, default_model=VLLM_MODEL)
        from app.inference.protocols import InferenceRequest

        request = InferenceRequest(
            prompt="请对以下肝脏超声描述进行LI-RADS分级：肝脏右叶占位",
            max_tokens=256,
        )
        response = await engine.generate(request)
        assert response.text is not None
        assert len(response.text) > 0

    @skip_no_llama_cpp
    @skip_no_vllm
    @pytest.mark.asyncio
    async def test_grading_consistency_rate(self):
        engine_a = LlamaCppEngine(base_url=LLAMA_CPP_URL)
        engine_b = VLLMEngine(base_url=VLLM_URL, default_model=VLLM_MODEL)

        cases = [
            {
                "case_id": f"int_rate_{i:03d}",
                "organ": "thyroid",
                "prompt": f"请对以下甲状腺超声描述进行TI-RADS分级：甲状腺结节{i+1}号",
                "expected_grading": "3",
            }
            for i in range(10)
        ]

        verifier = ConsistencyVerifier(engine_a=engine_a, engine_b=engine_b)
        report = await verifier.verify(cases)

        assert report.grading_consistency_rate >= 0.8, f"Grading consistency rate {report.grading_consistency_rate} below 0.8 threshold"

    def test_hallucination_detection_with_real_output(self):
        detector = HallucinationDetector()

        test_text = "甲状腺良性结节，TI-RADS 3类，建议6个月随访复查"
        result = detector.detect(test_text, "thyroid")
        assert hasattr(result, 'is_hallucination')
        assert hasattr(result, 'issues')
        assert isinstance(result.issues, list)

    @skip_no_llama_cpp
    @pytest.mark.asyncio
    async def test_llama_cpp_single_engine_consistency(self):
        engine = LlamaCppEngine(base_url=LLAMA_CPP_URL)

        cases = [
            {
                "case_id": "llama_single_001",
                "organ": "thyroid",
                "prompt": "请对以下甲状腺超声描述进行TI-RADS分级：甲状腺右叶结节，边界清晰，形态规则，无钙化",
                "expected_grading": "3",
            },
        ]

        from app.inference.protocols import InferenceRequest
        request = InferenceRequest(
            prompt=cases[0]["prompt"],
            max_tokens=256,
            temperature=0.1,
        )
        response = await engine.generate(request)
        assert response.text is not None
        assert len(response.text) > 0

        from app.inference.consistency import extract_grading
        grading = extract_grading(response.text, "thyroid")
        assert grading is not None, f"Expected grading in response: {response.text[:200]}"
