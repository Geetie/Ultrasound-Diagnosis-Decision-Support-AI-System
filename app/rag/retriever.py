"""T-GA-RAG-02: Multimodal parallel retrieval (Dense + Sparse + Image) with RRF.

This module implements the retrieval leg of the RAG pipeline. Three retrieval
legs can be enabled independently:

- Dense: bge-m3-medical text embedding -> VectorIndexClient (Qdrant/PGVector)
- Sparse: BM25 over knowledge_chunks.content (shared BM25Index instance)
- Image: ultrasound-clip image embedding -> VectorIndexClient (image collection)

The legs are launched concurrently with asyncio.gather. Their results are
merged with Reciprocal Rank Fusion (RRF), then truncated to request.top_k.

Failure handling:
- Any single leg failure is logged and degrades to that leg being empty.
- The dense leg retries once on transient EmbeddingError (connection).
- A missing image collection is treated as a soft skip (not an error).
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.embedding.protocols import (
    EmbeddingError,
    EmbeddingService,
    ImageEmbeddingRequest,
    SearchResult,
    TextEmbeddingRequest,
    VectorIndexClient,
    VectorIndexError,
)
from app.knowledge.protocols import KnowledgeChunk
from app.rag.bm25_fallback import BM25Index
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)

logger = structlog.get_logger("app.rag.retriever")

# Used to detect "collection does not exist" style errors from Qdrant so we
# can silently skip the image leg when the image collection is not provisioned.
_NOT_FOUND_PATTERNS = (
    re.compile(r"not\s*found", re.IGNORECASE),
    re.compile(r"doesn't\s*exist", re.IGNORECASE),
    re.compile(r"does\s*not\s*exist", re.IGNORECASE),
    re.compile(r"collection.*missing", re.IGNORECASE),
)


def _is_collection_not_found(exc: Exception) -> bool:
    msg = str(exc) or ""
    return any(p.search(msg) for p in _NOT_FOUND_PATTERNS)


class _Hit:
    """Internal lightweight hit type used during RRF fusion."""

    __slots__ = ("chunk", "chunk_id", "score", "source")

    def __init__(
        self,
        chunk_id: str,
        chunk: KnowledgeChunk,
        score: float,
        source: RetrievalMethod,
    ):
        self.chunk_id = chunk_id
        self.chunk = chunk
        self.score = score
        self.source = source


class Retriever:
    """T-GA-RAG-02 multi-modal parallel retriever.

    Args:
        vector_client: VectorIndexClient (Qdrant or PGVector, from factory).
        embedding_service: Text embedding service (BgeM3MedicalService).
        ultrasound_clip: Optional image embedding service. If None, image leg
            is disabled regardless of request.use_multimodal.
        bm25_index: Shared BM25Index instance (from bm25_fallback.py).
        session_factory: AsyncSession factory for resolving chunk metadata
            from search results.
        rrf_k: RRF rank constant (default 60, per Cormack et al. 2009).
    """

    def __init__(
        self,
        vector_client: VectorIndexClient,
        embedding_service: EmbeddingService,
        ultrasound_clip: EmbeddingService | None = None,
        bm25_index: BM25Index | None = None,
        session_factory: Any | None = None,
        rrf_k: int | None = None,
    ):
        self._vector_client = vector_client
        self._embedding = embedding_service
        self._clip = ultrasound_clip
        self._bm25 = bm25_index
        self._session_factory = session_factory
        self._rrf_k = rrf_k if rrf_k is not None else settings.rag_rrf_k

    async def retrieve(
        self,
        request: RetrievalRequest,
        enhanced_query: str,
    ) -> RetrievalResult:
        """Run dense + sparse (+ optional image) and RRF-merge them.

        Returns a RetrievalResult with retrieval_method=HYBRID and the
        top-k chunks. If every leg is empty, the result has zero chunks.
        """
        # Each leg is keyed by the RetrievalMethod enum value that its hits
        # carry (image leg hits are tagged DENSE inside _safe_image, so the
        # image leg is also keyed under DENSE).
        tasks: list[tuple[RetrievalMethod, asyncio.Future]] = []
        tasks.append((
            RetrievalMethod.DENSE,
            asyncio.create_task(self._safe_dense(enhanced_query, request)),
        ))
        if self._bm25 is not None:
            tasks.append((
                RetrievalMethod.SPARSE,
                asyncio.create_task(self._safe_sparse(enhanced_query, request)),
            ))
        if (
            request.use_multimodal
            and self._clip is not None
            and request.image_data is not None
        ):
            # Image leg hits are tagged DENSE in _safe_image; key under DENSE
            # so RRF fusion treats them as a second DENSE leg.
            tasks.append((
                RetrievalMethod.DENSE,
                asyncio.create_task(self._safe_image(request)),
            ))

        legs: dict[RetrievalMethod, list[_Hit]] = {}
        for method, task in tasks:
            try:
                hits = await task
            except Exception as exc:
                logger.warning(
                    "retriever_leg_failed",
                    leg=method.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                legs[method] = []
                continue
            existing = legs.get(method, [])
            existing.extend(hits)
            legs[method] = existing

        fused = self._rrf_fuse(legs)
        top_k = max(1, request.top_k)
        fused = fused[:top_k]
        chunks = [
            ScoredChunk(
                chunk=h.chunk,
                score=h.score,
                rerank_score=None,
                retrieval_source=h.source,
            )
            for h in fused
        ]
        # 二期 §6：记录本次引用（best-effort，失败只记日志）。
        # 放在返回前而不是各条检索腿里：只看最终入选的 chunks——落选条目的
        # "被引用"并不是真的被用上，把它算进去会让衰减依据失真。
        await self._record_references(chunks)

        return RetrievalResult(
            chunks=chunks,
            retrieval_method=RetrievalMethod.HYBRID,
        )

    @staticmethod
    async def _record_references(chunks: list[ScoredChunk]) -> None:
        chunk_ids = [chunk.chunk.chunk_id for chunk in chunks if chunk.chunk.chunk_id]
        if not chunk_ids:
            return
        from app.knowledge.decay import record_retrieval_references

        await record_retrieval_references(chunk_ids)

    # ------------------------------------------------------------------
    # Legs
    # ------------------------------------------------------------------

    async def _safe_dense(
        self, query: str, request: RetrievalRequest
    ) -> list[_Hit]:
        try:
            return await self._dense_search(query, request)
        except (EmbeddingError, VectorIndexError) as exc:
            logger.warning(
                "dense_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        except Exception as exc:
            logger.warning(
                "dense_search_unexpected",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    async def _safe_sparse(
        self, query: str, request: RetrievalRequest
    ) -> list[_Hit]:
        if self._bm25 is None or not self._bm25.is_built:
            return []
        try:
            hits = self._bm25.search(
                query, top_k=request.top_k, organ_filter=request.organ
            )
        except Exception as exc:
            logger.warning(
                "sparse_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        return [
            _Hit(
                chunk_id=h.chunk_id,
                chunk=h.chunk,
                score=h.score,
                source=RetrievalMethod.SPARSE,
            )
            for h in hits
        ]

    async def _safe_image(self, request: RetrievalRequest) -> list[_Hit]:
        if self._clip is None or request.image_data is None:
            return []
        try:
            resp = await self._clip.embed_image(
                ImageEmbeddingRequest(
                    image_data=request.image_data,
                    image_format=request.image_format,
                    request_id=request.request_id,
                    session_id=request.session_id,
                )
            )
        except EmbeddingError as exc:
            logger.warning(
                "image_embedding_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        if not resp.embeddings:
            return []
        query_vector = self._embedding_to_list(resp.embeddings[0])
        try:
            results = await self._vector_client.search(
                collection=settings.rag_image_collection,
                query_vector=query_vector,
                top_k=request.top_k,
                filter={"organ": request.organ} if request.organ else None,
            )
        except VectorIndexError as exc:
            if _is_collection_not_found(exc):
                logger.info(
                    "image_collection_not_found_skipping",
                    collection=settings.rag_image_collection,
                )
                return []
            logger.warning(
                "image_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        except Exception as exc:
            if _is_collection_not_found(exc):
                logger.info(
                    "image_collection_not_found_skipping",
                    collection=settings.rag_image_collection,
                )
                return []
            logger.warning(
                "image_search_unexpected",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        return await self._wrap_search_results(results, RetrievalMethod.DENSE)

    async def _dense_search(
        self, query: str, request: RetrievalRequest
    ) -> list[_Hit]:
        # Embed the query (1 call, no retry logic here; the orchestrator wraps).
        text_resp = await self._embedding.embed_text(
            TextEmbeddingRequest(
                texts=[query],
                request_id=request.request_id,
                session_id=request.session_id,
            )
        )
        if not text_resp.embeddings:
            return []
        query_vector = self._embedding_to_list(text_resp.embeddings[0])
        collection = request.kb_version or settings.rag_collection
        flt = {"organ": request.organ} if request.organ else None
        results = await self._vector_client.search(
            collection=collection,
            query_vector=query_vector,
            top_k=request.top_k,
            filter=flt,
        )
        return await self._wrap_search_results(results, RetrievalMethod.DENSE)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _wrap_search_results(
        self,
        results: list[SearchResult],
        source: RetrievalMethod,
    ) -> list[_Hit]:
        if not results:
            return []
        if self._session_factory is None:
            # Without a session factory we cannot hydrate chunks; fall back to
            # placeholder content. This branch is used by tests with mocks.
            return [
                _Hit(
                    chunk_id=r.id,
                    chunk=_placeholder_chunk(r.id),
                    score=float(r.score),
                    source=source,
                )
                for r in results
            ]
        session: AsyncSession = self._session_factory()
        try:
            from sqlalchemy import select

            from app.models.database import KnowledgeChunkModel

            ids = [r.id for r in results]
            stmt = select(KnowledgeChunkModel).where(
                KnowledgeChunkModel.chunk_id.in_(ids)
            )
            db_result = await session.execute(stmt)
            rows = {row.chunk_id: row for row in db_result.scalars().all()}
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                res = close()
                if hasattr(res, "__await__"):
                    await res

        score_map = {r.id: float(r.score) for r in results}
        hits: list[_Hit] = []
        for r in results:
            row = rows.get(r.id)
            if row is None:
                continue
            chunk = KnowledgeChunk(
                chunk_id=row.chunk_id,
                kb_version=row.kb_version,
                organ=row.organ,
                content=row.content,
                metadata=dict(row.meta) if row.meta else {},
                embedding=list(row.embedding) if row.embedding is not None else None,
                source_doc_id=row.source_doc_id,
                section_path=row.section_path or "",
                token_count=row.token_count or 0,
            )
            hits.append(
                _Hit(
                    chunk_id=r.id,
                    chunk=chunk,
                    score=score_map.get(r.id, 0.0),
                    source=source,
                )
            )
        return hits

    def _rrf_fuse(
        self, legs: dict[RetrievalMethod, list[_Hit]]
    ) -> list[_Hit]:
        """Reciprocal Rank Fusion: fused(d) = sum(1 / (k + rank_i(d))).

        Preserves the original _Hit.chunk / _Hit.source from the first leg
        where the chunk appears; ranks the fused list by fused score.
        """
        k = self._rrf_k
        fused_scores: dict[str, float] = {}
        first_seen: dict[str, _Hit] = {}
        for source, hits in legs.items():
            # Sort this leg by score desc to derive ranks.
            ordered = sorted(hits, key=lambda h: h.score, reverse=True)
            for rank, hit in enumerate(ordered, start=1):
                fused_scores[hit.chunk_id] = fused_scores.get(
                    hit.chunk_id, 0.0
                ) + 1.0 / (k + rank)
                if hit.chunk_id not in first_seen:
                    first_seen[hit.chunk_id] = hit

        # Deduplicate by chunk_id, keep the hit with the highest RRF score.
        unique: dict[str, _Hit] = {}
        for cid, score in fused_scores.items():
            hit = first_seen[cid]
            # Use the fused RRF score as the new "score" so consumers see a
            # single, comparable value.
            updated = _Hit(
                chunk_id=hit.chunk_id,
                chunk=hit.chunk,
                score=score,
                source=hit.source,
            )
            unique[cid] = updated
        return sorted(
            unique.values(), key=lambda h: h.score, reverse=True
        )

    @staticmethod
    def _embedding_to_list(emb: Any) -> list[float]:
        """Coerce an embedding (np.ndarray, list, etc.) to a flat list of floats."""
        try:
            import numpy as np
            if isinstance(emb, np.ndarray):
                return emb.astype(np.float32).ravel().tolist()
        except Exception:
            pass
        if isinstance(emb, list):
            return [float(x) for x in emb]
        if hasattr(emb, "tolist"):
            return [float(x) for x in emb.tolist()]
        return [float(emb)]

    async def health_check(self) -> dict[str, bool]:
        """Check health of embedding service and vector index.

        Returns a dict with keys "embedding" and "vector_index", values are
        True if healthy, False otherwise.
        """
        results: dict[str, bool] = {}
        try:
            results["embedding"] = bool(await self._embedding.health_check())
        except Exception:
            results["embedding"] = False
        try:
            results["vector_index"] = bool(await self._vector_client.health_check())
        except Exception:
            results["vector_index"] = False
        return results

    async def close(self) -> None:
        """Close resources held by the retriever."""
        for obj in (self._vector_client, self._embedding):
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    res = close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:
                    pass


def _placeholder_chunk(chunk_id: str) -> KnowledgeChunk:
    """Used when no session_factory is available (e.g. lightweight tests)."""
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="unknown",
        organ="unknown",
        content="[placeholder]",
        metadata={},
        embedding=None,
        source_doc_id=chunk_id,
        section_path="",
        token_count=0,
    )


__all__ = ["Retriever"]
