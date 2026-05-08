from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class EmbeddingErrorCode(str, Enum):
    TIMEOUT = "E2002_EMB_TIMEOUT"
    CONNECTION_FAILED = "E2002_EMB_CONNECTION_FAILED"
    MODEL_LOAD_FAILED = "E2002_EMB_MODEL_LOAD_FAILED"
    ENCODING_FAILED = "E2002_EMB_ENCODING_FAILED"
    INVALID_INPUT = "E2002_EMB_INVALID_INPUT"
    IMAGE_NOT_SUPPORTED = "E2002_EMB_IMAGE_NOT_SUPPORTED"
    SPARSE_NOT_SUPPORTED = "E2002_EMB_SPARSE_NOT_SUPPORTED"
    HEALTH_CHECK_FAILED = "E2002_EMB_HEALTH_CHECK_FAILED"
    DIMENSION_MISMATCH = "E2002_EMB_DIMENSION_MISMATCH"


class EmbeddingModality(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    MULTIMODAL = "multimodal"


class EmbeddingModelType(str, Enum):
    ULTRASOUND_CLIP = "ultrasound-clip"
    BGE_M3_MEDICAL = "bge-m3-medical"


class EmbeddingModelInfo(BaseModel):
    model_name: str = ""
    model_type: EmbeddingModelType = EmbeddingModelType.ULTRASOUND_CLIP
    dimension: int = 768
    max_input_length: int = 512
    supports_image: bool = False
    version: str = ""


class TextEmbeddingRequest(BaseModel):
    texts: list[str]
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("texts")
    @classmethod
    def texts_must_not_be_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("texts must not be empty")
        return v


class ImageEmbeddingRequest(BaseModel):
    image_data: bytes
    image_format: str = "dicom"
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("image_data")
    @classmethod
    def image_data_must_not_be_empty(cls, v: bytes) -> bytes:
        if not v:
            raise ValueError("image_data must not be empty")
        return v


class MultimodalEmbeddingRequest(BaseModel):
    texts: list[str] = Field(default_factory=list)
    images: list[bytes] = Field(default_factory=list)
    image_formats: list[str] = Field(default_factory=lambda: ["dicom"])
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("image_formats")
    @classmethod
    def image_formats_must_not_be_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("image_formats must not be empty")
        return v


class EmbeddingResponse(BaseModel):
    embeddings: list[Any] = Field(default_factory=list)
    model: str = ""
    dimension: int = 0
    modality: EmbeddingModality = EmbeddingModality.TEXT
    request_id: str | None = None
    latency_ms: float = 0.0
    token_count: int = 0

    model_config = {"arbitrary_types_allowed": True}


class SparseEmbeddingResponse(BaseModel):
    dense_embeddings: list[Any] = Field(default_factory=list)
    sparse_weights: list[dict[int, float]] = Field(default_factory=list)
    model: str = ""
    dimension: int = 0
    request_id: str | None = None
    latency_ms: float = 0.0
    token_count: int = 0

    model_config = {"arbitrary_types_allowed": True}


class EmbeddingError(Exception):
    def __init__(
        self,
        error_code: EmbeddingErrorCode,
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


class EmbeddingService(ABC):
    @abstractmethod
    async def embed_text(self, request: TextEmbeddingRequest) -> EmbeddingResponse:
        raise NotImplementedError

    @abstractmethod
    async def embed_text_sparse(self, request: TextEmbeddingRequest) -> SparseEmbeddingResponse:
        raise NotImplementedError

    async def embed_image(self, request: ImageEmbeddingRequest) -> EmbeddingResponse:
        raise NotImplementedError

    async def embed_multimodal(self, request: MultimodalEmbeddingRequest) -> EmbeddingResponse:
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    async def get_model_info(self) -> EmbeddingModelInfo:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
