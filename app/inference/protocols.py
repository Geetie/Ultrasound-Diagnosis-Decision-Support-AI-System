from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class InferenceErrorCode(str, Enum):
    TIMEOUT = "E2002_TIMEOUT"
    CONNECTION_FAILED = "E2002_CONNECTION_FAILED"
    GENERATION_FAILED = "E2002_GENERATION_FAILED"

    @classmethod
    def _missing_(cls, value: object) -> "InferenceErrorCode | None":
        _DEPRECATED_MAP: dict[str, InferenceErrorCode] = {
            "E2002_MODEL_LOAD_FAILED": cls.GENERATION_FAILED,
            "E2002_INVALID_REQUEST": cls.GENERATION_FAILED,
            "E2002_HEALTH_CHECK_FAILED": cls.GENERATION_FAILED,
        }
        return _DEPRECATED_MAP.get(str(value))


class _DeprecatedErrorCode:
    MODEL_LOAD_FAILED = "E2002_MODEL_LOAD_FAILED"
    INVALID_REQUEST = "E2002_INVALID_REQUEST"
    HEALTH_CHECK_FAILED = "E2002_HEALTH_CHECK_FAILED"


class InferenceError(Exception):
    def __init__(
        self,
        error_code: InferenceErrorCode,
        message: str,
        detail: str | None = None,
        retryable: bool = False,
        retry_strategy: str = "no_retry",
    ):
        self.error_code = error_code
        self.message = message
        self.detail = detail
        self.retryable = retryable
        self.retry_strategy = retry_strategy
        super().__init__(f"[{error_code.value}] {message}")


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelInfo(BaseModel):
    model_name: str = ""
    model_version: str = ""
    quantization: str = ""
    context_length: int = 0
    vocab_size: int = 0


class InferenceRequest(BaseModel):
    prompt: str
    max_tokens: int | None = None
    temperature: float | None = None
    top_p: float | None = None
    stop: list[str] | None = None
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("prompt")
    @classmethod
    def prompt_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("prompt must not be empty or whitespace-only")
        return v

    @field_validator("max_tokens")
    @classmethod
    def max_tokens_must_be_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("max_tokens must be a positive integer")
        return v

    @field_validator("temperature")
    @classmethod
    def temperature_must_be_valid(cls, v: float | None) -> float | None:
        if v is not None and (v < 0.0 or v > 2.0):
            raise ValueError("temperature must be between 0.0 and 2.0")
        return v

    @field_validator("top_p")
    @classmethod
    def top_p_must_be_valid(cls, v: float | None) -> float | None:
        if v is not None and (v < 0.0 or v > 1.0):
            raise ValueError("top_p must be between 0.0 and 1.0")
        return v

    @field_validator("stop")
    @classmethod
    def stop_must_not_be_empty_list(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and len(v) == 0:
            raise ValueError("stop must be None or a non-empty list")
        return v


class InferenceResponse(BaseModel):
    text: str
    model: str = ""
    usage: TokenUsage = TokenUsage()
    finish_reason: str = ""
    request_id: str | None = None
    latency_ms: float = 0.0


class InferenceEngine(ABC):
    @abstractmethod
    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_model_info(self) -> ModelInfo:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
