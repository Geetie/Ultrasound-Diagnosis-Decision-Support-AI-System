from typing import Any

from app.inference.base import BaseHttpInferenceEngine
from app.inference.protocols import (
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    TokenUsage,
)


class VLLMEngine(BaseHttpInferenceEngine):
    def __init__(
        self,
        base_url: str = "http://localhost:8002",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        default_max_tokens: int = 2048,
        default_temperature: float = 0.05,
        default_top_p: float = 0.95,
        default_model: str = "",
        require_model: bool = True,
    ):
        super().__init__(
            base_url=base_url,
            logger_name="app.inference.vllm",
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            default_max_tokens=default_max_tokens,
            default_temperature=default_temperature,
            default_top_p=default_top_p,
        )
        self._default_model = default_model
        self._require_model = require_model

    @property
    def _generate_endpoint(self) -> str:
        return "/v1/chat/completions"

    def _build_messages(self, request: InferenceRequest) -> list[dict[str, str]]:
        messages = request.extra.get("messages")
        if messages:
            if request.prompt.strip():
                self._log.warning(
                    "prompt_ignored_when_messages_provided",
                    engine=self._logger_name,
                    request_id=request.request_id,
                )
            return messages
        return [{"role": "user", "content": request.prompt}]

    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        messages = self._build_messages(request)
        payload: dict[str, Any] = {
            "messages": messages,
            "max_tokens": request.max_tokens if request.max_tokens is not None else self._default_max_tokens,
            "temperature": request.temperature if request.temperature is not None else self._default_temperature,
            "top_p": request.top_p if request.top_p is not None else self._default_top_p,
        }
        if request.stop:
            payload["stop"] = request.stop

        model_name = request.extra.get("model") or self._default_model
        if model_name:
            payload["model"] = model_name
        elif self._require_model:
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="vLLM requires a model name but none was provided",
                detail="Set default_model or pass model in request.extra",
                retryable=False,
            )
        else:
            self._log.warning(
                "model_field_missing",
                engine=self._logger_name,
                request_id=request.request_id,
            )

        return payload

    def _parse_response(self, data: dict[str, Any], request: InferenceRequest) -> InferenceResponse:
        choices = data.get("choices", [])
        if not choices:
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="vLLM returned empty choices",
                retryable=True,
                retry_strategy="retry_after_5s",
            )

        choice = choices[0]
        message = choice.get("message", {})
        text = message.get("content", "")
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
