"""
Knowledge Base Index Builder Protocols

This module defines the core data models, error codes, and abstract base classes
for the knowledge base index construction pipeline.

Components:
- KnowledgeChunk: A single chunked piece of knowledge base content with embedding.
- IndexBuildRequest / IndexBuildResult: Request/response models for build operations.
- IndexBuildErrorCode / IndexBuildError: Unified error handling for build failures.
- KnowledgeIndexBuilder: Abstract base class for all knowledge base index builders.

Design principles (consistent with app/inference/protocols.py and app/embedding/protocols.py):
- Use ABC pattern for multi-backend support (PGVector now, Qdrant in future).
- Type-safe data structures using Pydantic for strict validation.
- Backward-compatible error code handling for upstream/downstream integration.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator


class IndexBuildErrorCode(str, Enum):
    """Knowledge base index build error codes.

    Active error codes:
    - VERSION_MISMATCH: Provided kb_version does not match the manifest version.
    - SOURCE_NOT_FOUND: Source directory or files are missing.
    - INVALID_MANIFEST: Manifest JSON is malformed or missing required fields.
    - EMBEDDING_FAILED: Embedding service call failed.
    - DB_ERROR: Database write/read failure during index operations.
    - INVALID_ORGAN: Requested organ filter is not in the supported set.
    """
    VERSION_MISMATCH = "E3001_VERSION_MISMATCH"
    SOURCE_NOT_FOUND = "E3001_SOURCE_NOT_FOUND"
    INVALID_MANIFEST = "E3001_INVALID_MANIFEST"
    EMBEDDING_FAILED = "E3001_EMBEDDING_FAILED"
    DB_ERROR = "E3001_DB_ERROR"
    INVALID_ORGAN = "E3001_INVALID_ORGAN"


class IndexBuildError(Exception):
    """Knowledge base index build unified exception.

    Mirrors the InferenceError / EmbeddingError pattern: wraps an error code,
    human-readable message, optional detail, and retry metadata.

    Args:
        error_code: One of IndexBuildErrorCode values.
        message: Human-readable summary.
        detail: Optional technical detail (often the underlying exception).
        retryable: Whether the operation can be safely retried.
        retry_strategy: Retry strategy identifier.
    """
    def __init__(
        self,
        error_code: IndexBuildErrorCode,
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


class KnowledgeChunk(BaseModel):
    """A single chunked piece of knowledge base content with embedding.

    Represents the atomic unit stored in the knowledge index. The pipeline
    (chunker -> feature extractor -> indexer) produces lists of these objects.

    Attributes:
        chunk_id: Globally unique identifier, format "{source_doc_id}#{chunk_index}".
        kb_version: Version string in "kb_v{major}.{minor}.{patch}" form.
        organ: Target organ (e.g. "thyroid", "liver", "heart").
        content: Raw chunk text.
        metadata: Structured metadata extracted by FeatureExtractor
                  (classifications, measurements, anatomical_locations,
                  udt_coverage_rate, strategy, etc.).
        embedding: Optional dense embedding vector (bge-m3-medical 768d).
                   None if not yet embedded.
        source_doc_id: Identifier of the source document this chunk was split from.
        section_path: Hierarchical path (e.g. "TI-RADS/Category 4/Subcategory 4A").
        token_count: Approximate token count (heuristic len(text)//2).
    """
    chunk_id: str
    kb_version: str
    organ: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    embedding: Optional[List[float]] = None
    source_doc_id: str
    section_path: str = ""
    token_count: int = 0

    @field_validator("chunk_id")
    @classmethod
    def chunk_id_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("chunk_id must not be empty")
        return v

    @field_validator("content")
    @classmethod
    def content_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("content must not be empty")
        return v

    @field_validator("organ")
    @classmethod
    def organ_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("organ must not be empty")
        return v

    @field_validator("token_count")
    @classmethod
    def token_count_must_be_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("token_count must be >= 0")
        return v


class IndexBuildRequest(BaseModel):
    """Request to build (or rebuild) a knowledge base index.

    Attributes:
        kb_version: Version string in "kb_v{major}.{minor}.{patch}" form.
        source_dir: Directory containing manifest.json and organ subfolders.
        organs: Optional list of organ names to include; empty/None means all.
        force_rebuild: If True, delete existing chunks for this kb_version first.
        embedding_model: Logical embedding model name (default "bge-m3-medical").
        strategy: Chunking strategy name (default "sentence_boundary").
        batch_size: Number of chunks to embed+index per batch (default 50).
    """
    kb_version: str
    source_dir: str
    organs: List[str] = Field(default_factory=list)
    force_rebuild: bool = False
    embedding_model: str = "bge-m3-medical"
    strategy: str = "sentence_boundary"
    batch_size: int = 50

    @field_validator("kb_version")
    @classmethod
    def kb_version_must_be_valid(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("kb_version must not be empty")
        return v

    @field_validator("source_dir")
    @classmethod
    def source_dir_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("source_dir must not be empty")
        return v

    @field_validator("batch_size")
    @classmethod
    def batch_size_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("batch_size must be positive")
        return v


class IndexBuildResult(BaseModel):
    """Result of a successful index build.

    Attributes:
        kb_version: The version that was built.
        total_chunks: Total number of chunks written to the index.
        organ_breakdown: Per-organ chunk counts (e.g. {"thyroid": 123, "liver": 87}).
        build_duration_seconds: Wall-clock duration of the build.
        index_path: Logical identifier of the target index (e.g. "pg:knowledge_chunks").
        skipped_existing: Number of chunks skipped because they already existed
                          (resume / idempotent rebuild).
    """
    kb_version: str
    total_chunks: int = 0
    organ_breakdown: dict[str, int] = Field(default_factory=dict)
    build_duration_seconds: float = 0.0
    index_path: str = "pg:knowledge_chunks"
    skipped_existing: int = 0


class KnowledgeIndexBuilder(ABC):
    """Abstract knowledge base index builder.

    Subclasses must implement build and validate. The builder orchestrates:
    source loading -> chunking -> feature extraction -> embedding -> indexing.
    """

    @abstractmethod
    async def build(self, request: IndexBuildRequest) -> IndexBuildResult:
        """Build (or rebuild) the knowledge base index.

        Args:
            request: Build request specifying version, source, organs, etc.

        Returns:
            IndexBuildResult with statistics.

        Raises:
            IndexBuildError: On any build failure (with error code).
        """
        raise NotImplementedError

    @abstractmethod
    async def validate(self, kb_version: str) -> bool:
        """Validate that the index for a given kb_version exists and is non-empty.

        Args:
            kb_version: Version to validate.

        Returns:
            True if at least one chunk exists for the version, False otherwise.
        """
        raise NotImplementedError
