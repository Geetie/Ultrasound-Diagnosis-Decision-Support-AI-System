"""RAG 检索结果缓存（Redis L1 + 进程内 L2）。

## 为什么缓存要放在 RAG 层（而不是只在智能体层）

``RAGService.retrieve()`` 有三个调用方：

- ``app/agents/knowledge.py`` —— **已有**两层缓存（设计文档 §2.3）；
- ``app/fusion/consistency.py`` —— 一致性校验的指南对比，**直连 RAG**；
- ``app/fusion/crag.py`` —— CRAG 补充检索直接路径，**直连 RAG**。

后两条绕过智能体缓存，每次融合都会产生未缓存的检索（query 增强 → 向量
检索 → 精排 → BM25 兜底）。缓存放在 RAG 服务边界是唯一能同时覆盖三者的
位置，且对智能体路径退化为第二层（先命中智能体缓存，miss 后才落到这里）。

## 键设计

``rag:cache:{organ}:v{kb_version}:{sha1(规范化请求)[:16]}``

- **KB 版本嵌入键名**：知识库版本变更 → 旧键自然失效，无需显式清理；
- 规范化请求包含所有影响检索语义的字段（query / top_k / 各 use_* 开关 /
  min_score_threshold / image_format / extra），避免不同参数互相串味；
- ``request_id`` / ``session_id`` **不入键**（它们不影响结果，只用于追踪，
  且 ``RetrievalResult`` 里也不含这两个字段，不存在跨请求身份泄漏）。

## 不缓存的请求

``image_data`` 非空的请求不缓存：超声图像逐帧不同，缓存既无命中率又会让
键退化为携带大块二进制，属纯负担。

## 载荷裁剪（重要契约）

缓存时不写入 ``KnowledgeChunk.embedding``（bge-m3 768 维）。一条 20 chunk
的结果带向量约 300KB，裁剪后降到 KB 级；且检索结果的消费方（提示词拼装、
指南对比）只用 content / metadata / score。``HeuristicReranker`` 确实会读
``chunk.embedding``，但它在**缓存上游**执行（命中时根本不进入该路径）。
因此：**命中返回的 chunk.embedding 恒为 None**，有无向量不代表数据缺失。

## 降级语义

Redis 任何异常 → 视为 L1 miss（继续走 L2 + 真实检索），不向上抛；
载荷无法 JSON 序列化 → 跳过写入并计数，不影响本次返回；
与智能体层一致地做了 TTL 抖动（防雪崩）与负缓存（防穿透）。
"""
from __future__ import annotations

import hashlib
import json
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import structlog
from cachetools import LRUCache

from app.rag.protocols import RetrievalRequest, RetrievalResult

logger = structlog.get_logger(__name__)

CACHE_KEY_PREFIX = "rag:cache"

# L1（Redis）TTL：比智能体层的 24h 短。RAG 层是共享底座，命中面更宽，
# 一旦检索侧有变更（索引重建/配置调整），短 TTL 能让影响窗口更可控。
DEFAULT_REDIS_TTL_SECONDS = 3600  # 1h
# L2（进程内）TTL：远短于 L1，仅用于扛住瞬时热点与 Redis 抖动
DEFAULT_LOCAL_TTL_SECONDS = 300  # 5min
DEFAULT_LOCAL_CAPACITY = 128
# 负缓存（无结果）TTL：短，避免把"暂时无结果"固化
DEFAULT_NEGATIVE_TTL_SECONDS = 30
# TTL 抖动比例（防雪崩）
DEFAULT_TTL_JITTER_RATIO = 0.1

NEGATIVE_MARKER = "__NEG__"

_EXCLUDE_EMBEDDING = {"chunks": {"__all__": {"chunk": {"embedding"}}}}


@dataclass
class RetrievalCacheStats:
    """命中/未命中计数（供运维观测，不参与业务逻辑）。"""

    hits_redis: int = 0
    hits_local: int = 0
    misses: int = 0
    negative_hits: int = 0
    stores: int = 0
    store_skipped: int = 0
    errors: int = 0

    @property
    def hits(self) -> int:
        return self.hits_redis + self.hits_local

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "hits_redis": self.hits_redis,
            "hits_local": self.hits_local,
            "hits": self.hits,
            "misses": self.misses,
            "negative_hits": self.negative_hits,
            "stores": self.stores,
            "store_skipped": self.store_skipped,
            "errors": self.errors,
            "hit_rate": round(self.hit_rate, 4),
        }


class _LocalTTLCache:
    """带 TTL 的进程内 LRU（懒过期）。"""

    def __init__(self, maxsize: int, ttl_seconds: float) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._cache: LRUCache = LRUCache(maxsize=maxsize)
        self._expires: Dict[str, float] = {}
        self._ttl = float(ttl_seconds)

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        exp = self._expires.get(key)
        if exp is None or time.monotonic() >= exp:
            self._cache.pop(key, None)
            self._expires.pop(key, None)
            return None
        return self._cache[key]

    def set(self, key: str, value: Any, ttl_seconds: Optional[float] = None) -> None:
        self._cache[key] = value
        self._expires[key] = time.monotonic() + (ttl_seconds or self._ttl)

    def clear(self) -> None:
        self._cache.clear()
        self._expires.clear()

    def __len__(self) -> int:
        return len(self._cache)


_instance: Optional["RetrievalCache"] = None


def _register_instance(cache: "RetrievalCache") -> None:
    global _instance
    _instance = cache


def get_retrieval_cache() -> Optional["RetrievalCache"]:
    """取得本进程的检索缓存实例（未启用时为 None）。"""
    return _instance


def reset_retrieval_cache() -> None:
    """清空注册（仅用于测试与进程重启）。"""
    global _instance
    _instance = None


class RetrievalCache:
    """检索结果缓存（L1 Redis + L2 进程内 LRU）。

    Args:
        redis_factory: 需提供 ``async get_client()``（默认注入 ``RedisPool``）。
            ``None`` 时跳过 L1，仅用 L2。
        redis_ttl_seconds: L1 TTL。
        local_capacity: L2 容量。
        local_ttl_seconds: L2 TTL。
        negative_ttl_seconds: 负缓存 TTL。
        jitter_ratio: TTL 抖动比例（防雪崩）。
    """

    def __init__(
        self,
        redis_factory: Any = None,
        *,
        redis_ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
        local_capacity: int = DEFAULT_LOCAL_CAPACITY,
        local_ttl_seconds: int = DEFAULT_LOCAL_TTL_SECONDS,
        negative_ttl_seconds: int = DEFAULT_NEGATIVE_TTL_SECONDS,
        jitter_ratio: float = DEFAULT_TTL_JITTER_RATIO,
    ) -> None:
        self._redis_factory = redis_factory
        self._redis_ttl = int(redis_ttl_seconds)
        self._negative_ttl = int(negative_ttl_seconds)
        self._jitter_ratio = max(0.0, float(jitter_ratio))
        self._local = _LocalTTLCache(local_capacity, local_ttl_seconds)
        self._stats = RetrievalCacheStats()
        # 构造即注册（二期 §4.2）：缓存实例是在 lifespan 里作为构造参数内联创建的，
        # 没有留出显式注册点；自注册让健康检查能拿到它，而不必为一个探针去重构
        # 启动编排。每进程只会构造一个检索缓存，后者覆盖前者不构成问题。
        _register_instance(self)

    def health(self) -> Dict[str, Any]:
        """共享缓存健康信息（二期 §4.2 多副本可观测性）。

        多副本部署下的语义：**L1（Redis）是共享层**，各副本一致；**L2（进程内 LRU）
        是副本本地的**，不共享。因此副本数增加时整体命中率会上升（L1 命中更多），
        但单看某个副本的 L2 命中率会下降——这是正常的，不要据此判断"缓存坏了"。
        """
        stats = self._stats.as_dict()
        return {
            "redis_configured": self._redis_factory is not None,
            "local_entries": len(self._local),
            "local_capacity": getattr(self._local._cache, "maxsize", None),
            "redis_ttl_seconds": self._redis_ttl,
            "hit_rate": stats.get("hit_rate", 0.0),
            "hits": stats.get("hits", 0),
            "misses": stats.get("misses", 0),
            "shared_layer": "redis" if self._redis_factory is not None else "none",
        }

    # ------------------------------------------------------------------
    # 键与可缓存性
    # ------------------------------------------------------------------
    @staticmethod
    def is_cacheable(request: RetrievalRequest) -> bool:
        """图像检索不缓存（逐帧唯一，且键会被大块二进制污染）。"""
        return not request.image_data

    @staticmethod
    def make_key(request: RetrievalRequest, kb_version: str) -> str:
        """生成缓存键（KB 版本嵌入键名实现自然失效）。"""
        canonical = {
            "query": request.query,
            "organ": request.organ,
            "top_k": request.top_k,
            "use_query_enhancement": request.use_query_enhancement,
            "use_reranking": request.use_reranking,
            "use_bm25_fallback": request.use_bm25_fallback,
            "use_multimodal": request.use_multimodal,
            "use_llm_enhancement": request.use_llm_enhancement,
            "min_score_threshold": request.min_score_threshold,
            "image_format": request.image_format,
            # extra 用排序键序列化，保证键稳定（dict 顺序不影响语义）
            "extra": json.dumps(request.extra or {}, sort_keys=True, ensure_ascii=False),
        }
        digest = hashlib.sha1(
            json.dumps(canonical, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:16]
        organ = request.organ or "all"
        return f"{CACHE_KEY_PREFIX}:{organ}:v{kb_version}:{digest}"

    # ------------------------------------------------------------------
    # 读
    # ------------------------------------------------------------------
    async def get(self, key: str) -> Optional[RetrievalResult]:
        """查询缓存。

        Returns:
            命中返回 ``RetrievalResult``（负缓存命中返回空结果对象）；
            未命中返回 ``None``。
        """
        raw = await self._read_redis(key)
        if raw is not None:
            result = self._decode(raw, source="redis")
            if result is not None:
                self._stats.hits_redis += 1
                # L1 命中回填 L2，减少后续同 key 的网络往返
                self._local.set(key, raw)
                return result

        local_raw = self._local.get(key)
        if local_raw is not None:
            result = self._decode(local_raw, source="local")
            if result is not None:
                self._stats.hits_local += 1
                return result

        self._stats.misses += 1
        return None

    # ------------------------------------------------------------------
    # 写
    # ------------------------------------------------------------------
    async def set(self, key: str, result: RetrievalResult) -> None:
        """写入缓存（空结果写负缓存）。"""
        is_negative = not result.chunks
        payload = (
            json.dumps([NEGATIVE_MARKER])
            if is_negative
            else self._encode(result)
        )
        if payload is None:
            self._stats.store_skipped += 1
            return

        ttl = self._ttl_for(is_negative)
        self._local.set(key, payload, ttl_seconds=ttl)
        await self._write_redis(key, payload, ttl)
        self._stats.stores += 1

    # ------------------------------------------------------------------
    # 内部：编解码
    # ------------------------------------------------------------------
    @staticmethod
    def _encode(result: RetrievalResult) -> Optional[str]:
        """序列化（剔除 embedding；失败返回 None 由调用方跳过写入）。"""
        try:
            payload = result.model_dump(mode="json", exclude=_EXCLUDE_EMBEDDING)
            return json.dumps(payload, ensure_ascii=False)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "retrieval_cache_encode_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    def _decode(self, raw: str, *, source: str) -> Optional[RetrievalResult]:
        """反序列化（损坏或负缓存均安全处理）。"""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            data = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            self._stats.errors += 1
            logger.warning(
                "retrieval_cache_value_corrupt",
                source=source,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

        if isinstance(data, list) and data == [NEGATIVE_MARKER]:
            self._stats.negative_hits += 1
            # 负缓存：确认过"无结果"，返回空结果（其余字段用默认值）
            return RetrievalResult(chunks=[])

        try:
            return RetrievalResult.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            self._stats.errors += 1
            logger.warning(
                "retrieval_cache_validate_failed",
                source=source,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    # ------------------------------------------------------------------
    # 内部：TTL 与 Redis
    # ------------------------------------------------------------------
    def _ttl_for(self, is_negative: bool) -> int:
        base = self._negative_ttl if is_negative else self._redis_ttl
        if self._jitter_ratio <= 0 or base <= 0:
            return base
        low = base * (1.0 - self._jitter_ratio)
        high = base * (1.0 + self._jitter_ratio)
        return max(1, int(random.uniform(low, high)))

    async def _read_redis(self, key: str) -> Optional[str]:
        if self._redis_factory is None:
            return None
        try:
            client = await self._redis_factory.get_client()
        except Exception as exc:  # noqa: BLE001
            self._stats.errors += 1
            logger.warning(
                "retrieval_cache_redis_client_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        try:
            raw = await client.get(key)
        except Exception as exc:  # noqa: BLE001
            self._stats.errors += 1
            logger.warning(
                "retrieval_cache_redis_get_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8")
        return str(raw)

    async def _write_redis(self, key: str, payload: str, ttl: int) -> None:
        if self._redis_factory is None:
            return
        try:
            client = await self._redis_factory.get_client()
            await client.setex(key, ttl, payload)
        except Exception as exc:  # noqa: BLE001
            # Redis 不可用只影响跨进程共享，本地 L2 已写入，不向上抛
            self._stats.errors += 1
            logger.warning(
                "retrieval_cache_redis_set_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    # ------------------------------------------------------------------
    # 观测与维护
    # ------------------------------------------------------------------
    def clear_local(self) -> None:
        self._local.clear()

    def stats(self) -> Dict[str, Any]:
        payload = self._stats.as_dict()
        payload["local_entries"] = len(self._local)
        return payload


__all__ = [
    "CACHE_KEY_PREFIX",
    "DEFAULT_LOCAL_CAPACITY",
    "DEFAULT_LOCAL_TTL_SECONDS",
    "DEFAULT_NEGATIVE_TTL_SECONDS",
    "DEFAULT_REDIS_TTL_SECONDS",
    "DEFAULT_TTL_JITTER_RATIO",
    "NEGATIVE_MARKER",
    "RetrievalCache",
    "RetrievalCacheStats",
]
