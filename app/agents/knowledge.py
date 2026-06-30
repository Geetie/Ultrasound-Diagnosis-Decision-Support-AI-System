"""T-GA-AGT-03 — 知识查询智能体实现。

``KnowledgeAgentImpl`` 封装 RAG Service 并实现两层缓存：

- L1 — Redis（TTL 300s）：跨进程共享，命中后跳过 RAG 流水线。
- L2 — 进程内 LRU（容量 100）：Redis 不可用时降级到 L2，避免对底层 RAG 形成
      雪崩。

缓存键格式：``kb:cache:{organ.value}:{sha1(query)[:16]}``
使用 sha1 前 16 位（64-bit 十六进制）保证：
- 键长度可控（< 80 字符），不撑爆 Redis key 上限；
- 冲突概率极低（2^64 空间）。

降级语义：
- Redis 抛任何异常 → 视为 L1 miss，仅依赖 L2 + RAG；
- L2 命中 → 直接返回；
- RAG 抛 ``RAGError`` → 向上抛（不吞），调用方负责降级；
- query 为空 / top_k <= 0 → 抛 ``ValueError``。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, List, Optional

import structlog
from cachetools import LRUCache

from app.agents.protocols import KnowledgeAgent
from app.config.redis import RedisPool
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ
from app.rag.protocols import RetrievalRequest, RAGService

logger = structlog.get_logger("app.agents.knowledge")


DEFAULT_REDIS_TTL_SECONDS = 300
DEFAULT_LOCAL_CACHE_CAPACITY = 100


class KnowledgeAgentImpl(KnowledgeAgent):
    """知识查询智能体（带两层缓存）。

    Args:
        rag_service: 底层 RAG Service 实例（``RAGService`` ABC 实现）。
        redis_client: 可选 Redis 客户端工厂。默认 ``RedisPool``。
            传入 ``None`` 时跳过 L1 缓存（仅依赖 L2）。
        redis_ttl_seconds: L1 缓存 TTL（秒）。默认 300。
        local_cache_capacity: L2 缓存容量。默认 100。
    """

    def __init__(
        self,
        rag_service: Optional[RAGService],
        redis_client: Any = None,
        redis_ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
        local_cache_capacity: int = DEFAULT_LOCAL_CACHE_CAPACITY,
    ) -> None:
        if rag_service is None:
            raise ValueError("rag_service must not be None")
        if redis_ttl_seconds <= 0:
            raise ValueError("redis_ttl_seconds must be positive")
        if local_cache_capacity <= 0:
            raise ValueError("local_cache_capacity must be positive")

        self._rag = rag_service
        self._redis_factory = redis_client
        self._redis_ttl = redis_ttl_seconds
        self._local_cache: LRUCache = LRUCache(maxsize=local_cache_capacity)

        # 命中统计（仅用于测试和监控；不影响业务逻辑）
        self._hits_redis = 0
        self._hits_local = 0
        self._misses = 0

    # ------------------------------------------------------------------
    # 缓存键
    # ------------------------------------------------------------------

    @staticmethod
    def _make_cache_key(organ: Organ, query: str) -> str:
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
        return f"kb:cache:{organ.value}:{digest}"

    # ------------------------------------------------------------------
    # 内部：Redis 读写（容错）
    # ------------------------------------------------------------------

    async def _read_redis(self, key: str) -> Optional[List[KnowledgeChunk]]:
        if self._redis_factory is None:
            return None
        try:
            client = await self._redis_factory.get_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge_redis_get_client_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        try:
            raw = await client.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge_redis_get_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if raw is None:
            return None
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            items = json.loads(raw)
            return [KnowledgeChunk.model_validate(item) for item in items]
        except Exception as exc:  # noqa: BLE001
            # 缓存内容损坏：当成 miss 处理，避免阻塞业务。
            logger.warning(
                "knowledge_redis_value_corrupt",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    async def _write_redis(self, key: str, chunks: List[KnowledgeChunk]) -> None:
        if self._redis_factory is None:
            return
        try:
            client = await self._redis_factory.get_client()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge_redis_get_client_failed_on_write",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            payload = json.dumps([c.model_dump() for c in chunks])
            await client.setex(key, self._redis_ttl, payload)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge_redis_setex_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # KnowledgeAgent protocol
    # ------------------------------------------------------------------

    async def query(
        self,
        query: str,
        organ: Organ,
        top_k: int = 10,
    ) -> List[KnowledgeChunk]:
        if not query or not query.strip():
            raise ValueError("query must not be empty or whitespace-only")
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        cache_key = self._make_cache_key(organ, query)

        # L1 — Redis
        cached = await self._read_redis(cache_key)
        if cached is not None:
            self._hits_redis += 1
            logger.debug("knowledge_cache_hit_redis", key=cache_key)
            return cached[:top_k]

        # L2 — 进程内 LRU
        if cache_key in self._local_cache:
            self._hits_local += 1
            logger.debug("knowledge_cache_hit_local", key=cache_key)
            chunks = self._local_cache[cache_key]
            return chunks[:top_k]

        # MISS — 调用 RAG
        self._misses += 1
        start = time.monotonic()
        retrieval_request = RetrievalRequest(
            query=query,
            organ=organ.value,
            top_k=top_k,
        )
        result = await self._rag.retrieve(retrieval_request)
        chunks: List[KnowledgeChunk] = [sc.chunk for sc in result.chunks]

        # 写回缓存（失败不阻塞业务）
        await self._write_redis(cache_key, chunks)
        self._local_cache[cache_key] = chunks
        logger.debug(
            "knowledge_cache_miss_rag_latency_ms",
            key=cache_key,
            latency_ms=round((time.monotonic() - start) * 1000.0, 2),
            n_results=len(chunks),
        )
        return chunks[:top_k]

    async def health_check(self) -> bool:
        """RAG 健康即视为本智能体健康；Redis 健康是 bonus。"""
        try:
            return await self._rag.health_check()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "knowledge_rag_health_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    # ------------------------------------------------------------------
    # 测试 / 监控辅助
    # ------------------------------------------------------------------

    def get_cache_stats(self) -> dict:
        """返回缓存命中统计（用于测试和监控）。"""
        total = self._hits_redis + self._hits_local + self._misses
        hit_rate = (
            (self._hits_redis + self._hits_local) / total if total > 0 else 0.0
        )
        return {
            "hits_redis": self._hits_redis,
            "hits_local": self._hits_local,
            "misses": self._misses,
            "total": total,
            "hit_rate": round(hit_rate, 4),
            "local_cache_size": len(self._local_cache),
            "local_cache_capacity": self._local_cache.maxsize,
        }

    def clear_local_cache(self) -> None:
        """清空进程内 LRU 缓存（用于测试和运维）。"""
        self._local_cache.clear()


__all__ = [
    "KnowledgeAgentImpl",
    "DEFAULT_REDIS_TTL_SECONDS",
    "DEFAULT_LOCAL_CACHE_CAPACITY",
]
