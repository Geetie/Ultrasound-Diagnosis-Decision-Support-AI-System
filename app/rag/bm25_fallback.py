"""T-GA-RAG-03: BM25 fallback + shared BM25 index.

This module provides two cooperating classes:

- BM25Index: a process-wide, lazy-loaded BM25Okapi index over the
  knowledge_chunks table. It is shared between the hybrid retriever
  (sparse leg) and the fallback path so we never build the index twice.

- BM25Fallback: a thin policy object that decides when to trigger
  the BM25-only fallback and runs the search.

Tokenization: we use a simple deterministic strategy (CJK continuous
character sequences + alphanumeric splits). This avoids adding jieba
as a hard dependency. Quality is good enough for short medical queries
and is fully testable. A future iteration can swap in jieba without
changing the public API.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, List, Optional, Sequence

import structlog
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.knowledge.protocols import KnowledgeChunk
from app.models.database import KnowledgeChunkModel
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalResult,
    ScoredChunk,
)

logger = structlog.get_logger("app.rag.bm25_fallback")


# Tokenization patterns.
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9]+")


def tokenize(text: str) -> List[str]:
    """Simple CJK+alphanumeric tokenizer with 2-char window for CJK.

    Examples:
        "低回声结节"        -> ["低回声结节", "低回声", "回声结", "结节"]
        "TI-RADS 4A nodule" -> ["ti", "rads", "4a", "nodule"]

    CJK handling: keep the full phrase (helps exact-substring match) AND
    emit a 2-character sliding window (improves recall for partial matches).
    """
    if not text:
        return []
    text = text.lower()
    tokens: List[str] = []
    for cjk_match in _CJK_RE.finditer(text):
        cjk = cjk_match.group(0)
        tokens.append(cjk)
        # 2-char sliding window for partial-match recall
        for i in range(len(cjk) - 1):
            tokens.append(cjk[i:i + 2])
    # Replace non-alphanumeric with spaces, then split.
    ascii_cleaned = _NON_WORD_RE.sub(" ", text)
    for tok in ascii_cleaned.split():
        if tok:
            tokens.append(tok)
    return tokens


@dataclass
class _BuiltIndex:
    """Internal holder for a built BM25 index + metadata."""
    bm25: BM25Okapi
    chunk_ids: List[str]            # index position -> chunk_id
    chunks: List[KnowledgeChunk]     # index position -> chunk
    organs: List[str]                # index position -> organ
    kb_version: Optional[str]
    built_at: float


@dataclass
class BM25Hit:
    """A single BM25 search hit."""
    chunk_id: str
    score: float
    chunk: KnowledgeChunk


class BM25Index:
    """Lazy BM25 index over knowledge_chunks.

    Threading: not thread-safe; uses asyncio.Lock for concurrent awaits.

    The index is built on first use (or on kb_version change) and cached
    in memory. A `max_chunks` cap is enforced; rows beyond the cap are
    truncated with a WARN log so operators can spot knowledge-base bloat.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | Callable[[], Awaitable[AsyncSession]],
        max_chunks: int | None = None,
    ):
        self._session_factory = session_factory
        self._max_chunks = max_chunks or settings.rag_bm25_max_chunks
        self._built: Optional[_BuiltIndex] = None
        self._lock = asyncio.Lock()

    async def ensure_built(self, kb_version: Optional[str] = None) -> None:
        """Build the index if not built or if kb_version changed.

        Idempotent and concurrency-safe via an asyncio.Lock.
        """
        async with self._lock:
            if self._built is not None:
                if kb_version is None or self._built.kb_version == kb_version:
                    return
                logger.info(
                    "bm25_index_rebuild_due_to_version_change",
                    old=self._built.kb_version,
                    new=kb_version,
                )
            await self._build_locked(kb_version)

    async def _build_locked(self, kb_version: Optional[str]) -> None:
        session = self._session_factory()
        try:
            stmt = select(
                KnowledgeChunkModel.chunk_id,
                KnowledgeChunkModel.kb_version,
                KnowledgeChunkModel.organ,
                KnowledgeChunkModel.content,
                KnowledgeChunkModel.meta,
                KnowledgeChunkModel.embedding,
                KnowledgeChunkModel.source_doc_id,
                KnowledgeChunkModel.section_path,
                KnowledgeChunkModel.token_count,
            ).order_by(KnowledgeChunkModel.chunk_id)
            if kb_version is not None:
                stmt = stmt.where(KnowledgeChunkModel.kb_version == kb_version)
            stmt = stmt.limit(self._max_chunks)
            result = await session.execute(stmt)
            rows = result.all()
        finally:
            # AsyncSession close handled by caller via factory cleanup;
            # we only close if the factory returned a session we own.
            close = getattr(session, "close", None)
            if callable(close):
                res = close()
                if hasattr(res, "__await__"):
                    await res

        if len(rows) >= self._max_chunks:
            logger.warning(
                "bm25_index_truncated_to_max_chunks",
                max_chunks=self._max_chunks,
            )

        corpus: List[List[str]] = []
        chunk_ids: List[str] = []
        chunks: List[KnowledgeChunk] = []
        organs: List[str] = []
        for row in rows:
            tokenized = tokenize(row.content or "")
            if not tokenized:
                # Skip completely empty-tokenized docs to keep BM25Okapi happy.
                continue
            corpus.append(tokenized)
            chunk_ids.append(row.chunk_id)
            organs.append(row.organ)
            meta = dict(row.meta) if row.meta else {}
            embedding = list(row.embedding) if row.embedding is not None else None
            chunks.append(
                KnowledgeChunk(
                    chunk_id=row.chunk_id,
                    kb_version=row.kb_version,
                    organ=row.organ,
                    content=row.content,
                    metadata=meta,
                    embedding=embedding,
                    source_doc_id=row.source_doc_id,
                    section_path=row.section_path or "",
                    token_count=row.token_count or 0,
                )
            )

        bm25 = BM25Okapi(corpus) if corpus else None
        self._built = _BuiltIndex(
            bm25=bm25,
            chunk_ids=chunk_ids,
            chunks=chunks,
            organs=organs,
            kb_version=kb_version,
            built_at=time.monotonic(),
        )
        logger.info(
            "bm25_index_built",
            num_chunks=len(chunks),
            kb_version=kb_version,
        )

    def search(
        self,
        query: str,
        top_k: int = 20,
        organ_filter: Optional[str] = None,
    ) -> List[BM25Hit]:
        """Run a BM25 search against the (already built) index.

        Returns an empty list if the index has not been built yet (caller
        should call ensure_built() first).

        This is a CPU-bound sync method, intentionally: rank_bm25 is fast
        for <10k docs and avoids event-loop blocking for typical queries.
        """
        if self._built is None or self._built.bm25 is None:
            logger.warning("bm25_search_called_before_ensure_built")
            return []
        tokenized_query = tokenize(query)
        if not tokenized_query:
            return []
        scores = self._built.bm25.get_scores(tokenized_query)

        # Collect (score, position) pairs and filter by organ.
        scored: List[tuple[float, int]] = []
        for idx, score in enumerate(scores):
            if organ_filter is not None and self._built.organs[idx] != organ_filter:
                continue
            # BM25Okapi can produce negative scores; clamp to 0 for cleanliness.
            scored.append((max(0.0, float(score)), idx))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(0, top_k)]
        return [
            BM25Hit(
                chunk_id=self._built.chunk_ids[idx],
                score=score,
                chunk=self._built.chunks[idx],
            )
            for score, idx in top
        ]

    async def health_check(self) -> bool:
        return self._built is not None and self._built.bm25 is not None

    @property
    def is_built(self) -> bool:
        return self._built is not None and self._built.bm25 is not None

    @property
    def num_chunks(self) -> int:
        return len(self._built.chunk_ids) if self._built else 0

    async def close(self) -> None:
        self._built = None


class BM25Fallback:
    """T-GA-RAG-03 BM25 fallback policy + search wrapper.

    Triggers when:
    - The retriever returned 0 chunks.
    - The top1 score is below `min_top1_score`.
    - The retriever returned fewer than `min_results` chunks.
    """

    def __init__(
        self,
        bm25_index: BM25Index,
        min_top1_score: float | None = None,
        min_results: int | None = None,
    ):
        self._index = bm25_index
        self._min_top1_score = (
            min_top1_score if min_top1_score is not None
            else settings.rag_bm25_min_top1_score
        )
        self._min_results = (
            min_results if min_results is not None
            else settings.rag_bm25_min_results
        )

    def should_trigger(self, retriever_result: RetrievalResult) -> bool:
        if not retriever_result.has_results:
            return True
        if retriever_result.top_score < self._min_top1_score:
            return True
        if len(retriever_result.chunks) < self._min_results:
            return True
        return False

    def trigger_reason(self, retriever_result: RetrievalResult) -> str:
        if not retriever_result.has_results:
            return "no_results"
        if retriever_result.top_score < self._min_top1_score:
            return "low_top1_score"
        if len(retriever_result.chunks) < self._min_results:
            return "too_few_results"
        return "ok"

    async def fallback_search(
        self,
        query: str,
        top_k: int,
        organ: Optional[str],
    ) -> List[ScoredChunk]:
        """Run BM25 search and wrap hits as ScoredChunks.

        Returns an empty list if the index has not been built yet.
        """
        if not self._index.is_built:
            return []
        hits = self._index.search(query, top_k=top_k, organ_filter=organ)
        return [
            ScoredChunk(
                chunk=h.chunk,
                score=h.score,
                rerank_score=None,
                retrieval_source=RetrievalMethod.BM25_FALLBACK,
            )
            for h in hits
        ]


__all__ = [
    "BM25Index",
    "BM25Fallback",
    "BM25Hit",
    "tokenize",
]
