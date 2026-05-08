from typing import Any

from app.inference.base import BaseHttpInferenceEngine
from app.inference.protocols import (
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    TokenUsage,
)


class LlamaCppEngine(BaseHttpInferenceEngine):
    def __init__(
        self,
        base_url: str = "http://localhost:8001",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        default_max_tokens: int = 1024,
        default_temperature: float = 0.1,
        default_top_p: float = 0.95,
    ):
        super().__init__(
            base_url=base_url,
            logger_name="app.inference.llama_cpp",
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            default_max_tokens=default_max_tokens,
            default_temperature=default_temperature,
            default_top_p=default_top_p,
        )

    @property
    def _generate_endpoint(self) -> str:
        return "/v1/completions"

    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt": request.prompt,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self._default_max_tokens,
            "temperature": request.temperature if request.temperature is not None else self._default_temperature,
            "top_p": request.top_p if request.top_p is not None else self._default_top_p,
        }
        if request.stop:
            payload["stop"] = request.stop
        return payload

    def _parse_response(self, data: dict[str, Any], request: InferenceRequest) -> InferenceResponse:
        choices = data.get("choices", [])
        if not choices:
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="llama.cpp returned empty choices",
                retryable=True,
                retry_strategy="retry_after_5s",
            )

        choice = choices[0]
        text = choice.get("text", "")
        finish_reason = choice.get("finish_reason", "stop")

        usage_data = data.get("usage", {})
        usage = TokenUsage(
            prompt_tokens=usage_data.get("prompt_tokens", 0),
            completion_tokens=usage_data.get("completion_tokens", 0),
            total_tokens=usage_data.get("total_tokens", 0),
        )

        return InferenceResponse(
            text=text,
            model=data.get("model", ""),
            usage=usage,
            finish_reason=finish_reason,
            request_id=request.request_id,
        )
