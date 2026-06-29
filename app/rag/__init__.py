"""RAG (Retrieval-Augmented Generation) package.

Public API exports for T-GA-RAG-01/02/03.
"""
from app.rag.protocols import (
    EnhancedQuery,
    RAGError,
    RAGErrorCode,
    RAGService,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)

__all__ = [
    "EnhancedQuery",
    "RAGError",
    "RAGErrorCode",
    "RAGService",
    "RetrievalMethod",
    "RetrievalRequest",
    "RetrievalResult",
    "ScoredChunk",
]
