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
    dimension: int = 768  # ultrasound-clip=768d; bge-m3-medical=1024d
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


# ============================================================================
# Vector index protocols (T-GA-EMB-02 / T-GA-RAG-02 dependencies)
# ============================================================================


class VectorIndexBackend(str, Enum):
    """Vector index backend identifier.

    - QDRANT: Qdrant vector database (production / full mode).
    - PGVECTOR: PostgreSQL pgvector extension (degraded / compact mode).
    """
    QDRANT = "qdrant"
    PGVECTOR = "pgvector"


class VectorIndexErrorCode(str, Enum):
    """Vector index error codes.

    Active error codes:
    - CONNECTION_FAILED: backend unreachable.
    - UPSERT_FAILED: vector insert / update failed.
    - SEARCH_FAILED: similarity search failed.
    - DELETE_FAILED: collection / point deletion failed.
    - COLLECTION_NOT_FOUND: requested collection does not exist.
    - DIMENSION_MISMATCH: vector dimension does not match collection.
    - INVALID_INPUT: malformed collection name / metadata / query.
    """
    CONNECTION_FAILED = "E2003_VEC_CONNECTION_FAILED"
    UPSERT_FAILED = "E2003_VEC_UPSERT_FAILED"
    SEARCH_FAILED = "E2003_VEC_SEARCH_FAILED"
    DELETE_FAILED = "E2003_VEC_DELETE_FAILED"
    COLLECTION_NOT_FOUND = "E2003_VEC_COLLECTION_NOT_FOUND"
    DIMENSION_MISMATCH = "E2003_VEC_DIMENSION_MISMATCH"
    INVALID_INPUT = "E2003_VEC_INVALID_INPUT"


class VectorIndexError(Exception):
    """Unified exception for vector index operations.

    Mirrors EmbeddingError pattern: wraps an error code, human-readable
    message, optional detail, and retry metadata.
    """
    def __init__(
        self,
        error_code: VectorIndexErrorCode,
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


class VectorRecord(BaseModel):
    """A single vector record ready to upsert into a vector index.

    Attributes:
        id: Stable string identifier (non-empty).
        vector: Dense embedding vector (non-empty list of floats).
        metadata: Arbitrary key-value metadata stored alongside the vector.
    """
    id: str
    vector: list[float]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def id_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("id must not be empty")
        return v

    @field_validator("vector")
    @classmethod
    def vector_must_not_be_empty(cls, v: list[float]) -> list[float]:
        if not v:
            raise ValueError("vector must not be empty")
        return v


class SearchResult(BaseModel):
    """A single search hit returned by a vector index client.

    Attributes:
        id: The id of the matched record.
        score: Similarity score (cosine / euclid / dot depending on collection).
        metadata: The metadata payload of the matched record.
    """
    id: str
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class VectorIndexClient(ABC):
    """Abstract vector index client.

    Implementations: QdrantVectorIndex, PgvectorVectorIndex. Selected at runtime
    by `app.embedding.index_factory.create_index_client` based on settings
    and health-check.
    """

    @abstractmethod
    async def create_collection(
        self,
        collection: str,
        dimension: int,
        distance: str = "cosine",
    ) -> None:
        """Create a collection if it does not exist."""
        raise NotImplementedError

    @abstractmethod
    async def upsert(
        self,
        collection: str,
        vectors: list[VectorRecord],
    ) -> None:
        """Insert or update a batch of vectors into the collection."""
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        """Search a collection for the top-k most similar vectors."""
        raise NotImplementedError

    @abstractmethod
    async def delete_collection(self, collection: str) -> None:
        """Delete a collection entirely (or the underlying table for PGVector)."""
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Check whether the backend is reachable and functional."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release all resources held by the client (pool, sockets, etc.)."""
        raise NotImplementedError


__all__ = [
    "EmbeddingErrorCode",
    "EmbeddingModality",
    "EmbeddingModelType",
    "EmbeddingModelInfo",
    "TextEmbeddingRequest",
    "ImageEmbeddingRequest",
    "MultimodalEmbeddingRequest",
    "EmbeddingResponse",
    "SparseEmbeddingResponse",
    "EmbeddingError",
    "EmbeddingService",
    "VectorIndexBackend",
    "VectorIndexErrorCode",
    "VectorIndexError",
    "VectorRecord",
    "SearchResult",
    "VectorIndexClient",
]
