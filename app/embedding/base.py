import json
import time
from abc import ABC, abstractmethod
from typing import Any

import httpx
import numpy as np

from app.common.http_client_base import BaseHttpClient
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


class BaseHttpEmbeddingService(BaseHttpClient, EmbeddingService, ABC):
    """HTTP嵌入服务基类。

    继承自BaseHttpClient，提供HTTP客户端的通用功能。
    子类需要实现特定的业务逻辑。

    Args:
        base_url: 服务的基础URL
        logger_name: 日志记录器名称
        timeout_seconds: 请求超时时间（默认30秒）
        connect_timeout_seconds: 连接超时时间（默认10秒）
        health_check_timeout_seconds: 健康检查超时时间（默认5秒）
        dimension: 嵌入向量维度（默认768）
        max_connections: 连接池最大连接数（默认20）
        max_keepalive_connections: 连接池最大保持连接数（默认5）
    """

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
        super().__init__(
            base_url=base_url,
            logger_name=logger_name,
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
            keepalive_expiry=300.0,
        )
        self._dimension = dimension
        self._model_info_cache: EmbeddingModelInfo | None = None
        self._model_info_cached_at: float = 0.0

    def _create_closed_error(self) -> Exception:
        return EmbeddingError(
            error_code=EmbeddingErrorCode.ENCODING_FAILED,
            message="embedding service has been closed",
            detail=f"service={self._logger_name}",
            retryable=False,
        )

    def _create_timeout_error(self, detail: str) -> Exception:
        return EmbeddingError(
            error_code=EmbeddingErrorCode.TIMEOUT,
            message=f"{self._logger_name} request timed out",
            detail=detail,
            retryable=True,
            retry_strategy="retry_after_5s",
        )

    def _create_connection_error(self, detail: str) -> Exception:
        return EmbeddingError(
            error_code=EmbeddingErrorCode.CONNECTION_FAILED,
            message=f"Cannot connect to {self._logger_name} service",
            detail=detail,
            retryable=True,
            retry_strategy="retry_after_5s",
        )

    def _create_http_error(
        self,
        status_code: int,
        detail: str,
        retryable: bool,
    ) -> Exception:
        return EmbeddingError(
            error_code=EmbeddingErrorCode.ENCODING_FAILED,
            message=f"{self._logger_name} returned HTTP {status_code}",
            detail=detail,
            retryable=retryable,
            retry_strategy="retry_after_5s" if retryable else "no_retry",
        )

    def _create_json_decode_error(self, detail: str) -> Exception:
        return EmbeddingError(
            error_code=EmbeddingErrorCode.ENCODING_FAILED,
            message="returned invalid JSON",
            detail=detail,
            retryable=True,
            retry_strategy="retry_after_5s",
        )

    def _create_unexpected_error(self, detail: str) -> Exception:
        return EmbeddingError(
            error_code=EmbeddingErrorCode.ENCODING_FAILED,
            message=f"Unexpected error during {self._logger_name} request",
            detail=detail,
            retryable=False,
            retry_strategy="no_retry",
        )

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

    # ------------------------------------------------------------------
    # P1-EMB-03: retry loop for transient failures.
    # ------------------------------------------------------------------
    async def _run_with_retry(self, op_name: str, coro_factory):
        """Execute ``coro_factory()`` with bounded exponential backoff.

        Only ``EmbeddingError`` with ``retryable=True`` is retried; all
        other exceptions propagate immediately. Honours
        ``settings.embedding_retry_max_attempts`` and
        ``settings.embedding_retry_backoff_base`` so production can
        tune the budget without code changes.
        """
        from app.config.settings import settings

        max_attempts = max(1, int(getattr(settings, "embedding_retry_max_attempts", 3)))
        backoff_base = max(0.0, float(getattr(settings, "embedding_retry_backoff_base", 1.0)))

        last_exc: Exception | None = None
        for attempt in range(1, max_attempts + 1):
            try:
                return await coro_factory()
            except EmbeddingError as exc:
                last_exc = exc
                if not exc.retryable or attempt >= max_attempts:
                    raise
                sleep_for = backoff_base * (2 ** (attempt - 1))
                self._log.warning(
                    "embedding_request_retrying",
                    op=op_name,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    sleep_seconds=sleep_for,
                    error_code=exc.error_code.value,
                    error=str(exc),
                )
                if sleep_for > 0:
                    import asyncio
                    await asyncio.sleep(sleep_for)
        # Defensive: unreachable, but keeps type checkers happy.
        assert last_exc is not None
        raise last_exc
