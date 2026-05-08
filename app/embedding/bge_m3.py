from typing import Any

from app.embedding.base import BaseHttpEmbeddingService
from app.embedding.protocols import (
    EmbeddingError,
    EmbeddingErrorCode,
    EmbeddingModality,
    EmbeddingModelInfo,
    EmbeddingModelType,
    EmbeddingResponse,
    ImageEmbeddingRequest,
    MultimodalEmbeddingRequest,
    SparseEmbeddingResponse,
    TextEmbeddingRequest,
)

BGE_M3_DIM = 1024


class BgeM3MedicalService(BaseHttpEmbeddingService):
    def __init__(
        self,
        base_url: str = "http://localhost:8004",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        dimension: int = BGE_M3_DIM,
        max_connections: int = 20,
        max_keepalive_connections: int = 5,
    ):
        super().__init__(
            base_url=base_url,
            logger_name="app.embedding.bge_m3",
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            dimension=dimension,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

    def _build_fallback_model_info(self) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(
            model_type=EmbeddingModelType.BGE_M3_MEDICAL,
            dimension=self._dimension,
            supports_image=False,
        )

    def _build_model_info(self, data: dict[str, Any]) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(
            model_name=data.get("model_name", "bge-m3-medical"),
            model_type=EmbeddingModelType.BGE_M3_MEDICAL,
            dimension=data.get("dimension", self._dimension),
            max_input_length=data.get("max_input_length", 8192),
            supports_image=False,
            version=data.get("version", ""),
        )

    async def embed_text(self, request: TextEmbeddingRequest) -> EmbeddingResponse:
        start_time = self._monotonic_now()
        payload: dict[str, Any] = {
            "texts": request.texts,
            "return_dense": True,
            "return_sparse": False,
        }

        data = await self._do_post_json(
            "/v1/embeddings",
            payload,
            request_id=request.request_id,
            session_id=request.session_id,
        )
        latency_ms = (self._monotonic_now() - start_time) * 1000
        embeddings = self._parse_embeddings(data, expected_count=len(request.texts))

        self._log.info(
            "text_encoding_completed",
            request_id=request.request_id,
            session_id=request.session_id,
            num_texts=len(request.texts),
            latency_ms=round(latency_ms, 2),
        )

        return EmbeddingResponse(
            embeddings=embeddings,
            model=data.get("model", "bge-m3-medical"),
            dimension=self._dimension,
            modality=EmbeddingModality.TEXT,
            request_id=request.request_id,
            latency_ms=round(latency_ms, 2),
            token_count=data.get("token_count", 0),
        )

    async def embed_text_sparse(self, request: TextEmbeddingRequest) -> SparseEmbeddingResponse:
        start_time = self._monotonic_now()
        payload: dict[str, Any] = {
            "texts": request.texts,
            "return_dense": True,
            "return_sparse": True,
        }

        data = await self._do_post_json(
            "/v1/embeddings",
            payload,
            request_id=request.request_id,
            session_id=request.session_id,
        )
        latency_ms = (self._monotonic_now() - start_time) * 1000
        dense_embeddings = self._parse_embeddings(data, expected_count=len(request.texts))
        sparse_weights = self._parse_sparse_weights(data)

        if len(dense_embeddings) != len(sparse_weights):
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.ENCODING_FAILED,
                message=f"Dense/Sparse count mismatch: {len(dense_embeddings)} dense vs {len(sparse_weights)} sparse",
                retryable=False,
                retry_strategy="no_retry",
            )

        self._log.info(
            "sparse_encoding_completed",
            request_id=request.request_id,
            session_id=request.session_id,
            num_texts=len(request.texts),
            latency_ms=round(latency_ms, 2),
        )

        return SparseEmbeddingResponse(
            dense_embeddings=dense_embeddings,
            sparse_weights=sparse_weights,
            model=data.get("model", "bge-m3-medical"),
            dimension=self._dimension,
            request_id=request.request_id,
            latency_ms=round(latency_ms, 2),
            token_count=data.get("token_count", 0),
        )

    async def embed_image(self, request: ImageEmbeddingRequest) -> EmbeddingResponse:
        raise EmbeddingError(
            error_code=EmbeddingErrorCode.IMAGE_NOT_SUPPORTED,
            message="bge-m3-medical does not support image embedding; use Ultrasound-CLIP for multimodal encoding",
            retryable=False,
            retry_strategy="no_retry",
        )

    async def embed_multimodal(self, request: MultimodalEmbeddingRequest) -> EmbeddingResponse:
        raise EmbeddingError(
            error_code=EmbeddingErrorCode.IMAGE_NOT_SUPPORTED,
            message="bge-m3-medical does not support multimodal embedding; use Ultrasound-CLIP for multimodal encoding",
            retryable=False,
            retry_strategy="no_retry",
        )
