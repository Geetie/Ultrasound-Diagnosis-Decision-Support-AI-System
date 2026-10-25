import pytest

from app.inference.factory import create_inference_engine, create_inference_engine_from_settings
from app.inference.llama_cpp import LlamaCppEngine
from app.inference.protocols import InferenceError
from app.inference.vllm import VLLMEngine

pytestmark = [pytest.mark.track_llm, pytest.mark.track_inf]


class TestCreateInferenceEngine:
    def test_create_llama_cpp(self):
        engine = create_inference_engine("llama.cpp")
        assert isinstance(engine, LlamaCppEngine)
        assert engine._base_url == "http://localhost:8001"

    def test_create_vllm(self):
        engine = create_inference_engine("vllm")
        assert isinstance(engine, VLLMEngine)
        assert engine._base_url == "http://localhost:8002"

    def test_create_llama_cpp_custom_host_port(self):
        engine = create_inference_engine(
            "llama.cpp",
            llama_cpp_host="192.168.1.100",
            llama_cpp_port=9001,
        )
        assert isinstance(engine, LlamaCppEngine)
        assert engine._base_url == "http://192.168.1.100:9001"

    def test_create_vllm_custom_host_port(self):
        engine = create_inference_engine(
            "vllm",
            vllm_host="10.0.0.1",
            vllm_port=9002,
            vllm_default_model="Qwen2.5-7B",
        )
        assert isinstance(engine, VLLMEngine)
        assert engine._base_url == "http://10.0.0.1:9002"
        assert engine._default_model == "Qwen2.5-7B"

    def test_create_llama_cpp_compact_max_tokens(self):
        engine = create_inference_engine("llama.cpp", max_tokens=4096)
        assert isinstance(engine, LlamaCppEngine)
        assert engine._default_max_tokens == 1024

    def test_create_vllm_full_max_tokens(self):
        engine = create_inference_engine("vllm", max_tokens=4096)
        assert isinstance(engine, VLLMEngine)
        assert engine._default_max_tokens == 4096

    def test_unknown_engine_type(self):
        with pytest.raises(InferenceError) as exc_info:
            create_inference_engine("trtllm")
        assert "unknown inference engine type" in str(exc_info.value)

    def test_custom_temperature_and_top_p(self):
        engine = create_inference_engine(
            "llama.cpp",
            temperature=0.2,
            top_p=0.9,
        )
        assert engine._default_temperature == 0.2
        assert engine._default_top_p == 0.9

    def test_llama_cpp_retries_and_circuit_breaker(self):
        engine = create_inference_engine(
            "llama.cpp",
            max_retries=5,
            retry_delay_base=2.0,
            retry_delay_max=20.0,
            circuit_breaker_timeout=60.0,
            circuit_breaker_failure_threshold=10,
            circuit_breaker_success_threshold=5,
            pool_max_connections=20,
            pool_keepalive_expiry=600.0,
        )
        assert engine._max_retries == 5
        assert engine._retry_delay_base == 2.0
        assert engine._retry_delay_max == 20.0
        assert engine._circuit_breaker_timeout == 60.0
        assert engine._circuit_breaker_failure_threshold == 10
        assert engine._circuit_breaker_success_threshold == 5
        assert engine._max_connections == 20
        assert engine._keepalive_expiry == 600.0

    def test_vllm_retries_and_circuit_breaker(self):
        engine = create_inference_engine(
            "vllm",
            max_retries=5,
            retry_delay_base=2.0,
            retry_delay_max=20.0,
            circuit_breaker_timeout=60.0,
            circuit_breaker_failure_threshold=10,
            circuit_breaker_success_threshold=5,
            pool_max_connections=20,
            pool_keepalive_expiry=600.0,
        )
        assert engine._max_retries == 5
        assert engine._retry_delay_base == 2.0
        assert engine._retry_delay_max == 20.0
        assert engine._circuit_breaker_timeout == 60.0


class TestCreateInferenceEngineFromSettings:
    """从 settings 造引擎。

    ⚠️ A.88 口径变更（2026-10-25）：``Settings`` 的默认档位从
    ``compact``/``llama.cpp`` 改为 ``full``/``vllm``（对齐生产 .env 实测值；
    AGENTS.md 硬规则 #4 禁止把 llama.cpp 当**主**生成引擎，而旧的代码默认
    恰恰在把任何裸 ``Settings()`` 推回 CPU 引擎）。
    因此下面凡是"验证 llama.cpp 分支行为"的用例，一律**显式**指定
    ``engine_type="llama.cpp"`` —— 显式 compact 降级路径仍然完全可用，
    只是不再是默认。
    """

    def test_default_settings_is_vllm(self):
        """默认 settings → vLLM 引擎（生产对齐；反证：改回 compact 即红）。"""
        engine = create_inference_engine_from_settings()
        assert isinstance(engine, VLLMEngine)

    def test_override_engine_type(self):
        """显式覆盖必须真的生效：默认 vllm → 显式 llama.cpp（compact 链路验证用）。"""
        engine = create_inference_engine_from_settings(engine_type="llama.cpp")
        assert isinstance(engine, LlamaCppEngine)

    def test_override_host_port(self):
        engine = create_inference_engine_from_settings(
            engine_type="llama.cpp",
            llama_cpp_host="custom-host",
            llama_cpp_port=9999,
        )
        assert isinstance(engine, LlamaCppEngine)
        assert engine._base_url == "http://custom-host:9999"

    def test_falsy_temperature_not_overridden(self):
        engine = create_inference_engine_from_settings(
            engine_type="llama.cpp", temperature=0.0
        )
        assert isinstance(engine, LlamaCppEngine)
        assert engine._default_temperature == 0.0

    def test_falsy_top_p_not_overridden(self):
        engine = create_inference_engine_from_settings(
            engine_type="llama.cpp", top_p=0.0
        )
        assert isinstance(engine, LlamaCppEngine)
        assert engine._default_top_p == 0.0
