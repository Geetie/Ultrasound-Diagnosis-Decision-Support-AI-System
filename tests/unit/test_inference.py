import json
import time
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from app.inference.base import BaseHttpInferenceEngine
from app.inference.llama_cpp import LlamaCppEngine
from app.inference.protocols import (
    InferenceEngine,
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    TokenUsage,
    _DeprecatedErrorCode,
)
from app.inference.vllm import VLLMEngine


class TestInferenceRequest:
    def test_default_values(self):
        req = InferenceRequest(prompt="test prompt")
        assert req.prompt == "test prompt"
        assert req.max_tokens is None
        assert req.temperature is None
        assert req.top_p is None
        assert req.stop is None
        assert req.request_id is None
        assert req.session_id is None
        assert req.extra == {}

    def test_custom_values(self):
        req = InferenceRequest(
            prompt="diagnose thyroid",
            max_tokens=4096,
            temperature=0.05,
            top_p=0.9,
            stop=["\n", "###"],
            request_id="req_001",
            session_id="sess_001",
            extra={"model": "qwen2.5-7b"},
        )
        assert req.max_tokens == 4096
        assert req.temperature == 0.05
        assert req.stop == ["\n", "###"]
        assert req.request_id == "req_001"
        assert req.extra["model"] == "qwen2.5-7b"

    def test_empty_prompt_rejected(self):
        with pytest.raises(ValidationError, match="prompt must not be empty"):
            InferenceRequest(prompt="")

    def test_whitespace_prompt_rejected(self):
        with pytest.raises(ValidationError, match="prompt must not be empty"):
            InferenceRequest(prompt="   ")

    def test_zero_max_tokens_rejected(self):
        with pytest.raises(ValidationError, match="max_tokens must be a positive integer"):
            InferenceRequest(prompt="test", max_tokens=0)

    def test_negative_max_tokens_rejected(self):
        with pytest.raises(ValidationError, match="max_tokens must be a positive integer"):
            InferenceRequest(prompt="test", max_tokens=-1)

    def test_none_max_tokens_accepted(self):
        req = InferenceRequest(prompt="test", max_tokens=None)
        assert req.max_tokens is None

    def test_negative_temperature_rejected(self):
        with pytest.raises(ValidationError, match="temperature must be between"):
            InferenceRequest(prompt="test", temperature=-0.1)

    def test_excessive_temperature_rejected(self):
        with pytest.raises(ValidationError, match="temperature must be between"):
            InferenceRequest(prompt="test", temperature=2.5)

    def test_none_temperature_accepted(self):
        req = InferenceRequest(prompt="test", temperature=None)
        assert req.temperature is None

    def test_negative_top_p_rejected(self):
        with pytest.raises(ValidationError, match="top_p must be between"):
            InferenceRequest(prompt="test", top_p=-0.1)

    def test_excessive_top_p_rejected(self):
        with pytest.raises(ValidationError, match="top_p must be between"):
            InferenceRequest(prompt="test", top_p=1.5)

    def test_none_top_p_accepted(self):
        req = InferenceRequest(prompt="test", top_p=None)
        assert req.top_p is None

    def test_empty_stop_list_rejected(self):
        with pytest.raises(ValidationError, match="stop must be None or a non-empty list"):
            InferenceRequest(prompt="test", stop=[])


class TestInferenceResponse:
    def test_default_values(self):
        resp = InferenceResponse(text="result text")
        assert resp.text == "result text"
        assert resp.model == ""
        assert resp.finish_reason == ""
        assert resp.request_id is None
        assert resp.latency_ms == 0.0

    def test_with_usage(self):
        usage = TokenUsage(prompt_tokens=10, completion_tokens=20, total_tokens=30)
        resp = InferenceResponse(
            text="diagnosis result",
            model="qwen2.5-7b",
            usage=usage,
            finish_reason="stop",
            request_id="req_001",
            latency_ms=1500.5,
        )
        assert resp.usage.prompt_tokens == 10
        assert resp.usage.completion_tokens == 20
        assert resp.usage.total_tokens == 30
        assert resp.latency_ms == 1500.5


class TestInferenceError:
    def test_error_code_in_str(self):
        err = InferenceError(
            error_code=InferenceErrorCode.TIMEOUT,
            message="Request timed out",
        )
        assert err.error_code == InferenceErrorCode.TIMEOUT
        assert "E2002_TIMEOUT" in str(err)
        assert "Request timed out" in str(err)
        assert err.retryable is False
        assert err.retry_strategy == "no_retry"

    def test_retryable_error(self):
        err = InferenceError(
            error_code=InferenceErrorCode.CONNECTION_FAILED,
            message="Connection failed",
            retryable=True,
            retry_strategy="retry_after_5s",
        )
        assert err.retryable is True
        assert err.retry_strategy == "retry_after_5s"

    def test_message_attribute_exists(self):
        err = InferenceError(
            error_code=InferenceErrorCode.TIMEOUT,
            message="test",
        )
        assert hasattr(err, "message")
        assert err.message == "test"


class TestInferenceErrorCode:
    def test_active_codes_exist(self):
        actual_codes = [code.value for code in InferenceErrorCode]
        assert "E2002_TIMEOUT" in actual_codes
        assert "E2002_CONNECTION_FAILED" in actual_codes
        assert "E2002_GENERATION_FAILED" in actual_codes
        assert len(InferenceErrorCode) == 3

    def test_deprecated_codes_still_accessible(self):
        assert _DeprecatedErrorCode.MODEL_LOAD_FAILED == "E2002_MODEL_LOAD_FAILED"
        assert _DeprecatedErrorCode.INVALID_REQUEST == "E2002_INVALID_REQUEST"
        assert _DeprecatedErrorCode.HEALTH_CHECK_FAILED == "E2002_HEALTH_CHECK_FAILED"

    def test_missing_maps_deprecated_to_active(self):
        result = InferenceErrorCode._missing_("E2002_MODEL_LOAD_FAILED")
        assert result is InferenceErrorCode.GENERATION_FAILED

        result = InferenceErrorCode._missing_("E2002_INVALID_REQUEST")
        assert result is InferenceErrorCode.GENERATION_FAILED

        result = InferenceErrorCode._missing_("E2002_HEALTH_CHECK_FAILED")
        assert result is InferenceErrorCode.GENERATION_FAILED

    def test_missing_returns_none_for_unknown(self):
        result = InferenceErrorCode._missing_("E9999_UNKNOWN")
        assert result is None


def _make_mock_handler(response: httpx.Response | None = None, exc: Exception | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        return response
    return handler


class TestLlamaCppEngine:
    def test_init_default_values(self):
        engine = LlamaCppEngine()
        assert engine._base_url == "http://localhost:8001"
        assert engine._timeout_seconds == 30.0
        assert engine._default_max_tokens == 1024
        assert engine._default_temperature == 0.1

    def test_init_custom_values(self):
        engine = LlamaCppEngine(
            base_url="http://gpu-server:8001",
            timeout_seconds=60.0,
            default_max_tokens=2048,
            default_temperature=0.05,
        )
        assert engine._base_url == "http://gpu-server:8001"
        assert engine._timeout_seconds == 60.0
        assert engine._default_max_tokens == 2048

    def test_base_url_trailing_slash_stripped(self):
        engine = LlamaCppEngine(base_url="http://localhost:8001/")
        assert engine._base_url == "http://localhost:8001"

    @pytest.mark.asyncio
    async def test_generate_success(self, llama_engine):
        mock_response = httpx.Response(
            200,
            json={
                "choices": [{"text": "TI-RADS 4a", "finish_reason": "stop", "index": 0}],
                "model": "qwen2.5-7b-q4km",
                "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60},
            },
            request=httpx.Request("POST", "http://localhost:8001/v1/completions"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(
            prompt="Evaluate thyroid nodule",
            request_id="req_test_001",
            session_id="sess_test_001",
        )
        response = await llama_engine.generate(request)

        assert response.text == "TI-RADS 4a"
        assert response.model == "qwen2.5-7b-q4km"
        assert response.usage.prompt_tokens == 50
        assert response.usage.completion_tokens == 10
        assert response.usage.total_tokens == 60
        assert response.finish_reason == "stop"
        assert response.request_id == "req_test_001"
        assert response.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_generate_uses_engine_default_max_tokens_when_none(self, llama_engine):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [{"text": "Result", "finish_reason": "stop", "index": 0}],
                    "model": "test",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
                request=request,
            )

        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        assert request.max_tokens is None
        await llama_engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert body["max_tokens"] == 1024

    @pytest.mark.asyncio
    async def test_generate_uses_request_max_tokens_when_provided(self, llama_engine):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [{"text": "Result", "finish_reason": "stop", "index": 0}],
                    "model": "test",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
                request=request,
            )

        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test", max_tokens=512)
        await llama_engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert body["max_tokens"] == 512

    @pytest.mark.asyncio
    async def test_generate_server_error(self, llama_engine):
        mock_response = httpx.Response(
            500,
            text="Internal Server Error",
            request=httpx.Request("POST", "http://localhost:8001/v1/completions"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await llama_engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert exc_info.value.retryable is True
        assert exc_info.value.retry_strategy == "retry_after_5s"

    @pytest.mark.asyncio
    async def test_generate_client_error_not_retryable(self, llama_engine):
        mock_response = httpx.Response(
            400,
            text="Bad Request: invalid prompt",
            request=httpx.Request("POST", "http://localhost:8001/v1/completions"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await llama_engine.generate(request)

        assert exc_info.value.retryable is False
        assert exc_info.value.retry_strategy == "no_retry"

    @pytest.mark.asyncio
    async def test_generate_empty_choices(self, llama_engine):
        mock_response = httpx.Response(
            200,
            json={"choices": [], "model": "test"},
            request=httpx.Request("POST", "http://localhost:8001/v1/completions"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await llama_engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_generate_invalid_json_response(self, llama_engine):
        mock_response = httpx.Response(
            200,
            text="<html>not json</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("POST", "http://localhost:8001/v1/completions"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await llama_engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert "Expecting value" in exc_info.value.detail

    @pytest.mark.asyncio
    async def test_generate_timeout(self, llama_engine):
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.TimeoutException("timeout"))),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test", request_id="req_timeout")
        with pytest.raises(InferenceError) as exc_info:
            await llama_engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.TIMEOUT
        assert exc_info.value.retryable is True
        assert exc_info.value.retry_strategy == "retry_after_5s"

    @pytest.mark.asyncio
    async def test_generate_connection_failed(self, llama_engine):
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.ConnectError("refused"))),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await llama_engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.CONNECTION_FAILED
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_health_check_success(self, llama_engine):
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "http://localhost:8001/health"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        result = await llama_engine.health_check()
        assert result is True

    @pytest.mark.asyncio
    async def test_health_check_non_200_returns_false(self, llama_engine):
        mock_response = httpx.Response(
            503,
            text="Service Unavailable",
            request=httpx.Request("GET", "http://localhost:8001/health"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        result = await llama_engine.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_health_check_failure(self, llama_engine):
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.ConnectError("refused"))),
            base_url=llama_engine._base_url,
        )
        result = await llama_engine.health_check()
        assert result is False

    @pytest.mark.asyncio
    async def test_get_model_info_success(self, llama_engine):
        llama_engine._model_info_cache = None
        llama_engine._model_info_cached_at = 0.0

        mock_response = httpx.Response(
            200,
            json={"data": [{"id": "qwen2.5-7b-instruct-q4_k_m"}]},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        info = await llama_engine.get_model_info()
        assert info.model_name == "qwen2.5-7b-instruct-q4_k_m"

        cached_info = await llama_engine.get_model_info()
        assert cached_info.model_name == info.model_name

    @pytest.mark.asyncio
    async def test_get_model_info_empty(self, llama_engine):
        llama_engine._model_info_cache = None
        llama_engine._model_info_cached_at = 0.0

        mock_response = httpx.Response(
            200,
            json={"data": []},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        info = await llama_engine.get_model_info()
        assert info.model_name == ""

    @pytest.mark.asyncio
    async def test_get_model_info_connection_error(self, llama_engine):
        llama_engine._model_info_cache = None
        llama_engine._model_info_cached_at = 0.0

        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.ConnectError("refused"))),
            base_url=llama_engine._base_url,
        )
        info = await llama_engine.get_model_info()
        assert info.model_name == ""

    @pytest.mark.asyncio
    async def test_get_model_info_invalid_json_returns_empty(self, llama_engine):
        llama_engine._model_info_cache = None
        llama_engine._model_info_cached_at = 0.0

        mock_response = httpx.Response(
            200,
            text="<html>not json</html>",
            headers={"content-type": "text/html"},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )
        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=llama_engine._base_url,
        )
        info = await llama_engine.get_model_info()
        assert info.model_name == ""

    @pytest.mark.asyncio
    async def test_close(self, llama_engine):
        mock_client = AsyncMock()
        mock_client.is_closed = False

        def update_is_closed(*args, **kwargs):
            mock_client.is_closed = True

        mock_client.aclose.side_effect = update_is_closed
        llama_engine._client = mock_client

        await llama_engine.close()

        mock_client.aclose.assert_awaited_once()
        assert mock_client.is_closed is True
        assert llama_engine._client is None
        assert llama_engine._closed is True

    @pytest.mark.asyncio
    async def test_close_no_client(self):
        engine = LlamaCppEngine()
        await engine.close()
        assert engine._closed is True

    @pytest.mark.asyncio
    async def test_generate_after_close_raises_error(self):
        engine = LlamaCppEngine()
        await engine.close()

        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert "closed" in str(exc_info.value).lower()

    def test_get_client_lazy_initialization_via_attribute(self):
        engine = LlamaCppEngine()
        assert engine._client is None
        assert engine._closed is False

        engine._closed = True
        assert engine._closed is True

    @pytest.mark.asyncio
    async def test_get_client_lazy_initialization(self, llama_engine):
        assert llama_engine._client is None

        client1 = await llama_engine._get_client()
        assert llama_engine._client is not None

        client2 = await llama_engine._get_client()
        assert client1 is client2

    @pytest.mark.asyncio
    async def test_get_client_after_close_raises(self):
        engine = LlamaCppEngine()
        mock_client = AsyncMock()
        mock_client.is_closed = True
        engine._client = mock_client
        engine._closed = True

        with pytest.raises(InferenceError) as exc_info:
            await engine._get_client()

        assert "closed" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_generate_with_stop_tokens(self, llama_engine):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [{"text": "Result", "finish_reason": "stop", "index": 0}],
                    "model": "test",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
                request=request,
            )

        llama_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=llama_engine._base_url,
        )
        request = InferenceRequest(prompt="test", stop=["\n", "###"])
        await llama_engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert body.get("stop") == ["\n", "###"]


class TestVLLMEngine:
    def test_init_default_values(self):
        engine = VLLMEngine(default_model="test-model")
        assert engine._base_url == "http://localhost:8002"
        assert engine._default_max_tokens == 2048
        assert engine._default_temperature == 0.05
        assert engine._require_model is True
        assert engine._default_model == "test-model"

    def test_init_default_model_empty_string(self):
        engine = VLLMEngine()
        assert engine._default_model == ""
        assert engine._require_model is True

    def test_init_custom_values(self):
        engine = VLLMEngine(
            base_url="http://gpu-cluster:8002",
            default_max_tokens=4096,
            default_temperature=0.02,
            require_model=False,
        )
        assert engine._base_url == "http://gpu-cluster:8002"
        assert engine._default_max_tokens == 4096
        assert engine._require_model is False

    def test_base_url_trailing_slash_stripped(self):
        engine = VLLMEngine(base_url="http://localhost:8002/")
        assert engine._base_url == "http://localhost:8002"

    @pytest.mark.asyncio
    async def test_generate_success(self, vllm_engine):
        mock_response = httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "TI-RADS 4a, 建议FNA"},
                        "finish_reason": "stop",
                        "index": 0,
                    }
                ],
                "model": "qwen2.5-7b-instruct",
                "usage": {"prompt_tokens": 80, "completion_tokens": 15, "total_tokens": 95},
            },
            request=httpx.Request("POST", "http://localhost:8002/v1/chat/completions"),
        )
        vllm_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=vllm_engine._base_url,
        )
        request = InferenceRequest(prompt="评估甲状腺右叶低回声结节", request_id="req_vllm_001")
        response = await vllm_engine.generate(request)

        assert response.text == "TI-RADS 4a, 建议FNA"
        assert response.model == "qwen2.5-7b-instruct"
        assert response.usage.prompt_tokens == 80
        assert response.usage.completion_tokens == 15
        assert response.finish_reason == "stop"
        assert response.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_generate_with_custom_messages(self, vllm_engine):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "诊断结果"}, "finish_reason": "stop", "index": 0}
                    ],
                    "model": "test",
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
                request=request,
            )

        vllm_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=vllm_engine._base_url,
        )
        request = InferenceRequest(
            prompt="test",
            extra={
                "messages": [
                    {"role": "system", "content": "You are a medical assistant."},
                    {"role": "user", "content": "Evaluate this nodule"},
                ]
            },
        )
        await vllm_engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert len(body["messages"]) == 2
        assert body["messages"][0]["role"] == "system"

    @pytest.mark.asyncio
    async def test_generate_server_error(self, vllm_engine):
        mock_response = httpx.Response(
            503,
            text="Service Unavailable",
            request=httpx.Request("POST", "http://localhost:8002/v1/chat/completions"),
        )
        vllm_engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=vllm_engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await vllm_engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert exc_info.value.retryable is True

    @pytest.mark.asyncio
    async def test_generate_empty_choices(self):
        mock_response = httpx.Response(
            200,
            json={"choices": [], "model": "test"},
            request=httpx.Request("POST", "http://localhost:8002/v1/chat/completions"),
        )
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert exc_info.value.retryable is True

        await engine.close()

    @pytest.mark.asyncio
    async def test_generate_timeout(self):
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.TimeoutException("timeout"))),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.TIMEOUT

        await engine.close()

    @pytest.mark.asyncio
    async def test_generate_connection_failed(self):
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.ConnectError("refused"))),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.CONNECTION_FAILED

        await engine.close()

    @pytest.mark.asyncio
    async def test_health_check_success(self):
        mock_response = httpx.Response(
            200,
            request=httpx.Request("GET", "http://localhost:8002/health"),
        )
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=engine._base_url,
        )
        result = await engine.health_check()
        assert result is True

        await engine.close()

    @pytest.mark.asyncio
    async def test_health_check_failure(self):
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.ConnectError("refused"))),
            base_url=engine._base_url,
        )
        result = await engine.health_check()
        assert result is False

        await engine.close()

    @pytest.mark.asyncio
    async def test_get_model_info_success(self):
        mock_response = httpx.Response(
            200,
            json={"data": [{"id": "qwen2.5-7b-instruct", "context_length": 32768, "vocab_size": 152064}]},
            request=httpx.Request("GET", "http://localhost:8002/v1/models"),
        )
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=engine._base_url,
        )
        info = await engine.get_model_info()
        assert info.model_name == "qwen2.5-7b-instruct"
        assert info.context_length == 32768
        assert info.vocab_size == 152064

        await engine.close()

    @pytest.mark.asyncio
    async def test_get_model_info_empty(self):
        mock_response = httpx.Response(
            200,
            json={"data": []},
            request=httpx.Request("GET", "http://localhost:8002/v1/models"),
        )
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(mock_response)),
            base_url=engine._base_url,
        )
        info = await engine.get_model_info()
        assert info.model_name == ""

        await engine.close()

    @pytest.mark.asyncio
    async def test_get_model_info_connection_error(self):
        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(exc=httpx.ConnectError("refused"))),
            base_url=engine._base_url,
        )
        info = await engine.get_model_info()
        assert info.model_name == ""

        await engine.close()

    @pytest.mark.asyncio
    async def test_close(self):
        engine = VLLMEngine(default_model="test-model")
        mock_client = AsyncMock()
        mock_client.is_closed = False

        def update_is_closed(*args, **kwargs):
            mock_client.is_closed = True

        mock_client.aclose.side_effect = update_is_closed
        engine._client = mock_client

        await engine.close()

        mock_client.aclose.assert_awaited_once()
        assert mock_client.is_closed is True
        assert engine._client is None
        assert engine._closed is True

    @pytest.mark.asyncio
    async def test_close_no_client(self):
        engine = VLLMEngine(default_model="test-model")
        await engine.close()
        assert engine._closed is True

    @pytest.mark.asyncio
    async def test_generate_with_stop_tokens(self):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "Result"}, "finish_reason": "stop", "index": 0}
                    ],
                    "model": "test",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
                request=request,
            )

        engine = VLLMEngine(default_model="test-model")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test", stop=["\n", "###"])
        await engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert body.get("stop") == ["\n", "###"]

        await engine.close()

    @pytest.mark.asyncio
    async def test_generate_includes_model_in_payload(self):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "Result"}, "finish_reason": "stop", "index": 0}
                    ],
                    "model": "qwen2.5-7b",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
                request=request,
            )

        engine = VLLMEngine(default_model="qwen2.5-7b")
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        await engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert body.get("model") == "qwen2.5-7b"

        await engine.close()

    @pytest.mark.asyncio
    async def test_generate_missing_model_raises_when_required(self):
        engine = VLLMEngine(default_model="", require_model=True)
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(_make_mock_handler(
                httpx.Response(200, json={"choices": [], "model": ""}, request=httpx.Request("POST", "http://test/"))
            )),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        with pytest.raises(InferenceError) as exc_info:
            await engine.generate(request)

        assert exc_info.value.error_code == InferenceErrorCode.GENERATION_FAILED
        assert "model" in str(exc_info.value).lower()

        await engine.close()

    @pytest.mark.asyncio
    async def test_generate_missing_model_warns_when_not_required(self):
        captured_request = None

        async def capture_handler(request: httpx.Request) -> httpx.Response:
            nonlocal captured_request
            captured_request = request
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {"message": {"role": "assistant", "content": "Result"}, "finish_reason": "stop", "index": 0}
                    ],
                    "model": "default",
                    "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
                },
                request=request,
            )

        engine = VLLMEngine(default_model="", require_model=False)
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(capture_handler),
            base_url=engine._base_url,
        )
        request = InferenceRequest(prompt="test")
        await engine.generate(request)

        body = json.loads(captured_request.content.decode())
        assert "model" not in body

        await engine.close()


class TestInferenceEngineProtocol:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            InferenceEngine()

    def test_llama_cpp_implements_protocol(self):
        engine = LlamaCppEngine()
        assert isinstance(engine, InferenceEngine)
        assert isinstance(engine, BaseHttpInferenceEngine)

    def test_vllm_implements_protocol(self):
        engine = VLLMEngine(default_model="test-model")
        assert isinstance(engine, InferenceEngine)
        assert isinstance(engine, BaseHttpInferenceEngine)

    def test_subclasses_must_implement_abstract_methods(self):
        with pytest.raises(TypeError, match="Can't instantiate abstract class"):
            class BrokenEngine(BaseHttpInferenceEngine):
                pass
            BrokenEngine(base_url="http://test", logger_name="broken")


class TestTokenUsage:
    def test_default_values(self):
        usage = TokenUsage()
        assert usage.prompt_tokens == 0
        assert usage.completion_tokens == 0
        assert usage.total_tokens == 0

    def test_custom_values(self):
        usage = TokenUsage(prompt_tokens=100, completion_tokens=50, total_tokens=150)
        assert usage.prompt_tokens == 100
        assert usage.total_tokens == 150


class TestModelInfo:
    def test_default_values(self):
        info = ModelInfo()
        assert info.model_name == ""
        assert info.context_length == 0

    def test_custom_values(self):
        info = ModelInfo(
            model_name="qwen2.5-7b",
            quantization="Q4_K_M",
            context_length=32768,
        )
        assert info.model_name == "qwen2.5-7b"
        assert info.quantization == "Q4_K_M"
        assert info.context_length == 32768


class TestModelInfoCacheTTL:
    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        mock_fresh_response = httpx.Response(
            200,
            json={"data": [{"id": "fresh-model"}]},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )

        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_fresh_response

        engine = LlamaCppEngine()
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(counting_handler),
            base_url=engine._base_url,
        )

        info1 = await engine.get_model_info()
        assert info1.model_name == "fresh-model"
        assert call_count == 1

        info2 = await engine.get_model_info()
        assert info2.model_name == "fresh-model"
        assert call_count == 1

        with patch.object(time, 'monotonic', return_value=time.monotonic() + 301):
            info3 = await engine.get_model_info()
            assert info3.model_name == "fresh-model"
            assert call_count == 2

        await engine.close()

    @pytest.mark.asyncio
    async def test_cache_not_expired_at_ttl_minus_one(self):
        mock_fresh_response = httpx.Response(
            200,
            json={"data": [{"id": "fresh-model"}]},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )

        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_fresh_response

        engine = LlamaCppEngine()
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(counting_handler),
            base_url=engine._base_url,
        )

        await engine.get_model_info()
        assert call_count == 1

        with patch.object(time, 'monotonic', return_value=time.monotonic() + 299):
            await engine.get_model_info()
            assert call_count == 1

        await engine.close()

    @pytest.mark.asyncio
    async def test_cache_expired_at_exact_ttl(self):
        mock_fresh_response = httpx.Response(
            200,
            json={"data": [{"id": "fresh-model"}]},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )

        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_fresh_response

        engine = LlamaCppEngine()
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(counting_handler),
            base_url=engine._base_url,
        )

        await engine.get_model_info()
        assert call_count == 1

        with patch.object(time, 'monotonic', return_value=time.monotonic() + 300):
            await engine.get_model_info()
            assert call_count == 2

        await engine.close()

    @pytest.mark.asyncio
    async def test_cache_used_within_ttl(self):
        mock_fresh_response = httpx.Response(
            200,
            json={"data": [{"id": "fresh-model"}]},
            request=httpx.Request("GET", "http://localhost:8001/v1/models"),
        )

        call_count = 0

        async def counting_handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return mock_fresh_response

        engine = LlamaCppEngine()
        engine._client = httpx.AsyncClient(
            transport=httpx.MockTransport(counting_handler),
            base_url=engine._base_url,
        )

        await engine.get_model_info()
        assert call_count == 1

        with patch.object(time, 'monotonic', return_value=time.monotonic() + 100):
            await engine.get_model_info()
            assert call_count == 1

        with patch.object(time, 'monotonic', return_value=time.monotonic() + 200):
            await engine.get_model_info()
            assert call_count == 1

        await engine.close()


class TestSettingsValidation:
    def test_compact_profile_forces_llama_cpp(self):
        from app.config.settings import Settings
        s = Settings(deployment_profile='compact', inference_engine='vllm')
        assert s.inference_engine == 'llama.cpp'

    def test_full_profile_forces_vllm(self):
        from app.config.settings import Settings
        s = Settings(deployment_profile='full', inference_engine='llama.cpp')
        assert s.inference_engine == 'vllm'

    def test_port_conflict_rejected(self):
        from app.config.settings import Settings
        with pytest.raises(ValueError, match="Port conflict"):
            Settings(api_port=8001, llama_cpp_port=8001)

    def test_effective_max_tokens_compact(self):
        from app.config.settings import Settings
        s = Settings(deployment_profile='compact')
        assert s.effective_max_tokens == 1024

    def test_effective_max_tokens_full(self):
        from app.config.settings import Settings
        s = Settings(deployment_profile='full')
        assert s.effective_max_tokens == 2048

    def test_effective_max_tokens_has_docstring(self):
        from app.config.settings import Settings
        assert Settings.effective_max_tokens.fget.__doc__ is not None
        assert "compact" in Settings.effective_max_tokens.fget.__doc__.lower()

    def test_invalid_deployment_profile_rejected(self):
        from app.config.settings import Settings
        with pytest.raises(ValidationError, match="deployment_profile must be one of"):
            Settings(deployment_profile='compct')

    def test_alias_migration_inference_max_tokens(self):
        from app.config.settings import Settings
        s = Settings(**{'inference_max_tokens': 512})
        assert s.max_tokens == 512

    def test_alias_migration_inference_temperature(self):
        from app.config.settings import Settings
        s = Settings(**{'inference_temperature': 0.5})
        assert s.temperature == 0.5

    def test_alias_migration_inference_top_p(self):
        from app.config.settings import Settings
        s = Settings(**{'inference_top_p': 0.8})
        assert s.top_p == 0.8

    def test_dicom_scp_port_in_conflict_detection(self):
        from app.config.settings import Settings
        with pytest.raises(ValueError, match="Port conflict"):
            Settings(dicom_scp_port=8001, llama_cpp_port=8001)

    def test_llama_cpp_port_default_8001(self):
        from app.config.settings import Settings
        s = Settings()
        assert s.llama_cpp_port == 8001
