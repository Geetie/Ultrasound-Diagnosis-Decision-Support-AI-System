import json
from typing import Any

from app.embedding.base import BaseHttpEmbeddingService, get_mime_type
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

ULTRASOUND_CLIP_DIM = 768


class UltrasoundClipService(BaseHttpEmbeddingService):
    def __init__(
        self,
        base_url: str = "http://localhost:8003",
        timeout_seconds: float = 30.0,
        connect_timeout_seconds: float = 10.0,
        health_check_timeout_seconds: float = 5.0,
        dimension: int = ULTRASOUND_CLIP_DIM,
        max_connections: int = 20,
        max_keepalive_connections: int = 5,
    ):
        super().__init__(
            base_url=base_url,
            logger_name="app.embedding.ultrasound_clip",
            timeout_seconds=timeout_seconds,
            connect_timeout_seconds=connect_timeout_seconds,
            health_check_timeout_seconds=health_check_timeout_seconds,
            dimension=dimension,
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )

    def _build_fallback_model_info(self) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(
            model_type=EmbeddingModelType.ULTRASOUND_CLIP,
            dimension=self._dimension,
            supports_image=True,
        )

    def _build_model_info(self, data: dict[str, Any]) -> EmbeddingModelInfo:
        return EmbeddingModelInfo(
            model_name=data.get("model_name", "ultrasound-clip"),
            model_type=EmbeddingModelType.ULTRASOUND_CLIP,
            dimension=data.get("dimension", self._dimension),
            max_input_length=data.get("max_input_length", 512),
            supports_image=True,
            version=data.get("version", ""),
        )

    async def embed_text(self, request: TextEmbeddingRequest) -> EmbeddingResponse:
        start_time = self._monotonic_now()
        payload: dict[str, Any] = {
            "texts": request.texts,
            "modality": "text",
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
            model=data.get("model", "ultrasound-clip"),
            dimension=self._dimension,
            modality=EmbeddingModality.TEXT,
            request_id=request.request_id,
            latency_ms=round(latency_ms, 2),
            token_count=data.get("token_count", 0),
        )

    async def embed_text_sparse(self, request: TextEmbeddingRequest) -> SparseEmbeddingResponse:
        raise EmbeddingError(
            error_code=EmbeddingErrorCode.SPARSE_NOT_SUPPORTED,
            message="Ultrasound-CLIP does not produce sparse embeddings; use bge-m3-medical for sparse retrieval",
            retryable=False,
            retry_strategy="no_retry",
        )

    async def embed_image(self, request: ImageEmbeddingRequest) -> EmbeddingResponse:
        start_time = self._monotonic_now()
        mime_type = get_mime_type(request.image_format)
        files = [("image", ("image", request.image_data, mime_type))]
        form_data: dict[str, Any] = {"modality": "image"}

        data = await self._do_post_multipart(
            "/v1/embeddings/image",
            files=files,
            data=form_data,
            request_id=request.request_id,
            session_id=request.session_id,
        )
        latency_ms = (self._monotonic_now() - start_time) * 1000
        embeddings = self._parse_embeddings(data, expected_count=1)

        self._log.info(
            "image_encoding_completed",
            request_id=request.request_id,
            session_id=request.session_id,
            image_format=request.image_format,
            latency_ms=round(latency_ms, 2),
        )

        return EmbeddingResponse(
            embeddings=embeddings,
            model=data.get("model", "ultrasound-clip"),
            dimension=self._dimension,
            modality=EmbeddingModality.IMAGE,
            request_id=request.request_id,
            latency_ms=round(latency_ms, 2),
        )

    async def embed_multimodal(self, request: MultimodalEmbeddingRequest) -> EmbeddingResponse:
        if not request.texts and not request.images:
            raise EmbeddingError(
                error_code=EmbeddingErrorCode.INVALID_INPUT,
                message="At least one of texts or images must be provided",
                retryable=False,
                retry_strategy="no_retry",
            )

        start_time = self._monotonic_now()
        files = []
        for i, (img_data, img_fmt) in enumerate(
            zip(request.images, request.image_formats[: len(request.images)], strict=False)
        ):
            mime_type = get_mime_type(img_fmt)
            files.append(("images", (f"image_{i}", img_data, mime_type)))

        form_data: dict[str, Any] = {
            "texts": json.dumps(request.texts),
            "modality": "multimodal",
        }

        data = await self._do_post_multipart(
            "/v1/embeddings/multimodal",
            files=files,
            data=form_data,
            request_id=request.request_id,
            session_id=request.session_id,
        )
        latency_ms = (self._monotonic_now() - start_time) * 1000
        expected_count = len(request.texts) + len(request.images)
        embeddings = self._parse_embeddings(data, expected_count=expected_count)

        self._log.info(
            "multimodal_encoding_completed",
            request_id=request.request_id,
            session_id=request.session_id,
            num_texts=len(request.texts),
            num_images=len(request.images),
            latency_ms=round(latency_ms, 2),
        )

        return EmbeddingResponse(
            embeddings=embeddings,
            model=data.get("model", "ultrasound-clip"),
            dimension=self._dimension,
            modality=EmbeddingModality.MULTIMODAL,
            request_id=request.request_id,
            latency_ms=round(latency_ms, 2),
            token_count=data.get("token_count", 0),
        )
