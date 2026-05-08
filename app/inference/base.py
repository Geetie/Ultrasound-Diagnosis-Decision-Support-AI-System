import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
import structlog

from app.inference.protocols import (
    InferenceEngine,
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
)

_MODEL_INFO_TTL_SECONDS = 300.0


class BaseHttpInferenceEngine(InferenceEngine, ABC):
    def __init__(
        self,
        base_url: str,
        logger_name: str,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        default_max_tokens: int = 2048,
        default_temperature: float = 0.1,
        default_top_p: float = 0.95,
    ):
        self._base_url = base_url.rstrip("/")
        self._logger_name = logger_name
        self._timeout_seconds = timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._health_check_timeout_seconds = health_check_timeout_seconds
        self._default_max_tokens = default_max_tokens
        self._default_temperature = default_temperature
        self._default_top_p = default_top_p
        self._client: httpx.AsyncClient | None = None
        self._model_info_cache: ModelInfo | None = None
        self._model_info_cached_at: float = 0.0
        self._closed = False
        self._log = structlog.get_logger(logger_name)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="engine has been closed",
                detail=f"engine={self._logger_name}",
                retryable=False,
            )
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=self._connect_timeout_seconds,
                    read=self._timeout_seconds,
                    write=self._connect_timeout_seconds,
                    pool=self._connect_timeout_seconds,
                ),
            )
        return self._client

    @abstractmethod
    def _build_payload(self, request: InferenceRequest) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def _parse_response(self, data: dict[str, Any], request: InferenceRequest) -> InferenceResponse:
        raise NotImplementedError

    @property
    @abstractmethod
    def _generate_endpoint(self) -> str:
        raise NotImplementedError

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        start_time = time.monotonic()
        client = await self._get_client()
        payload = self._build_payload(request)

        try:
            response = await client.post(self._generate_endpoint, json=payload)
            latency_ms = (time.monotonic() - start_time) * 1000

            if response.status_code != 200:
                error_body = response.text[:500]
                self._log.error(
                    "generation_failed",
                    engine=self._logger_name,
                    status_code=response.status_code,
                    error=error_body,
                    request_id=request.request_id,
                )
                raise InferenceError(
                    error_code=InferenceErrorCode.GENERATION_FAILED,
                    message=f"returned HTTP {response.status_code}",
                    detail=error_body,
                    retryable=response.status_code >= 500,
                    retry_strategy="retry_after_5s" if response.status_code >= 500 else "no_retry",
                )

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                self._log.error(
                    "json_decode_failed",
                    engine=self._logger_name,
                    request_id=request.request_id,
                    error=str(exc),
                )
                raise InferenceError(
                    error_code=InferenceErrorCode.GENERATION_FAILED,
                    message="returned invalid JSON",
                    detail=str(exc),
                    retryable=True,
                    retry_strategy="retry_after_5s",
                ) from exc

            result = self._parse_response(data, request)
            result.latency_ms = round(latency_ms, 2)

            self._log.info(
                "generation_completed",
                engine=self._logger_name,
                request_id=request.request_id,
                session_id=request.session_id,
                latency_ms=result.latency_ms,
                prompt_tokens=result.usage.prompt_tokens,
                completion_tokens=result.usage.completion_tokens,
                finish_reason=result.finish_reason,
            )

            return result

        except InferenceError:
            raise
        except httpx.TimeoutException as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._log.error(
                "timeout",
                engine=self._logger_name,
                request_id=request.request_id,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )
            raise InferenceError(
                error_code=InferenceErrorCode.TIMEOUT,
                message="inference request timed out",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            ) from exc
        except httpx.ConnectError as exc:
            self._log.error(
                "connection_failed",
                engine=self._logger_name,
                base_url=self._base_url,
                error=str(exc),
            )
            raise InferenceError(
                error_code=InferenceErrorCode.CONNECTION_FAILED,
                message="cannot connect to inference server",
                detail=f"Connection to {self._base_url} failed: {exc}",
                retryable=True,
                retry_strategy="retry_after_5s",
            ) from exc
        except Exception as exc:
            self._log.error(
                "unexpected_error",
                engine=self._logger_name,
                request_id=request.request_id,
                error=str(exc),
            )
            raise InferenceError(
                error_code=InferenceErrorCode.GENERATION_FAILED,
                message="unexpected error during inference",
                detail=str(exc),
                retryable=False,
            ) from exc

    async def health_check(self) -> bool:
        client = await self._get_client()
        try:
            response = await client.get(
                "/health",
                timeout=httpx.Timeout(
                    connect=self._connect_timeout_seconds,
                    read=self._health_check_timeout_seconds,
                    write=self._health_check_timeout_seconds,
                    pool=self._health_check_timeout_seconds,
                ),
            )
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.ConnectError):
            self._log.warning("health_check_failed", engine=self._logger_name, base_url=self._base_url)
            return False

    async def get_model_info(self) -> ModelInfo:
        now = time.monotonic()
        if self._model_info_cache is not None and (now - self._model_info_cached_at) < _MODEL_INFO_TTL_SECONDS:
            return self._model_info_cache

        client = await self._get_client()
        try:
            response = await client.get("/v1/models")
            if response.status_code != 200:
                self._log.warning(
                    "model_info_unavailable",
                    engine=self._logger_name,
                    status_code=response.status_code,
                )
                return ModelInfo()

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                self._log.warning(
                    "model_info_json_decode_failed",
                    engine=self._logger_name,
                    error=str(exc),
                )
                return ModelInfo()

            models = data.get("data", [])
            if not models:
                return ModelInfo()

            model_data = models[0]
            self._model_info_cache = ModelInfo(
                model_name=model_data.get("id", ""),
                model_version="",
                quantization="",
                context_length=model_data.get("context_length", 0) or 0,
                vocab_size=model_data.get("vocab_size", 0) or 0,
            )
            self._model_info_cached_at = time.monotonic()
            return self._model_info_cache

        except InferenceError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._log.warning("model_info_fetch_failed", engine=self._logger_name, error=str(exc))
            return ModelInfo()

    async def close(self) -> None:
        self._closed = True
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._log.info("client_closed", engine=self._logger_name)
