"""RAG (Retrieval-Augmented Generation) Protocols.

This module defines the core data models, error codes, and abstract base classes
for the RAG retrieval pipeline (T-GA-RAG-01/02/03).

Components:
- RAGErrorCode / RAGError: Unified error handling for RAG failures.
- RetrievalMethod: Enum of retrieval strategies (dense / sparse / hybrid / bm25_fallback).
- RetrievalRequest: Request to perform a RAG retrieval.
- ScoredChunk: A single retrieved chunk with score and optional rerank score.
- RetrievalResult: Response with chunks, scores, and pipeline metadata.
- EnhancedQuery: T-GA-RAG-01 query enhancement result (3-layer expansion).
- RAGService: Abstract base class for the RAG service orchestrator.

Design principles (consistent with app/inference/protocols.py and app/embedding/protocols.py):
- Use ABC pattern for multi-backend support.
- Type-safe data structures using Pydantic for strict validation.
- Backward-compatible error code handling.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.knowledge.protocols import KnowledgeChunk


class RAGErrorCode(str, Enum):
    """RAG pipeline error codes.

    Active error codes:
    - ENHANCEMENT_FAILED: Query enhancement (T-GA-RAG-01) failed.
    - RETRIEVAL_FAILED: Multi-modal retrieval (T-GA-RAG-02) failed.
    - RERANK_FAILED: Cross-encoder reranking (T-GA-RAG-03) failed.
    - BM25_INDEX_FAILED: BM25 index build/search failed.
    - TIMEOUT: RAG pipeline exceeded P99 threshold.
    - HEALTH_CHECK_FAILED: Service or one of its dependencies is unhealthy.
    """
    ENHANCEMENT_FAILED = "E5001_RAG_ENHANCEMENT_FAILED"
    RETRIEVAL_FAILED = "E5001_RAG_RETRIEVAL_FAILED"
    RERANK_FAILED = "E5001_RAG_RERANK_FAILED"
    BM25_INDEX_FAILED = "E5001_RAG_BM25_INDEX_FAILED"
    TIMEOUT = "E5001_RAG_TIMEOUT"
    HEALTH_CHECK_FAILED = "E5001_RAG_HEALTH_CHECK_FAILED"


class RAGError(Exception):
    """RAG pipeline unified exception.

    Mirrors InferenceError / EmbeddingError pattern: wraps an error code,
    human-readable message, optional detail, and retry metadata.
    """
    def __init__(
        self,
        error_code: RAGErrorCode,
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


class RetrievalMethod(str, Enum):
    """Retrieval method used to fetch chunks.

    - DENSE: Vector similarity (bge-m3-medical via Qdrant/PGVector).
    - SPARSE: BM25 over knowledge_chunks.content.
    - HYBRID: Reciprocal Rank Fusion (RRF) of dense + sparse.
    - BM25_FALLBACK: BM25-only retrieval triggered when primary methods underperform.
    - GRAPH: Knowledge-graph neighbor expansion (PgVectorGraphAdapter) appended
      after fused top-k; only active when settings.graph_query_enabled is True.
    """
    DENSE = "dense"
    SPARSE = "sparse"
    # 01-1 #3 分槽修复：影像腿独立 key。此前与 dense 腿共用 DENSE，
    # _rrf_fuse 把两条腿拼成同一条 rank 序列互相挤位（高分 dense 块把影像块
    # 推到 rank 20+，k=60 下分差被压平 → 影像命中进不了 top-5）。
    IMAGE = "image"
    HYBRID = "hybrid"
    BM25_FALLBACK = "bm25_fallback"
    GRAPH = "graph"


class RetrievalRequest(BaseModel):
    """Request to perform a RAG retrieval.

    Attributes:
        query: Raw user query (non-empty).
        organ: Optional organ filter (e.g. "thyroid", "liver", "heart").
        image_data: Optional ultrasound image bytes for multimodal retrieval.
        image_format: Image format hint for embedding service.
        top_k: Retriever top-k (default 20, fed into reranker).
        use_query_enhancement: Whether to apply T-GA-RAG-01 enhancement.
        use_reranking: Whether to apply T-GA-RAG-03 cross-encoder reranking.
        use_bm25_fallback: Whether to fall back to BM25 if primary underperforms.
        use_multimodal: Whether to attempt image embedding when image_data is set.
        use_llm_enhancement: Whether to additionally call Agnes LLM for query rewriting.
        min_score_threshold: Minimum score for a chunk to be kept in final result.
        kb_version: Optional knowledge base version filter (forces BM25 index rebuild on change).
        request_id: Optional request correlation id (for tracing).
        session_id: Optional session id (for caching).
        extra: Arbitrary metadata.
    """
    query: str
    organ: str | None = None
    image_data: bytes | None = None
    image_format: str = "dicom"
    top_k: int = 20
    use_query_enhancement: bool = True
    use_reranking: bool = True
    use_bm25_fallback: bool = True
    use_multimodal: bool = True
    use_llm_enhancement: bool = False
    min_score_threshold: float = 0.5
    kb_version: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)

    @field_validator("query")
    @classmethod
    def query_must_not_be_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query must not be empty or whitespace-only")
        return v

    @field_validator("top_k")
    @classmethod
    def top_k_must_be_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("top_k must be positive")
        return v


class ScoredChunk(BaseModel):
    """A single retrieved chunk with its score and source.

    Attributes:
        chunk: KnowledgeChunk content + metadata.
        score: Original retriever score (vector cosine similarity or BM25 score).
        rerank_score: Cross-encoder score (None if reranking not applied).
        retrieval_source: Which retrieval method produced this hit.
    """
    chunk: KnowledgeChunk
    score: float
    rerank_score: float | None = None
    retrieval_source: RetrievalMethod

    model_config = {"arbitrary_types_allowed": True}


class EnhancedQuery(BaseModel):
    """T-GA-RAG-01 query enhancement result.

    Attributes:
        original: Raw user query.
        enhanced: Expanded query string used for retrieval.
        organ: Detected organ (from UDTParser or caller hint).
        layer1_synonyms: Synonyms (L1 expansion).
        layer2_hypernyms: Hypernyms / category co-membership (L2 expansion).
        layer3_related: Related terms in the same organ (L3 expansion).
        llm_enhanced: True if Agnes LLM rewriting succeeded.
    """
    original: str
    enhanced: str
    organ: str | None = None
    layer1_synonyms: list[str] = Field(default_factory=list)
    layer2_hypernyms: list[str] = Field(default_factory=list)
    layer3_related: list[str] = Field(default_factory=list)
    llm_enhanced: bool = False


class RetrievalResult(BaseModel):
    """Response from a RAG retrieval.

    Attributes:
        chunks: ScoredChunk list ordered by final score (post-rerank or post-fallback).
        retrieval_method: Which retrieval method was actually used for the final result.
        enhanced_query: The enhanced query string (None if enhancement disabled).
        llm_enhanced: Whether LLM rewriting was applied during enhancement.
        triggered_bm25_fallback: True if the result came from BM25 fallback path.
        latency_ms: Total retrieval latency in milliseconds.
        p99_violation: True if latency exceeded the configured P99 critical threshold.
        dropped_missing_row: 向量库命中但 PG 无该 chunk_id 而被丢弃的条数（A.88 T4-⑥）。
            "零静默失败"必须带定语：这不再是一句没日志的 ``continue``，而是有
            warning 日志 + 计数 + 本字段。>0 说明索引与 PG 不一致（需重建/补写）。
        image_leg_skip_reason: 本次影像腿未执行/未取到数据的原因码（A.88 T4-①）。
            空串表示影像腿已执行或不需要执行；具体码见
            ``app/rag/image_leg.py`` 的 ``IMAGE_SKIP_*`` 与
            ``app/rag/retriever.py`` 的 ``IMAGE_SKIP_*``。**不写死默认值**：
            每个分支留自己的原因。
    """
    chunks: list[ScoredChunk] = Field(default_factory=list)
    retrieval_method: RetrievalMethod = RetrievalMethod.HYBRID
    enhanced_query: str | None = None
    llm_enhanced: bool = False
    triggered_bm25_fallback: bool = False
    latency_ms: float = 0.0
    p99_violation: bool = False
    dropped_missing_row: int = 0
    image_leg_skip_reason: str = ""

    @property
    def top_score(self) -> float:
        """Highest score in the result (0.0 if empty)."""
        return self.chunks[0].score if self.chunks else 0.0

    @property
    def has_results(self) -> bool:
        """Whether the result contains at least one chunk."""
        return len(self.chunks) > 0


class RAGService(ABC):
    """Abstract RAG service orchestrator.

    Subclasses must implement the full retrieval pipeline:
    enhance -> retrieve -> rerank -> bm25_fallback (as needed).

    The pipeline is defined in the T-GA-RAG-01/02/03 spec.
    """

    @abstractmethod
    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        """Run the full RAG pipeline for a single request.

        Args:
            request: RetrievalRequest with query, organ, and pipeline flags.

        Returns:
            RetrievalResult with final top-k chunks, scores, and metadata.

        Raises:
            RAGError: On unrecoverable pipeline failure.
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Check health of RAG service and all dependencies.

        Returns:
            True if all dependencies (embedding, vector index, reranker, BM25) are healthy.
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Close all resources held by the RAG service."""
        raise NotImplementedError
