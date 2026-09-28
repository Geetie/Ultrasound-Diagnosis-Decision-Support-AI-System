"""PGVector 向量索引客户端实现。

本模块实现 VectorIndexClient 协议的 PGVector 后端，作为紧凑模式降级方案。
- 使用 asyncpg + pgvector (pgvector.sqlalchemy.register_vector)
- 表结构: vector_index_{collection} (id, embedding vector(N), metadata jsonb)
- 支持 cosine 距离 (<=> 操作符, 转换为 1 - distance 作为 score)
- filter 转换: dict → metadata->>'key' = value 的 WHERE 子句(key 白名单校验防 SQL 注入)

依赖: pip install asyncpg pgvector
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

from app.config.dsn import to_asyncpg_dsn
from app.embedding.protocols import (
    SearchResult,
    VectorIndexClient,
    VectorIndexError,
    VectorIndexErrorCode,
    VectorRecord,
)

logger = structlog.get_logger("app.embedding.pgvector_client")

_COLLECTION_NAME_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")
_META_KEY_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]{0,62}$")

try:
    import asyncpg
    from pgvector.asyncpg import register_vector

    _PGVECTOR_AVAILABLE = True
except ImportError:
    asyncpg = None  # type: ignore[assignment]
    register_vector = None  # type: ignore[assignment]
    _PGVECTOR_AVAILABLE = False


def _validate_collection_name(collection: str) -> str:
    """校验集合名(用于表名后缀)防止 SQL 注入。"""
    if not _COLLECTION_NAME_RE.match(collection):
        raise VectorIndexError(
            error_code=VectorIndexErrorCode.INVALID_INPUT,
            message=f"invalid collection name: {collection!r}",
            detail="must match [a-zA-Z_][a-zA-Z0-9_]{0,62}",
            retryable=False,
        )
    return f"vector_index_{collection}"


def _validate_meta_key(key: str) -> str:
    """校验 metadata key 防止 SQL 注入。"""
    if not _META_KEY_RE.match(key):
        raise VectorIndexError(
            error_code=VectorIndexErrorCode.INVALID_INPUT,
            message=f"invalid metadata key: {key!r}",
            detail="must match [a-zA-Z_][a-zA-Z0-9_]{0,62}",
            retryable=False,
        )
    return key


class PgvectorVectorIndex(VectorIndexClient):
    """PGVector 向量索引客户端(降级方案)。

    Attributes:
        database_url: PostgreSQL 连接 URL
        pool_min_size: 连接池最小连接数
        pool_max_size: 连接池最大连接数
    """

    def __init__(
        self,
        database_url: str,
        pool_min_size: int = 1,
        pool_max_size: int = 5,
    ):
        if not _PGVECTOR_AVAILABLE:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message="asyncpg or pgvector not installed",
                detail="pip install asyncpg pgvector",
                retryable=False,
            )
        if not database_url:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.INVALID_INPUT,
                message="database_url is required for PGVector backend",
                retryable=False,
            )
        # 归一掉 SQLAlchemy 方言后缀（`postgresql+asyncpg://` → `postgresql://`），
        # 否则 asyncpg 建池必失败，见 _normalize_dsn 的说明。
        self._database_url = to_asyncpg_dsn(database_url)
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None
        self._closed = False

    async def _get_pool(self) -> asyncpg.Pool:
        if self._closed:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message="pgvector client has been closed",
                retryable=False,
            )
        if self._pool is None:
            try:
                self._pool = await asyncpg.create_pool(
                    dsn=self._database_url,
                    min_size=self._pool_min_size,
                    max_size=self._pool_max_size,
                    init=self._init_connection,
                )
            except Exception as exc:
                raise VectorIndexError(
                    error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                    message="failed to create asyncpg pool",
                    detail=str(exc),
                    retryable=True,
                    retry_strategy="retry_after_5s",
                )
        return self._pool

    @staticmethod
    async def _init_connection(conn: Any) -> None:
        """在每个新连接上注册 pgvector 类型。"""
        await register_vector(conn)

    async def create_collection(
        self,
        collection: str,
        dimension: int,
        distance: str = "cosine",
    ) -> None:
        table_name = _validate_collection_name(collection)
        if distance.lower() != "cosine":
            logger.warning(
                "pgvector_unsupported_distance_fallback_to_cosine",
                requested=distance,
            )
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS {table_name} (
                        id TEXT PRIMARY KEY,
                        embedding vector({dimension}),
                        metadata JSONB DEFAULT '{{}}'::jsonb
                    )
                    """
                )
                try:
                    await conn.execute(
                        f"""
                        CREATE INDEX IF NOT EXISTS {table_name}_embedding_idx
                        ON {table_name}
                        USING ivfflat (embedding vector_cosine_ops)
                        WITH (lists = 100)
                        """
                    )
                except Exception as exc:
                    logger.warning(
                        "pgvector_ivfflat_index_creation_failed",
                        error=str(exc),
                        table=table_name,
                    )
            logger.info(
                "pgvector_collection_created",
                table=table_name,
                dimension=dimension,
            )
        except asyncpg.PostgresError as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.UPSERT_FAILED,
                message=f"failed to create table {table_name}",
                detail=str(exc),
                retryable=False,
            )

    async def upsert(
        self,
        collection: str,
        vectors: list[VectorRecord],
    ) -> None:
        if not vectors:
            return
        table_name = _validate_collection_name(collection)
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn, conn.transaction():
                for record in vectors:
                    await conn.execute(
                        f"""
                            INSERT INTO {table_name} (id, embedding, metadata)
                            VALUES ($1, $2::vector, $3::jsonb)
                            ON CONFLICT (id) DO UPDATE
                            SET embedding = EXCLUDED.embedding,
                                metadata = EXCLUDED.metadata
                            """,
                        record.id,
                        record.vector,
                        json.dumps(record.metadata, ensure_ascii=False),
                    )
            logger.info(
                "pgvector_upsert_completed",
                table=table_name,
                count=len(vectors),
            )
        except asyncpg.PostgresError as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.UPSERT_FAILED,
                message=f"pgvector upsert failed for {table_name}",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        table_name = _validate_collection_name(collection)
        where_clauses: list[str] = []
        where_params: list[Any] = []
        if filter:
            for key, value in filter.items():
                safe_key = _validate_meta_key(key)
                where_params.append(json.dumps(value, ensure_ascii=False) if not isinstance(value, (str, int, float, bool)) else value)
                where_clauses.append(f"metadata->>{safe_key} = ${len(where_params)}::text")

        where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        query = (
            f"SELECT id, 1 - (embedding <=> ${len(where_params) + 1}::vector) AS score, metadata "
            f"FROM {table_name}{where_sql} "
            f"ORDER BY embedding <=> ${len(where_params) + 1}::vector "
            f"LIMIT ${len(where_params) + 2}"
        )
        query_params = [*where_params, query_vector, top_k]
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(query, *query_params)
            results = [
                SearchResult(
                    id=row["id"],
                    score=float(row["score"]),
                    metadata=json.loads(row["metadata"]) if isinstance(row["metadata"], str) else dict(row["metadata"] or {}),
                )
                for row in rows
            ]
            logger.info(
                "pgvector_search_completed",
                table=table_name,
                top_k=top_k,
                returned=len(results),
            )
            return results
        except asyncpg.UndefinedTableError as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.COLLECTION_NOT_FOUND,
                message=f"table {table_name} not found",
                detail=str(exc),
                retryable=False,
            )
        except asyncpg.PostgresError as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.SEARCH_FAILED,
                message=f"pgvector search failed for {table_name}",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            )

    async def delete_collection(self, collection: str) -> None:
        table_name = _validate_collection_name(collection)
        pool = await self._get_pool()
        try:
            async with pool.acquire() as conn:
                await conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            logger.info(
                "pgvector_collection_deleted",
                table=table_name,
            )
        except asyncpg.PostgresError as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.DELETE_FAILED,
                message=f"failed to drop table {table_name}",
                detail=str(exc),
                retryable=False,
            )

    async def health_check(self) -> bool:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.fetchval("SELECT 1")
            return True
        except Exception as exc:
            # 必须同时打 `detail`：`VectorIndexError.__str__` 只输出
            # `[错误码] message`，真正的原因（DSN 非法、密码错、库不存在…）在 detail 里。
            # 真机上就是因为这里只打 __str__，日志只剩一行
            # `pgvector_health_check_failed error=[E2003_VEC_CONNECTION_FAILED]`，
            # 真因被完全吞掉，排查只能靠猜。
            logger.warning(
                "pgvector_health_check_failed",
                error=str(exc),
                error_detail=getattr(exc, "detail", None),
                error_type=type(exc).__name__,
            )
            return False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


__all__ = ["PgvectorVectorIndex"]
