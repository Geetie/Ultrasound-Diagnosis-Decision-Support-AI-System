"""
Hybrid Indexer for Knowledge Base Chunks

Stores KnowledgeChunk records in PostgreSQL with pgvector for vector search
and JSONB for structured metadata. Supports:

- Upsert (idempotent on chunk_id, supports resume / partial rebuild)
- Delete-by-version (force-rebuild cleanup)
- Vector search by cosine distance (with optional organ filter)
- Existence check by chunk_id (for resume logic in the builder)

Architecture note: the implementation is wired to the existing
`knowledge_chunks` table (KnowledgeChunkModel). Future EMB track can swap
in a Qdrant-backed implementation behind the same HybridIndexer protocol
without changing the builder.
"""
from __future__ import annotations

from typing import Protocol

import structlog
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.knowledge.protocols import KnowledgeChunk
from app.models.database import KnowledgeChunkModel

logger = structlog.get_logger("app.knowledge.hybrid_indexer")


class HybridIndexer(Protocol):
    """Protocol for hybrid indexers (PGVector now; Qdrant later)."""
    async def upsert(
        self, chunks: list[KnowledgeChunk], session: AsyncSession
    ) -> int:
        ...

    async def delete_by_version(
        self, kb_version: str, session: AsyncSession
    ) -> int:
        ...

    async def exists(self, chunk_id: str, session: AsyncSession) -> bool:
        ...

    async def get_by_id(
        self, chunk_id: str, session: AsyncSession
    ) -> KnowledgeChunk | None:
        ...

    async def vector_search(
        self,
        query_embedding: list[float],
        k: int,
        organ: str | None,
        session: AsyncSession,
    ) -> list[KnowledgeChunk]:
        ...


def _chunk_to_row(chunk: KnowledgeChunk) -> dict:
    """Map KnowledgeChunk to ORM insert values.

    Note: KnowledgeChunkModel uses ``meta = Column('metadata', JSONB)`` to
    avoid clashing with SQLAlchemy's declarative ``metadata`` attribute, so
    the values dict must use the attribute name ``meta`` (not the column
    name ``metadata``).
    ⚠️ 该规则只适用于 `.values()`（按属性名取值）；`on_conflict_do_update`
    的 ``set_`` 字符串键是**数据库列名**（必须写 ``metadata``），见下方 upsert。
    """
    return {
        "chunk_id": chunk.chunk_id,
        "kb_version": chunk.kb_version,
        "organ": chunk.organ,
        "content": chunk.content,
        "meta": chunk.metadata or {},
        "embedding": chunk.embedding,
        "source_doc_id": chunk.source_doc_id,
        "section_path": chunk.section_path or "",
        "token_count": chunk.token_count,
    }


def _row_to_chunk(row: KnowledgeChunkModel) -> KnowledgeChunk:
    """Map ORM row back to KnowledgeChunk."""
    return KnowledgeChunk(
        chunk_id=row.chunk_id,
        kb_version=row.kb_version,
        organ=row.organ,
        content=row.content,
        metadata=row.meta or {},
        embedding=list(row.embedding) if row.embedding is not None else None,
        source_doc_id=row.source_doc_id,
        section_path=row.section_path or "",
        token_count=row.token_count or 0,
    )


def build_upsert_statement(values: list[dict]):
    """构造 chunk 幂等 upsert 语句（独立函数便于编译级回归测试）。"""
    stmt = pg_insert(KnowledgeChunkModel).values(values)
    return stmt.on_conflict_do_update(
        index_elements=[KnowledgeChunkModel.chunk_id],
        set_={
            # ⚠️ set_ 的字符串键是**数据库列名**（物理列 metadata，
            # 由 ORM `meta = Column('metadata', …)` 映射）——用属性名
            # "meta" 会报 `column "meta" does not exist`
            # （真机 2026-09-29 首次正式建库实测，此前从未跑通过）。
            "kb_version": stmt.excluded.kb_version,
            "organ": stmt.excluded.organ,
            "content": stmt.excluded.content,
            "metadata": stmt.excluded.metadata,
            "embedding": stmt.excluded.embedding,
            "source_doc_id": stmt.excluded.source_doc_id,
            "section_path": stmt.excluded.section_path,
            "token_count": stmt.excluded.token_count,
            "updated_at": stmt.excluded.updated_at,
        },
    )


class PGVectorHybridIndexer:
    """PostgreSQL + pgvector backed hybrid indexer.

    Uses ON CONFLICT (chunk_id) DO UPDATE for idempotent upserts. Cosine
    distance is computed via pgvector's `cosine_distance` operator.
    """

    def __init__(self, batch_flush_size: int = 50):
        if batch_flush_size <= 0:
            raise ValueError("batch_flush_size must be positive")
        self.batch_flush_size = batch_flush_size

    async def upsert(
        self, chunks: list[KnowledgeChunk], session: AsyncSession
    ) -> int:
        """Insert or update chunks in batches. Returns count written."""
        if not chunks:
            return 0

        written = 0
        for start in range(0, len(chunks), self.batch_flush_size):
            batch = chunks[start : start + self.batch_flush_size]
            values = [_chunk_to_row(c) for c in batch]
            stmt = build_upsert_statement(values)
            await session.execute(stmt)
            await session.flush()
            written += len(batch)
        await session.commit()
        logger.info("indexer_upsert_complete", written=written)
        return written

    async def delete_by_version(
        self, kb_version: str, session: AsyncSession
    ) -> int:
        """Delete all chunks for a given kb_version. Returns row count."""
        stmt = delete(KnowledgeChunkModel).where(
            KnowledgeChunkModel.kb_version == kb_version
        )
        result = await session.execute(stmt)
        await session.commit()
        count = result.rowcount or 0
        logger.info(
            "indexer_delete_by_version", kb_version=kb_version, count=count
        )
        return count

    async def exists(self, chunk_id: str, session: AsyncSession) -> bool:
        stmt = select(KnowledgeChunkModel.chunk_id).where(
            KnowledgeChunkModel.chunk_id == chunk_id
        )
        result = await session.execute(stmt)
        return result.first() is not None

    async def get_by_id(
        self, chunk_id: str, session: AsyncSession
    ) -> KnowledgeChunk | None:
        stmt = select(KnowledgeChunkModel).where(
            KnowledgeChunkModel.chunk_id == chunk_id
        )
        result = await session.execute(stmt)
        row = result.scalar_one_or_none()
        return _row_to_chunk(row) if row is not None else None

    async def vector_search(
        self,
        query_embedding: list[float],
        k: int,
        organ: str | None,
        session: AsyncSession,
    ) -> list[KnowledgeChunk]:
        """Return top-k chunks by cosine similarity. Optionally filter by organ.

        Note: this returns ALL chunks whose embedding is not None; downstream
        RAG ranking (T-GA-RAG-02/03) is expected to re-rank these results.
        """
        if not query_embedding:
            return []
        if k <= 0:
            return []

        stmt = select(
            KnowledgeChunkModel,
            KnowledgeChunkModel.embedding.cosine_distance(query_embedding).label(
                "distance"
            ),
        ).where(KnowledgeChunkModel.embedding.is_not(None))

        if organ:
            stmt = stmt.where(KnowledgeChunkModel.organ == organ)

        stmt = stmt.order_by("distance").limit(k)

        result = await session.execute(stmt)
        rows = result.all()
        return [_row_to_chunk(row[0]) for row in rows]

    async def count_by_version(
        self, kb_version: str, session: AsyncSession
    ) -> int:
        """Return number of chunks for a given kb_version (used by validate)."""
        from sqlalchemy import func
        stmt = select(func.count()).select_from(KnowledgeChunkModel).where(
            KnowledgeChunkModel.kb_version == kb_version
        )
        result = await session.execute(stmt)
        return int(result.scalar() or 0)
