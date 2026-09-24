"""T-GA-RAG-03: Cross-encoder reranking + heuristic fallback.

The default RerankService uses the bge-reranker-base model via an HTTP
service (referenced as settings.bge_reranker_host:bge_reranker_port).
This mirrors the BgeM3MedicalService pattern and avoids pulling
transformers/torch into the main app process.

When the bge-reranker service is unreachable, HeuristicReranker
provides a deterministic fallback (token Jaccard + cosine similarity
over chunk embeddings). The orchestrator (service.py) decides which
to use and logs the choice.
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod

import httpx
import numpy as np
import structlog

from app.knowledge.protocols import KnowledgeChunk
from app.rag.bm25_fallback import tokenize
from app.rag.protocols import RetrievalMethod, ScoredChunk

logger = structlog.get_logger("app.rag.reranker")


class RerankError(Exception):
    """Raised when a rerank service fails unrecoverably."""

    def __init__(self, message: str, detail: str | None = None):
        self.message = message
        self.detail = detail
        super().__init__(message)


class RerankService(ABC):
    """Abstract rerank service."""

    @abstractmethod
    async def rerank(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        """Rerank the given chunks against the query, return top_k.

        Returns ScoredChunk objects preserving the original chunk, with
        rerank_score set and score = rerank_score (for the reranked set).
        retrieval_source is set to HYBRID by default (the orchestrator
        adjusts this if it knows the source).
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """Check the rerank service is healthy."""
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """Release any held resources."""
        raise NotImplementedError


class BgeRerankerService(RerankService):
    """bge-reranker-base HTTP client.

    Endpoint contract: POST {base_url}/v1/rerank
        body: {"query": str, "documents": List[str], "top_k": int, "model": str}
        response: {"results": [{"index": int, "relevance_score": float}, ...]}

    Compatible with text-embeddings-inference's rerank endpoint.
    """

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 5.0,
        health_check_timeout_seconds: float = 3.0,
    ):
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._health_timeout = health_check_timeout_seconds
        self._client: httpx.AsyncClient | None = None
        self._closed = False

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._closed:
            raise RerankError("BgeRerankerService has been closed")
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                limits=httpx.Limits(
                    max_connections=10,
                    max_keepalive_connections=5,
                ),
            )
        return self._client

    async def rerank(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        if not chunks:
            return []
        documents = [c.content for c in chunks]
        payload = {
            "query": query,
            "documents": documents,
            "top_k": min(top_k, len(chunks)),
            "model": self._model,
        }
        try:
            client = self._ensure_client()
            response = await client.post("/v1/rerank", json=payload)
        except (httpx.TimeoutException, httpx.ConnectError) as exc:
            raise RerankError(
                f"bge-reranker request failed: {exc}",
                detail=type(exc).__name__,
            ) from exc
        except Exception as exc:
            raise RerankError(
                f"bge-reranker unexpected error: {exc}",
                detail=type(exc).__name__,
            ) from exc

        if response.status_code != 200:
            raise RerankError(
                f"bge-reranker returned HTTP {response.status_code}",
                detail=response.text[:200],
            )

        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise RerankError("bge-reranker returned invalid JSON") from exc

        results = data.get("results") if isinstance(data, dict) else None
        if not isinstance(results, list):
            raise RerankError("bge-reranker response missing 'results' list")

        scored: list[ScoredChunk] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            try:
                idx = int(item.get("index", -1))
            except (TypeError, ValueError):
                continue
            if idx < 0 or idx >= len(chunks):
                continue
            try:
                rel = float(item.get("relevance_score", 0.0))
            except (TypeError, ValueError):
                rel = 0.0
            scored.append(
                ScoredChunk(
                    chunk=chunks[idx],
                    score=rel,
                    rerank_score=rel,
                    retrieval_source=RetrievalMethod.HYBRID,
                )
            )
        scored.sort(key=lambda x: x.rerank_score or 0.0, reverse=True)
        return scored[:top_k]

    async def health_check(self) -> bool:
        try:
            client = self._ensure_client()
            response = await client.get(
                "/health", timeout=self._health_timeout
            )
            return response.status_code == 200
        except (httpx.TimeoutException, httpx.ConnectError):
            return False
        except Exception:
            return False

    async def close(self) -> None:
        self._closed = True
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class HeuristicReranker(RerankService):
    """Deterministic reranker used as a fallback when bge-reranker is down.

    Score per chunk = jaccard_weight * jaccard(query_tokens, chunk_tokens)
                    + cosine_weight * chunk_embedding cosine similarity
    """

    def __init__(self, jaccard_weight: float = 0.5, cosine_weight: float = 0.5):
        if abs((jaccard_weight + cosine_weight) - 1.0) > 1e-6:
            raise ValueError("jaccard_weight + cosine_weight must equal 1.0")
        self._jw = jaccard_weight
        self._cw = cosine_weight

    @staticmethod
    def _jaccard(a: list[str], b: list[str]) -> float:
        sa, sb = set(a), set(b)
        if not sa and not sb:
            return 0.0
        inter = sa & sb
        union = sa | sb
        return len(inter) / len(union) if union else 0.0

    @staticmethod
    def _cosine(a: list[float] | None) -> float:
        """Map a single vector's norm to a 0..1 score (no pairwise here).

        For the heuristic reranker we don't have a query embedding, so we
        fall back to a self-norm signal: high-norm vectors tend to carry
        more "content". This is intentionally weak; it is only a fallback.
        """
        if a is None or not a:
            return 0.0
        arr = np.asarray(a, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm <= 0.0:
            return 0.0
        # Squash to [0, 1] using 1 - exp(-norm/10) so the score is bounded.
        import math
        return 1.0 - math.exp(-norm / 10.0)

    async def rerank(
        self,
        query: str,
        chunks: list[KnowledgeChunk],
        top_k: int,
    ) -> list[ScoredChunk]:
        if not chunks:
            return []
        q_tokens = tokenize(query)
        scored: list[ScoredChunk] = []
        for chunk in chunks:
            j = self._jaccard(q_tokens, tokenize(chunk.content))
            c = self._cosine(chunk.embedding) if chunk.embedding else 0.0
            score = self._jw * j + self._cw * c
            scored.append(
                ScoredChunk(
                    chunk=chunk,
                    score=score,
                    rerank_score=score,
                    retrieval_source=RetrievalMethod.HYBRID,
                )
            )
        scored.sort(key=lambda x: x.rerank_score or 0.0, reverse=True)
        return scored[:top_k]

    async def health_check(self) -> bool:
        return True  # always available, in-process

    async def close(self) -> None:
        return None


__all__ = [
    "BgeRerankerService",
    "HeuristicReranker",
    "RerankError",
    "RerankService",
]
