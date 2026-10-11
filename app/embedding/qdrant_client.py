"""Qdrant 向量索引客户端实现。

本模块实现 VectorIndexClient 协议的 Qdrant 后端，作为生产主方案。
- 使用 qdrant-client 库的 AsyncQdrantClient
- 支持 cosine / euclid / dot 三种距离度量
- filter 转换: dict → qdrant.models.Filter (FieldCondition + MatchValue)
- 错误映射: QdrantException → VectorIndexError

依赖: pip install qdrant-client>=1.7.0
"""

from __future__ import annotations

from typing import Any

import structlog

from app.embedding.protocols import (
    SearchResult,
    VectorIndexClient,
    VectorIndexError,
    VectorIndexErrorCode,
    VectorRecord,
)

logger = structlog.get_logger("app.embedding.qdrant_client")

_DISTANCE_MAP: dict[str, str] = {
    "cosine": "Cosine",
    "euclid": "Euclid",
    "dot": "Dot",
}

try:
    from qdrant_client import AsyncQdrantClient
    from qdrant_client.http import models as qmodels
    from qdrant_client.http.exceptions import UnexpectedResponse

    _QDRANT_AVAILABLE = True
except ImportError:
    AsyncQdrantClient = None  # type: ignore[assignment,misc]
    qmodels = None  # type: ignore[assignment]
    UnexpectedResponse = Exception  # type: ignore[assignment,misc]
    _QDRANT_AVAILABLE = False


class QdrantVectorIndex(VectorIndexClient):
    """Qdrant 向量索引客户端。

    Attributes:
        host: Qdrant HTTP 主机名
        port: Qdrant HTTP 端口
        grpc_port: Qdrant gRPC 端口（可选）
        timeout: 请求超时秒数
        prefix: 集合名前缀（用于多租户隔离，默认 "ultrasound_"）
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6333,
        grpc_port: int = 6334,
        timeout: float = 30.0,
        prefix: str = "ultrasound_",
        api_key: str | None = None,
    ):
        self._host = host
        self._port = port
        self._grpc_port = grpc_port
        self._timeout = timeout
        self._prefix = prefix
        self._api_key = api_key
        self._client: AsyncQdrantClient | None = None
        self._closed = False
        self._import_check_done = False

    def _ensure_qdrant_available(self) -> None:
        """惰性检查 qdrant-client 是否可用(延迟到实际方法调用)。"""
        if self._import_check_done:
            return
        if not _QDRANT_AVAILABLE:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message="qdrant-client not installed",
                detail="pip install qdrant-client>=1.7.0",
                retryable=False,
            )
        self._import_check_done = True

    def _collection_name(self, collection: str) -> str:
        """拼接集合前缀，避免与外部 collection 冲突。"""
        if collection.startswith(self._prefix):
            return collection
        return f"{self._prefix}{collection}"

    def _ensure_client(self) -> AsyncQdrantClient:
        self._ensure_qdrant_available()
        if self._closed:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message="qdrant client has been closed",
                retryable=False,
            )
        if self._client is None:
            self._client = AsyncQdrantClient(
                host=self._host,
                port=self._port,
                grpc_port=self._grpc_port,
                timeout=self._timeout,
                api_key=self._api_key,
            )
        return self._client

    def _build_filter(self, filter_dict: dict[str, Any] | None) -> Any | None:
        """dict filter → qdrant.models.Filter。

        支持的语法:
            {"organ": "thyroid"}                → organ == "thyroid"
            {"organ": ["thyroid", "liver"]}     → organ IN (...)
            {"age": {"gte": 18, "lte": 65}}     → 18 <= age <= 65
        """
        if not filter_dict:
            return None
        must_conditions = []
        for key, value in filter_dict.items():
            if isinstance(value, list):
                must_conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchAny(any=value),
                    )
                )
            elif isinstance(value, dict):
                range_params = {}
                for op, v in value.items():
                    if op == "gte":
                        range_params["gte"] = v
                    elif op == "lte":
                        range_params["lte"] = v
                    elif op == "gt":
                        range_params["gt"] = v
                    elif op == "lt":
                        range_params["lt"] = v
                if range_params:
                    must_conditions.append(
                        qmodels.FieldCondition(key=key, range=qmodels.Range(**range_params))
                    )
            else:
                must_conditions.append(
                    qmodels.FieldCondition(
                        key=key,
                        match=qmodels.MatchValue(value=value),
                    )
                )
        return qmodels.Filter(must=must_conditions)

    async def create_collection(
        self,
        collection: str,
        dimension: int,
        distance: str = "cosine",
    ) -> None:
        client = self._ensure_client()
        collection_name = self._collection_name(collection)
        distance_name = _DISTANCE_MAP.get(distance.lower(), "Cosine")
        try:
            exists = await client.collection_exists(collection_name)
            if exists:
                logger.info(
                    "qdrant_collection_already_exists",
                    collection=collection_name,
                )
                return
            try:
                distance_enum = getattr(qmodels.Distance, distance_name)
            except AttributeError:
                # qdrant-client >= 1.19 起枚举成员改为全大写
                # （COSINE / EUCLID / DOT），早期版本为首字母大写。
                distance_enum = getattr(qmodels.Distance, distance_name.upper())
            await client.create_collection(
                collection_name=collection_name,
                vectors_config=qmodels.VectorParams(
                    size=dimension,
                    distance=distance_enum,
                ),
            )
            logger.info(
                "qdrant_collection_created",
                collection=collection_name,
                dimension=dimension,
                distance=distance_name,
            )
        except UnexpectedResponse as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.UPSERT_FAILED,
                message=f"failed to create collection {collection_name}",
                detail=str(exc),
                retryable=False,
            )
        except Exception as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.CONNECTION_FAILED,
                message=f"qdrant create_collection failed for {collection_name}",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            )

    async def upsert(
        self,
        collection: str,
        vectors: list[VectorRecord],
    ) -> None:
        if not vectors:
            return
        client = self._ensure_client()
        collection_name = self._collection_name(collection)
        try:
            # qdrant point id 只接受 UUID/无符号整数——chunk_id 是任意字符串，
            # 用 uuid5 确定性映射（同 id 幂等），原始 id 存 payload 供检索还原
            # （A.50.8 E6 首次真用即暴露：400 Bad Request）
            import uuid as _uuid

            points = [
                qmodels.PointStruct(
                    id=str(_uuid.uuid5(_uuid.NAMESPACE_URL, record.id)),
                    vector=record.vector,
                    payload={**dict(record.metadata), "chunk_id": record.id},
                )
                for record in vectors
            ]
            await client.upsert(collection_name=collection_name, points=points, wait=True)
            logger.info(
                "qdrant_upsert_completed",
                collection=collection_name,
                count=len(points),
            )
        except UnexpectedResponse as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.UPSERT_FAILED,
                message=f"qdrant upsert failed for {collection_name}",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            )
        except Exception as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.UPSERT_FAILED,
                message="unexpected error during qdrant upsert",
                detail=str(exc),
                retryable=False,
            )

    async def search(
        self,
        collection: str,
        query_vector: list[float],
        top_k: int = 10,
        filter: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        client = self._ensure_client()
        collection_name = self._collection_name(collection)
        qdrant_filter = self._build_filter(filter)
        try:
            hits = await client.search(
                collection_name=collection_name,
                query_vector=query_vector,
                limit=top_k,
                query_filter=qdrant_filter,
                with_payload=True,
            )
            results = [
                SearchResult(
                    # 还原原始 chunk_id（upsert 时存 payload；uuid5 点id 仅内部）
                    id=str((hit.payload or {}).get("chunk_id") or hit.id),
                    score=float(hit.score),
                    metadata=dict(hit.payload or {}),
                )
                for hit in hits
            ]
            logger.info(
                "qdrant_search_completed",
                collection=collection_name,
                top_k=top_k,
                returned=len(results),
            )
            return results
        except UnexpectedResponse as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.SEARCH_FAILED,
                message=f"qdrant search failed for {collection_name}",
                detail=str(exc),
                retryable=True,
                retry_strategy="retry_after_5s",
            )
        except Exception as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.SEARCH_FAILED,
                message="unexpected error during qdrant search",
                detail=str(exc),
                retryable=False,
            )

    async def delete_collection(self, collection: str) -> None:
        client = self._ensure_client()
        collection_name = self._collection_name(collection)
        try:
            await client.delete_collection(collection_name=collection_name)
            logger.info(
                "qdrant_collection_deleted",
                collection=collection_name,
            )
        except UnexpectedResponse as exc:
            raise VectorIndexError(
                error_code=VectorIndexErrorCode.DELETE_FAILED,
                message=f"qdrant delete_collection failed for {collection_name}",
                detail=str(exc),
                retryable=False,
            )

    async def health_check(self) -> bool:
        try:
            client = self._ensure_client()
            await client.get_collections()
            return True
        except Exception as exc:
            logger.warning(
                "qdrant_health_check_failed",
                error=str(exc),
            )
            return False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._client is not None:
            await self._client.close()
            self._client = None


__all__ = ["QdrantVectorIndex"]
