import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
import numpy as np
import structlog

from app.embedding.protocols import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingModelInfo,
    EmbeddingService,
)

_MODEL_INFO_TTL_SECONDS = 300.0

_MIME_TYPE_MAP: dict[str, str] = {
    "dicom": "application/dicom",
    "jpeg": "image/jpeg",
    "jpg": "image/jpeg",
    "png": "image/png",
    "bmp": "image/bmp",
    "tiff": "image/tiff",
}


def get_mime_type(image_format: str) -> str:
    return _MIME_TYPE_MAP.get(image_format.lower(), f"application/{image_format}")


class BaseHttpEmbeddingService(EmbeddingService, ABC):
    def __init__(
        self,
        base_url: str,
        logger_name: str,
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        dimension: int = 768,
        max_connections: int = 20,
        max_keepalive_connections: int = 5,
    ):
        self._base_url = base_url.rstrip("/")
        self._logger_name = logger_name
        self._timeout_seconds = timeout_seconds
        self._connect_timeout_seconds = connect_timeout_seconds
        self._health_check_timeout_seconds = health_check_timeout_seconds
        self._dimension = dimension
        self._max_connections = max_connections
        self._max_keepalive_connections = max_keepalive_connections
        self._client: httpx.AsyncClient | None = None
        self._model_info_cache: EmbeddingModelInfo | None = None
        self._model_info_cached_at: float = 0.0
        self._closed = False
        self._log = structlog.get_logger(logger_name)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.ENCODING_FAILED,
                message="embedding service has been closed",
                detail=f"service={self._logger_name}",
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
                limits=httpx.Limits(
                    max_connections=self._max_connections,
                    max_keepalive_connections=self._max_keepalive_connections,
                ),
            )
        return self._client

    def _parse_embeddings(self, data: dict[str, Any], expected_count: int | None = None) -> list[np.ndarray]:
        raw = data.get("dense_embeddings", data.get("embeddings", []))
        result = []
        for vec in raw:
            arr = np.array(vec, dtype=np.float32)
            if arr.shape[0] != self._dimension:
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.DIMENSION_MISMATCH,
                    message=f"Expected dimension {self._dimension}, got {arr.shape[0]}",
                    retryable=False,
                    retry_strategy="no_retry",
                )
            result.append(arr)
        if expected_count is not None and len(result) != expected_count:
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.ENCODING_FAILED,
                message=f"Expected {expected_count} embeddings, got {len(result)}",
                retryable=False,
                retry_strategy="no_retry",
            )
        return result

    @staticmethod
    def _monotonic_now() -> float:
        return time.monotonic()

    def _parse_sparse_weights(self, data: dict[str, Any]) -> list[dict[int, float]]:
        raw = data.get("sparse_embeddings", [])
        result = []
        for sparse_vec in raw:
            if not isinstance(sparse_vec, list):
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.ENCODING_FAILED,
                    message="sparse_embeddings entry must be a list",
                    retryable=False,
                    retry_strategy="no_retry",
                )
            weights: dict[int, float] = {}
            for entry in sparse_vec:
                if not isinstance(entry, dict):
                    continue
                idx = entry.get("index", entry.get("token_id"))
                weight = entry.get("weight", entry.get("value"))
                if idx is None or weight is None:
                    continue
                weights[int(idx)] = float(weight)
            result.append(weights)
        return result

    async def _do_post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        client = await self._get_client()

        try:
            response = await client.post(endpoint, json=payload)
            latency_ms = (time.monotonic() - start_time) * 1000

            if response.status_code != 200:
                error_body = response.text[:500]
                self._log.error(
                    "embedding_request_failed",
                    engine=self._logger_name,
                    endpoint=endpoint,
                    status_code=response.status_code,
                    error=error_body,
                    request_id=request_id,
                )
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.ENCODING_FAILED,
                    message=f"{self._logger_name} returned HTTP {response.status_code}",
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
                    request_id=request_id,
                    error=str(exc),
                )
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.ENCODING_FAILED,
                    message="returned invalid JSON",
                    detail=str(exc),
                    retryable=True,
                    retry_strategy="retry_after_5s",
                ) from exc

            return data

        except EmbeddingError:
            raise
        except httpx.TimeoutException as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._log.error(
                "timeout",
                engine=self._logger_name,
                endpoint=endpoint,
                request_id=request_id,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.TIMEOUT,
                message=f"{self._logger_name} request timed out",
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
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.CONNECTION_FAILED,
                message=f"Cannot connect to {self._logger_name} service",
                detail=f"Connection to {self._base_url} failed: {exc}",
                retryable=True,
                retry_strategy="retry_after_5s",
            ) from exc
        except Exception as exc:
            self._log.error(
                "unexpected_error",
                engine=self._logger_name,
                request_id=request_id,
                error=str(exc),
            )
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.ENCODING_FAILED,
                message=f"Unexpected error during {self._logger_name} request",
                detail=str(exc),
                retryable=False,
                retry_strategy="no_retry",
            ) from exc

    async def _do_post_multipart(
        self,
        endpoint: str,
        files: list[tuple[str, tuple[str, bytes, str]]],
        data: dict[str, Any],
        request_id: str | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        start_time = time.monotonic()
        client = await self._get_client()

        try:
            response = await client.post(endpoint, files=files, data=data)
            latency_ms = (time.monotonic() - start_time) * 1000

            if response.status_code != 200:
                error_body = response.text[:500]
                self._log.error(
                    "embedding_multipart_failed",
                    engine=self._logger_name,
                    endpoint=endpoint,
                    status_code=response.status_code,
                    error=error_body,
                    request_id=request_id,
                    session_id=session_id,
                )
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.ENCODING_FAILED,
                    message=f"{self._logger_name} returned HTTP {response.status_code}",
                    detail=error_body,
                    retryable=response.status_code >= 500,
                    retry_strategy="retry_after_5s" if response.status_code >= 500 else "no_retry",
                )

            try:
                return response.json()
            except json.JSONDecodeError as exc:
                self._log.error(
                    "json_decode_failed",
                    engine=self._logger_name,
                    request_id=request_id,
                    session_id=session_id,
                    error=str(exc),
                )
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.ENCODING_FAILED,
                    message="returned invalid JSON",
                    detail=str(exc),
                    retryable=True,
                    retry_strategy="retry_after_5s",
                ) from exc

        except EmbeddingError:
            raise
        except httpx.TimeoutException as exc:
            latency_ms = (time.monotonic() - start_time) * 1000
            self._log.error(
                "timeout",
                engine=self._logger_name,
                endpoint=endpoint,
                request_id=request_id,
                session_id=session_id,
                latency_ms=round(latency_ms, 2),
                error=str(exc),
            )
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.TIMEOUT,
                message=f"{self._logger_name} request timed out",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            ) from exc
        except httpx.ConnectError as exc:
            self._log.error(
                "connection_failed",
                engine=self._logger_name,
                base_url=self._base_url,
                request_id=request_id,
                session_id=session_id,
                error=str(exc),
            )
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.CONNECTION_FAILED,
                message=f"Cannot connect to {self._logger_name} service",
                detail=f"Connection to {self._base_url} failed: {exc}",
                retryable=True,
                retry_strategy="retry_after_5s",
            ) from exc
        except Exception as exc:
            self._log.error(
                "unexpected_error",
                engine=self._logger_name,
                request_id=request_id,
                error=str(exc),
            )
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.ENCODING_FAILED,
                message=f"Unexpected error during {self._logger_name} request",
                detail=str(exc),
                retryable=False,
                retry_strategy="no_retry",
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

    async def get_model_info(self) -> EmbeddingModelInfo:
        now = time.monotonic()
        if self._model_info_cache is not None and (now - self._model_info_cached_at) < _MODEL_INFO_TTL_SECONDS:
            return self._model_info_cache

        client = await self._get_client()
        try:
            response = await client.get("/v1/model/info")
            if response.status_code != 200:
                self._log.warning(
                    "model_info_unavailable",
                    engine=self._logger_name,
                    status_code=response.status_code,
                )
                return self._build_fallback_model_info()

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                self._log.error(
                    "model_info_json_decode_failed",
                    engine=self._logger_name,
                    error=str(exc),
                )
                raise EmbeddingError(
                    error_code=EmbeddingErrorCode.ENCODING_FAILED,
                    message="model info returned invalid JSON",
                    detail=str(exc),
                    retryable=True,
                    retry_strategy="retry_after_5s",
                ) from exc

            self._model_info_cache = self._build_model_info(data)
            self._model_info_cached_at = time.monotonic()
            return self._model_info_cache

        except EmbeddingError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            self._log.warning("model_info_fetch_failed", engine=self._logger_name, error=str(exc))
            return self._build_fallback_model_info()

    @abstractmethod
    def _build_fallback_model_info(self) -> EmbeddingModelInfo:
        raise NotImplementedError

    @abstractmethod
    def _build_model_info(self, data: dict[str, Any]) -> EmbeddingModelInfo:
        raise NotImplementedError

    async def close(self) -> None:
        self._closed = True
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            self._log.info("client_closed", engine=self._logger_name)
