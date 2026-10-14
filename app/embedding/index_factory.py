"""向量索引客户端工厂模块。

本模块提供 create_index_client 工厂函数,根据 settings.vector_index_backend 选择
Qdrant 或 PGVector 实现,支持 Qdrant 不可用时自动降级到 PGVector(紧凑模式)。

设计原则:
- 主备降级:Qdrant 为默认(全模式),PGVector 为降级(紧凑模式)
- factory 在初始化时尝试 Qdrant health-check(3s 超时),失败则降级并打 WARN 日志
- 不抛异常(保持向后兼容)
- 双入口模式:create_index_client(显式参数) + create_index_client_from_settings

降级策略(§5.5 设计规格):
- Qdrant 不可用时降级至 PGVector 做主向量检索(延迟增 3-5 倍)
- entity_matching 始终使用 PGVector(与主检索后端无关,≤1万向量)
"""

from __future__ import annotations

import asyncio

import structlog

from app.embedding.protocols import (
    VectorIndexBackend,
    VectorIndexClient,
    VectorIndexError,
    VectorIndexErrorCode,
)

logger = structlog.get_logger("app.embedding.index_factory")


async def create_index_client(
    backend: VectorIndexBackend | str | None = None,
    qdrant_host: str = "localhost",
    qdrant_port: int = 6333,
    qdrant_grpc_port: int = 6334,
    qdrant_timeout_seconds: float = 3.0,
    qdrant_api_key: str | None = None,
    database_url: str = "",
    auto_fallback: bool = True,
) -> VectorIndexClient:
    """创建向量索引客户端。

    Args:
        backend: 显式指定后端(默认从 settings 读取)
        qdrant_host: Qdrant HTTP 主机名
        qdrant_port: Qdrant HTTP 端口
        qdrant_grpc_port: Qdrant gRPC 端口
        qdrant_timeout_seconds: Qdrant health-check 超时秒数
        qdrant_api_key: Qdrant API key(可选)
        database_url: PostgreSQL 连接 URL(PGVector 必需)
        auto_fallback: Qdrant 不可用时是否自动降级到 PGVector

    Returns:
        可用的 VectorIndexClient 实例

    Raises:
        VectorIndexError: 显式 backend=QDRANT 但 Qdrant 不可用且 auto_fallback=False
    """
    resolved_backend = _resolve_backend(backend)
    if resolved_backend == VectorIndexBackend.QDRANT:
        return await _create_qdrant_with_fallback(
            qdrant_host=qdrant_host,
            qdrant_port=qdrant_port,
            qdrant_grpc_port=qdrant_grpc_port,
            qdrant_timeout_seconds=qdrant_timeout_seconds,
            qdrant_api_key=qdrant_api_key,
            database_url=database_url,
            auto_fallback=auto_fallback,
        )
    return await _create_pgvector(database_url)


def _resolve_backend(backend: VectorIndexBackend | str | None) -> VectorIndexBackend:
    if backend is None:
        try:
            from app.config.settings import settings

            backend = settings.vector_index_backend
        except Exception:
            logger.warning("_resolve_backend: 未预期异常，按原逻辑降级", exc_info=True)
            backend = VectorIndexBackend.QDRANT.value
    if isinstance(backend, str):
        return VectorIndexBackend(backend)
    return backend


async def _create_qdrant_with_fallback(
    qdrant_host: str,
    qdrant_port: int,
    qdrant_grpc_port: int,
    qdrant_timeout_seconds: float,
    qdrant_api_key: str | None,
    database_url: str,
    auto_fallback: bool,
) -> VectorIndexClient:
    from app.embedding.qdrant_client import QdrantVectorIndex

    try:
        client = QdrantVectorIndex(
            host=qdrant_host,
            port=qdrant_port,
            grpc_port=qdrant_grpc_port,
            timeout=qdrant_timeout_seconds,
            api_key=qdrant_api_key,
        )
        healthy = await asyncio.wait_for(
            client.health_check(), timeout=qdrant_timeout_seconds
        )
        if not healthy:
            await client.close()
            if auto_fallback:
                logger.warning(
                    "qdrant_unhealthy_falling_back_to_pgvector",
                    host=qdrant_host,
                    port=qdrant_port,
                )
                return await _create_pgvector(database_url)
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message="qdrant unhealthy and auto_fallback disabled",
                retryable=False,
            )
        logger.info(
            "qdrant_client_initialized",
            host=qdrant_host,
            port=qdrant_port,
        )
        return client
    except VectorIndexError as exc:
        if not auto_fallback:
            raise
        if exc.error_code in (
            VectorIndexErrorCode.CONNECTION_FAILED,
            VectorIndexErrorCode.INVALID_INPUT,
        ):
            logger.warning(
                "qdrant_init_failed_falling_back_to_pgvector",
                error=exc.message,
            )
            return await _create_pgvector(database_url)
        raise
    except (asyncio.TimeoutError, Exception) as exc:
        if not auto_fallback:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message=f"qdrant init failed: {exc}",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            )
        logger.warning(
            "qdrant_init_failed_falling_back_to_pgvector",
            error=str(exc),
        )
        return await _create_pgvector(database_url)


async def _create_pgvector(database_url: str) -> VectorIndexClient:
    from app.embedding.pgvector_client import PgvectorVectorIndex

    if not database_url:
        try:
            from app.config.settings import settings

            database_url = settings.database_url
        except Exception:
            logger.warning("_create_pgvector: 未预期异常，按原逻辑降级", exc_info=True)
            database_url = ""
    client = PgvectorVectorIndex(database_url=database_url)
    healthy = await client.health_check()
    if not healthy:
        await client.close()
        raise VectorIndexError(
            error_code=VectorIndexErrorCode.CONNECTION_FAILED,
            message="pgvector unhealthy",
            detail=f"database_url={database_url[:20]}...",
            retryable=True,
            retry_strategy="retry_after_5s",
        )
    logger.info("pgvector_client_initialized")
    return client


async def create_index_client_from_settings(
    backend: str | None = None,
    auto_fallback: bool | None = None,
) -> VectorIndexClient:
    """从 settings 构造 VectorIndexClient(便捷入口)。"""
    from app.config.settings import settings

    return await create_index_client(
        backend=backend if backend is not None else settings.vector_index_backend,
        qdrant_host=settings.qdrant_host,
        qdrant_port=settings.qdrant_port,
        qdrant_grpc_port=settings.qdrant_grpc_port,
        qdrant_timeout_seconds=settings.qdrant_timeout_seconds,
        database_url=settings.database_url,
        auto_fallback=(
            auto_fallback if auto_fallback is not None else settings.vector_index_auto_fallback
        ),
    )


__all__ = [
    "create_index_client",
    "create_index_client_from_settings",
    "ensure_entity_matching_collection",
]


async def ensure_entity_matching_collection(
    client: VectorIndexClient,
    dimension: int = 768,
) -> None:
    """确保 entity_matching collection 存在（PGVector 专用，≤1万向量）。

    设计规格 §5.5: PGVector 仅承载实体近似匹配（≤1万向量），
    主向量检索（100万向量）由 Qdrant 承载。

    Args:
        client: VectorIndexClient 实例（通常为 PgvectorVectorIndex）。
        dimension: 向量维度（默认 768，对应 ultrasound-clip）。
    """
    from app.config.settings import settings

    try:
        await client.create_collection(
            collection=settings.rag_entity_matching_collection,
            dimension=dimension,
            distance="cosine",
        )
        logger.info(
            "entity_matching_collection_created",
            collection=settings.rag_entity_matching_collection,
            dimension=dimension,
        )
    except Exception as exc:
        # Collection may already exist; that's fine.
        logger.debug(
            "entity_matching_collection_create_skipped",
            error=str(exc),
        )
