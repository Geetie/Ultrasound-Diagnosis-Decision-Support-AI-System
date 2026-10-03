"""T-GA-AGT-03 — 知识查询智能体实现。

``KnowledgeAgentImpl`` 封装 RAG Service 并实现两层缓存，与设计文档
``01-系统架构设计.md`` §2.3 完全对齐：

- **L1 — Redis**（TTL 24h，86400s）：跨进程共享缓存，命中后跳过 RAG 流水线。
- **L2 — 进程内 LRU**（容量 ≤ 50，TTL 1h）：Redis 不可用时降级到 L2，
      同时作为本进程内的快速访问层（避免对底层 RAG 形成雪崩）。

**缓存键格式**（与设计文档 §2.3 缓存键格式一致）：

- 顶层 query-based 缓存键：``kb:cache:{organ.value}:v{kb_version}:{sha1(query)[:16]}``
  - KB 版本号嵌入键名实现**自然过期失效**（知识库版本变更 → 旧键自然消失）；
  - sha1 前 16 位（64-bit 十六进制）保证键长度可控（< 80 字符），冲突概率极低。
- L2 键与 L1 共享同一 key space，通过 TTL 1h < L1 TTL 24h 满足
  ``L2 TTL ≤ L1 TTL`` 的一致性约束。

**版本校验**：
- 缓存键包含 ``kb_version``，每次 ``query()`` 时通过注入的
  ``kb_version_resolver`` 获取当前版本；若缓存命中键中的版本与当前版本
  不一致 → 视为 miss，重新走 RAG。
- 设计文档 §2.3 明确"知识库版本更新时，按器官维度清除缓存"——本实现通过
  版本号嵌入键名实现自然清除，无需显式 ACK 事件。

**防护机制**（与设计文档 §2.3 防护策略对齐）：
- **缓存击穿防护** (Cache Breakdown)：进程内 ``asyncio.Lock`` 字典保证同一
  缓存键在并发查询时只触发一次 RAG 调用。热点 key 失效时不会出现
  "雷击" 现象打到 RAG。
- **缓存穿透防护** (Cache Penetration)：RAG 返回空结果时写入 L1+L2
  **负缓存**（short TTL），避免恶意或异常查询反复打穿到 RAG。
- **缓存雪崩防护** (Cache Avalanche)：实际写入 L1 / L2 的 TTL 会被
  ``jitter_ratio`` (默认 0.1) 随机扰动，避免大量 key 同时过期引发雪崩。

**降级语义**：
- Redis 抛任何异常 → 视为 L1 miss，仅依赖 L2 + RAG；
- L2 命中 → 直接返回（同时校验版本，版本不一致视为 miss）；
- RAG 抛 ``RAGError`` → 向上抛（不吞），调用方负责降级；
- query 为空 / top_k <= 0 → 抛 ``ValueError``；
- 缓存值损坏（JSON 解析失败）→ 当作 miss 处理。

**L2 TTL 机制**：
- 使用 ``cachetools.LRUCache`` 配合 ``expires`` dict + 懒清理实现 TTL 1h。
- 每次访问 L2 时检查 entry 是否过期；过期则移除并视为 miss。
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import random
import time
from collections.abc import Callable
from typing import Any

import structlog
from cachetools import LRUCache

from app.agents.protocols import KnowledgeAgent
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ
from app.rag.protocols import RAGService, RetrievalRequest

logger = structlog.get_logger("app.agents.knowledge")


# ---------------------------------------------------------------------------
# 默认常量（与 01-系统架构设计.md §2.3 对齐）
# ---------------------------------------------------------------------------

# L1 — Redis TTL：24 小时（一期 full Profile 默认）
DEFAULT_REDIS_TTL_SECONDS = 86400  # 24 * 3600

# L2 — 进程内 LRU 容量：≤ 50（设计文档明确）
DEFAULT_LOCAL_CACHE_CAPACITY = 50

# L2 — 进程内 LRU TTL：1 小时（设计文档明确 L2 TTL ≤ L1 TTL）
DEFAULT_LOCAL_CACHE_TTL_SECONDS = 3600  # 1 * 3600

# KB 版本号解析器：默认返回 "kb_v0.0.0"（无版本信息时使用）
# 调用方可在 lifespan 中注入 `lambda: get_current_kb_version()` 形式
DEFAULT_KB_VERSION = "kb_v0.0.0"

# 雪崩防护：TTL 随机化比例（±10%）
DEFAULT_TTL_JITTER_RATIO = 0.1

# 穿透防护：负缓存 TTL（短 TTL，避免污染正常缓存；写回 L1+L2）
DEFAULT_NEGATIVE_CACHE_TTL_SECONDS = 60

# 击穿防护：单 key 锁最大等待时间（防止慢 RAG 阻塞读路径）
DEFAULT_KEY_LOCK_TIMEOUT_SECONDS = 30.0

# T-GB-AGT-02：检索档位中可映射到 ``RetrievalRequest`` 的字段
# （``RetrievalProfile.as_retrieval_kwargs()`` 的键，不含 full_validation）。
_RETRIEVAL_PROFILE_FIELDS = frozenset(
    {
        "use_query_enhancement",
        "use_reranking",
        "use_bm25_fallback",
        "use_llm_enhancement",
        "top_k",
    }
)


class _TTLLRUCache:
    """带 TTL 的 LRU 缓存包装器。

    基于 ``cachetools.LRUCache`` 维护容量上限，配合 ``expires`` dict 记录
    每个 entry 的过期时间。每次 ``__getitem__`` / ``__contains__`` 触发
    懒清理（lazy eviction）：访问时若发现已过期则移除并视为未命中。

    Args:
        maxsize: 最大条目数。
        ttl_seconds: 每个条目的存活时间（秒）。
    """

    def __init__(self, maxsize: int, ttl_seconds: int) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._cache: LRUCache = LRUCache(maxsize=maxsize)
        self._expires: dict[Any, float] = {}
        self._ttl = float(ttl_seconds)

    def __contains__(self, key: Any) -> bool:
        return self._is_alive(key)

    def _is_alive(self, key: Any) -> bool:
        if key not in self._cache:
            return False
        exp = self._expires.get(key)
        if exp is None or time.monotonic() >= exp:
            # 过期：移除
            with contextlib.suppress(KeyError):
                del self._cache[key]
            self._expires.pop(key, None)
            return False
        return True

    def __setitem__(self, key: Any, value: Any) -> None:
        self._cache[key] = value
        self._expires[key] = time.monotonic() + self._ttl
        # 并发审查 P2-1：LRU 淘汰只动 _cache，_expires 的 TTL 元数据永久残留
        # （被淘汰的 key 不会再被访问 → 懒过期永不触发）→ 缓慢无界泄漏。
        if len(self._expires) > len(self._cache):
            for stale in [k for k in self._expires if k not in self._cache]:
                del self._expires[stale]

    def __getitem__(self, key: Any) -> Any:
        if not self._is_alive(key):
            raise KeyError(key)
        return self._cache[key]

    def __delitem__(self, key: Any) -> None:
        del self._cache[key]
        self._expires.pop(key, None)

    def __len__(self) -> int:
        return len(self._cache)

    @property
    def maxsize(self) -> int:
        return self._cache.maxsize

    def clear(self) -> None:
        self._cache.clear()
        self._expires.clear()

    def items(self):
        # 遍历时跳过已过期项（与 cachelib 行为一致）
        now = time.monotonic()
        for k in list(self._cache.keys()):
            exp = self._expires.get(k)
            if exp is None or now >= exp:
                continue
            yield k, self._cache[k]


class KnowledgeAgentImpl(KnowledgeAgent):
    """知识查询智能体（带两层缓存 + KB 版本校验 + 击穿/穿透/雪崩防护）。

    Args:
        rag_service: 底层 RAG Service 实例（``RAGService`` ABC 实现）。
        redis_client: 可选 Redis 客户端工厂。默认 ``RedisPool``。
            传入 ``None`` 时跳过 L1 缓存（仅依赖 L2）。
        redis_ttl_seconds: L1 缓存 TTL（秒）。默认 86400（24h）。
        local_cache_capacity: L2 缓存容量。默认 50。
        local_cache_ttl_seconds: L2 缓存 TTL（秒）。默认 3600（1h）。
        kb_version_resolver: 可选 callable，返回当前 KB 版本字符串。
            默认 ``lambda: DEFAULT_KB_VERSION``。
            在 lifespan 启动期可注入 ``lambda: get_current_kb_version()``。
        jitter_ratio: 雪崩防护：写入 TTL 时的随机扰动比例（±）。
            0.1 表示 TTL 在 [base*0.9, base*1.1] 之间均匀分布。默认 0.1。
        negative_cache_ttl_seconds: 穿透防护：负缓存 TTL（秒）。默认 60。
        enable_breakdown_lock: 击穿防护：是否启用单 key 锁。默认 True。
        key_lock_timeout_seconds: 击穿防护：单 key 锁最大等待时间（秒）。默认 30.0。
    """

    def __init__(
        self,
        rag_service: RAGService | None,
        redis_client: Any = None,
        redis_ttl_seconds: int = DEFAULT_REDIS_TTL_SECONDS,
        local_cache_capacity: int = DEFAULT_LOCAL_CACHE_CAPACITY,
        local_cache_ttl_seconds: int = DEFAULT_LOCAL_CACHE_TTL_SECONDS,
        kb_version_resolver: Callable[[], str] | None = None,
        jitter_ratio: float = DEFAULT_TTL_JITTER_RATIO,
        negative_cache_ttl_seconds: int = DEFAULT_NEGATIVE_CACHE_TTL_SECONDS,
        enable_breakdown_lock: bool = True,
        key_lock_timeout_seconds: float = DEFAULT_KEY_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        if rag_service is None:
            raise ValueError("rag_service must not be None")
        if redis_ttl_seconds <= 0:
            raise ValueError("redis_ttl_seconds must be positive")
        if local_cache_capacity <= 0:
            raise ValueError("local_cache_capacity must be positive")
        if local_cache_ttl_seconds <= 0:
            raise ValueError("local_cache_ttl_seconds must be positive")
        if not (0.0 <= jitter_ratio < 1.0):
            raise ValueError("jitter_ratio must be in [0.0, 1.0)")
        if negative_cache_ttl_seconds <= 0:
            raise ValueError("negative_cache_ttl_seconds must be positive")
        if key_lock_timeout_seconds <= 0:
            raise ValueError("key_lock_timeout_seconds must be positive")
        # 设计文档 §2.3 一致性约束：L2 TTL ≤ L1 TTL
        if local_cache_ttl_seconds > redis_ttl_seconds:
            raise ValueError(
                f"local_cache_ttl_seconds ({local_cache_ttl_seconds}) must be "
                f"<= redis_ttl_seconds ({redis_ttl_seconds}); "
                "see 01-系统架构设计.md §2.3 L1/L2 TTL consistency rule"
            )
        # 负缓存 TTL 须为正（避免污染正缓存的过期时间判断）
        if negative_cache_ttl_seconds > redis_ttl_seconds:
            raise ValueError(
                f"negative_cache_ttl_seconds ({negative_cache_ttl_seconds}) "
                f"should be <= redis_ttl_seconds ({redis_ttl_seconds}); "
                "negative cache TTL must not exceed positive cache TTL"
            )

        self._rag = rag_service
        self._redis_factory = redis_client
        self._redis_ttl = redis_ttl_seconds
        self._local_cache: _TTLLRUCache = _TTLLRUCache(
            maxsize=local_cache_capacity,
            ttl_seconds=local_cache_ttl_seconds,
        )
        self._kb_version_resolver = kb_version_resolver or (lambda: DEFAULT_KB_VERSION)
        self._jitter_ratio = jitter_ratio
        self._negative_cache_ttl = negative_cache_ttl_seconds
        self._enable_breakdown_lock = enable_breakdown_lock
        self._key_lock_timeout = key_lock_timeout_seconds

        # 击穿防护：单 key 互斥锁字典（懒创建）
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._key_locks_guard = asyncio.Lock()

        # 命中统计（仅用于测试和监控；不影响业务逻辑）
        self._hits_redis = 0
        self._hits_local = 0
        self._misses = 0
        self._version_mismatch_count = 0
        self._breakdown_lock_wait_count = 0
        self._negative_cache_hits = 0

    # ------------------------------------------------------------------
    # KB 版本
    # ------------------------------------------------------------------

    def _current_kb_version(self) -> str:
        """返回当前 KB 版本号（解析器抛错时回退到默认版本）。"""
        try:
            v = self._kb_version_resolver()
            return v if v else DEFAULT_KB_VERSION
        except Exception as exc:
            logger.warning(
                "knowledge_kb_version_resolver_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return DEFAULT_KB_VERSION

    # ------------------------------------------------------------------
    # 缓存键（与设计文档 §2.3 格式对齐：版本号嵌入键名）
    # ------------------------------------------------------------------

    @staticmethod
    def _make_cache_key(
        organ: Organ,
        query: str,
        kb_version: str,
        profile_fingerprint: str = "",
    ) -> str:
        """生成 query-based 顶层缓存键。

        格式：``kb:cache:{organ.value}:v{kb_version}:{sha1(query)[:16]}``
        传入 ``profile_fingerprint`` 时追加 ``:{指纹}`` 后缀。

        KB 版本号嵌入键名 → KB 版本变更时旧键自然失效，无需显式清除。
        档位指纹同理：标准路径（不精排、不 LLM 增强）与全量路径的检索
        结果不同，必须落在不同缓存条目，否则"升级到全量"会命中标准路径
        的旧结果而形同虚设。
        """
        digest = hashlib.sha1(query.encode("utf-8")).hexdigest()[:16]
        suffix = f":{profile_fingerprint}" if profile_fingerprint else ""
        return f"kb:cache:{organ.value}:v{kb_version}:{digest}{suffix}"

    # ------------------------------------------------------------------
    # 内部：检索档位（T-GB-AGT-02）
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_profile(profile: Any) -> dict[str, Any] | None:
        """把档位归一为 ``RetrievalRequest`` 可用的字段子集。

        接受 ``RoutingProfile`` / ``dict``；未知字段一律丢弃（避免把
        ``full_validation`` 之类的非检索字段塞进请求）。
        """
        if not profile:
            return None
        try:
            if hasattr(profile, "as_retrieval_kwargs"):
                data = dict(profile.as_retrieval_kwargs())
            else:
                data = dict(profile)
        except Exception as exc:
            logger.warning(
                "knowledge_profile_normalize_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None
        return {
            k: v for k, v in data.items() if k in _RETRIEVAL_PROFILE_FIELDS
        } or None

    @classmethod
    def _profile_fingerprint(cls, profile: Any) -> str:
        """档位指纹（写入缓存键，避免标准/全量路径互相污染）。"""
        normalized = cls._normalize_profile(profile)
        if not normalized:
            return ""
        payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False)
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]

    # ------------------------------------------------------------------
    # 内部：负缓存哨兵（用于区分"未命中"与"明确为空"）
    # ------------------------------------------------------------------

    # 使用单例对象作为哨兵（list 不可哈希，但此 sentinel 不作为 cache key），
    # 通过 ``is`` 判定比字符串 sentinel 更安全。
    _NEGATIVE_SENTINEL: list[Any] = []  # type: ignore[var-annotated]

    @classmethod
    def _is_negative_marker(cls, value: Any) -> bool:
        """判定一个缓存值是否为「负缓存哨兵」（表示明确无结果）。

        通过 ``is`` 判定（sentinel 是单例），与普通空列表区分。
        """
        return value is cls._NEGATIVE_SENTINEL

    # ------------------------------------------------------------------
    # 内部：雪崩防护 — TTL 随机化
    # ------------------------------------------------------------------

    def _jitter_ttl(self, base_ttl: int) -> int:
        """对 TTL 应用随机扰动（± jitter_ratio）。

        雪崩防护：避免大量缓存 key 同时过期。
        """
        if self._jitter_ratio <= 0.0 or base_ttl <= 0:
            return base_ttl
        low = base_ttl * (1.0 - self._jitter_ratio)
        high = base_ttl * (1.0 + self._jitter_ratio)
        return max(1, int(random.uniform(low, high)))

    # ------------------------------------------------------------------
    # 内部：击穿防护 — 单 key 互斥锁
    # ------------------------------------------------------------------

    async def _get_key_lock(self, key: str) -> asyncio.Lock:
        """懒创建并返回 ``key`` 对应的互斥锁。

        使用 ``_key_locks_guard`` 保护字典并发写；不同 key 互不阻塞。
        """
        # 快速路径（无锁读）
        lock = self._key_locks.get(key)
        if lock is not None:
            return lock
        # 慢速路径（加锁创建）
        async with self._key_locks_guard:
            lock = self._key_locks.get(key)
            if lock is None:
                lock = asyncio.Lock()
                self._key_locks[key] = lock
            return lock

    async def _cleanup_key_lock(self, key: str) -> None:
        """RAG 调用完成后清理对应 key 的锁，避免字典无界增长。"""
        async with self._key_locks_guard:
            self._key_locks.pop(key, None)

    # ------------------------------------------------------------------
    # 内部：Redis 读写（容错 + 负缓存 + TTL 抖动）
    # ------------------------------------------------------------------

    # Redis 负缓存标记：JSON 序列化为 ``["__NEG__"]``
    _REDIS_NEGATIVE_MARKER = "__NEG__"

    async def _read_redis(
        self, key: str
    ) -> tuple[list[KnowledgeChunk] | None, bool]:
        """从 Redis 读取缓存。

        Returns:
            ``(chunks, is_negative)``：
            - ``(None, False)``：未命中
            - ``(chunks, False)``：正缓存命中
            - ``([], True)``：负缓存命中（RAG 已确认无结果）
        """
        if self._redis_factory is None:
            return None, False
        try:
            client = await self._redis_factory.get_client()
        except Exception as exc:
            logger.warning(
                "knowledge_redis_get_client_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None, False
        try:
            raw = await client.get(key)
        except Exception as exc:
            logger.warning(
                "knowledge_redis_get_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None, False
        if raw is None:
            return None, False
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            items = json.loads(raw)
            # 负缓存标记
            if isinstance(items, list) and len(items) == 1 and items[0] == self._REDIS_NEGATIVE_MARKER:
                return [], True
            return [KnowledgeChunk.model_validate(item) for item in items], False
        except Exception as exc:
            # 缓存内容损坏：当成 miss 处理，避免阻塞业务。
            logger.warning(
                "knowledge_redis_value_corrupt",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None, False

    async def _write_redis(
        self,
        key: str,
        chunks: list[KnowledgeChunk] | None,
        is_negative: bool = False,
    ) -> None:
        """写入 Redis 缓存（自动应用 TTL 抖动 + 支持负缓存）。

        Args:
            key: 缓存键。
            chunks: 缓存值；``None`` 时不写入。
            is_negative: True 时写入负缓存标记，TTL 用 ``_negative_cache_ttl``。
        """
        if self._redis_factory is None or chunks is None:
            return
        try:
            client = await self._redis_factory.get_client()
        except Exception as exc:
            logger.warning(
                "knowledge_redis_get_client_failed_on_write",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return
        try:
            if is_negative:
                payload = json.dumps([self._REDIS_NEGATIVE_MARKER])
                ttl = self._jitter_ttl(self._negative_cache_ttl)
            else:
                payload = json.dumps([c.model_dump() for c in chunks])
                ttl = self._jitter_ttl(self._redis_ttl)
            await client.setex(key, ttl, payload)
        except Exception as exc:
            logger.warning(
                "knowledge_redis_setex_failed",
                key=key,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    def _write_local(
        self,
        key: str,
        chunks: list[KnowledgeChunk] | None,
        is_negative: bool = False,
    ) -> None:
        """写入 L2 进程内缓存（直接构造 ``_TTLLRUCache`` 的 entry）。"""
        if chunks is None:
            return
        if is_negative:
            # 负缓存值用 ``[]``；通过 ``_is_negative_marker`` 识别
            # _TTLLRUCache 接受任意可哈希 key，``[]`` 不可哈希 → 转 tuple
            self._local_cache[key] = self._NEGATIVE_SENTINEL
        else:
            self._local_cache[key] = list(chunks)

    def _read_local(
        self, key: str
    ) -> tuple[list[KnowledgeChunk] | None, bool]:
        """从 L2 进程内缓存读取。

        Returns: ``(chunks, is_negative)``，同 ``_read_redis``。
        """
        if key not in self._local_cache:
            return None, False
        value = self._local_cache[key]
        if self._is_negative_marker(value):
            return [], True
        return value, False

    # ------------------------------------------------------------------
    # 内部：实际 RAG 调用（被击穿锁保护）
    # ------------------------------------------------------------------

    async def _call_rag(
        self,
        query: str,
        organ: Organ,
        top_k: int,
        profile: Any = None,
    ) -> list[KnowledgeChunk]:
        """调用底层 RAG（**不加锁**；由 ``query()`` 在锁内调用）。

        T-GB-AGT-02：``profile`` 传入时，其 ``use_reranking`` /
        ``use_llm_enhancement`` / ``use_bm25_fallback`` / ``use_query_enhancement``
        会覆盖 RAG 的默认开关，``top_k`` 用于**检索深度**（送入精排的候选
        条数）；调用方要求的 ``top_k``（返回 chunk 上限）由 ``query()`` 负责
        截断，两者语义不同、互不冲突。
        """
        profile_kwargs = self._normalize_profile(profile) or {}
        retrieval_top_k = int(profile_kwargs.pop("top_k", 0) or 0) or top_k
        retrieval_request = RetrievalRequest(
            query=query,
            organ=organ.value,
            top_k=retrieval_top_k,
            **profile_kwargs,
        )
        result = await self._rag.retrieve(retrieval_request)
        return [sc.chunk for sc in result.chunks]

    # ------------------------------------------------------------------
    # KnowledgeAgent protocol
    # ------------------------------------------------------------------

    async def query(
        self,
        query: str,
        organ: Organ,
        top_k: int = 10,
        profile: Any = None,
    ) -> list[KnowledgeChunk]:
        """带两层缓存 + 三重防护的知识查询。

        防护策略（与 01-架构 §2.3 对齐）：
        - L1/L2 缓存键含 KB 版本 + 检索档位指纹（自然失效）；
        - 击穿防护：单 key 互斥锁，热点 key 失效时只触发一次 RAG；
        - 穿透防护：RAG 空结果写入短 TTL 负缓存；
        - 雪崩防护：写入 TTL ±10% 随机扰动。

        Args:
            query: 用户查询文本（非空）。
            organ: 目标器官。
            top_k: 返回的 chunk 上限。
            profile: T-GB-AGT-02 检索档位（``dict`` 或 ``RetrievalProfile``）。
                非空时其开关与检索深度作用于底层 ``RetrievalRequest``，并
                计入缓存键指纹——标准路径与全量路径结果不会互相污染。

        Returns:
            ``KnowledgeChunk`` 列表（已截断到 top_k）。

        Raises:
            ValueError: query 为空 / top_k <= 0。
            RAGError: RAG 失败（调用方负责降级）。
        """
        if not query or not query.strip():
            raise ValueError("query must not be empty or whitespace-only")
        if top_k <= 0:
            raise ValueError("top_k must be positive")

        kb_version = self._current_kb_version()
        cache_key = self._make_cache_key(
            organ, query, kb_version, self._profile_fingerprint(profile)
        )

        # ========== L1 (Redis) 命中检查 ==========
        cached, is_neg = await self._read_redis(cache_key)
        if cached is not None:
            self._hits_redis += 1
            if is_neg:
                self._negative_cache_hits += 1
            logger.debug(
                "knowledge_cache_hit_redis",
                key=cache_key,
                kb_version=kb_version,
                is_negative=is_neg,
            )
            return cached[:top_k]

        # ========== L2 (本地 LRU) 命中检查 ==========
        cached, is_neg = self._read_local(cache_key)
        if cached is not None:
            self._hits_local += 1
            if is_neg:
                self._negative_cache_hits += 1
            logger.debug(
                "knowledge_cache_hit_local",
                key=cache_key,
                kb_version=kb_version,
                is_negative=is_neg,
            )
            return cached[:top_k]

        # ========== MISS — 调用 RAG ==========
        # 击穿防护：对同一 cache_key 加锁；并发查询只触发一次 RAG。
        if self._enable_breakdown_lock:
            lock = await self._get_key_lock(cache_key)
            try:
                # 等待锁（带超时），避免慢 RAG 永久阻塞读路径
                try:
                    await asyncio.wait_for(
                        lock.acquire(), timeout=self._key_lock_timeout
                    )
                    self._breakdown_lock_wait_count += 1
                except asyncio.TimeoutError:
                    logger.warning(
                        "knowledge_breakdown_lock_timeout",
                        key=cache_key,
                        timeout_seconds=self._key_lock_timeout,
                    )
                    # 超时回退：跳过锁保护直接调用 RAG（best-effort）
                    return await self._rag_fallback_with_cache(
                        query=query,
                        organ=organ,
                        top_k=top_k,
                        cache_key=cache_key,
                        kb_version=kb_version,
                        profile=profile,
                    )
                try:
                    return await self._rag_fallback_with_cache(
                        query=query,
                        organ=organ,
                        top_k=top_k,
                        cache_key=cache_key,
                        kb_version=kb_version,
                        profile=profile,
                    )
                finally:
                    lock.release()
                    await self._cleanup_key_lock(cache_key)
            except Exception:
                # 锁路径异常 → 退化为无锁调用，确保业务不中断
                return await self._rag_fallback_with_cache(
                    query=query,
                    organ=organ,
                    top_k=top_k,
                    cache_key=cache_key,
                    kb_version=kb_version,
                    profile=profile,
                )
        else:
            return await self._rag_fallback_with_cache(
                query=query,
                organ=organ,
                top_k=top_k,
                cache_key=cache_key,
                kb_version=kb_version,
                profile=profile,
            )

    async def _rag_fallback_with_cache(
        self,
        query: str,
        organ: Organ,
        top_k: int,
        cache_key: str,
        kb_version: str,
        profile: Any = None,
    ) -> list[KnowledgeChunk]:
        """实际 RAG 调用 + 写回缓存（含穿透防护 + 雪崩防护）。

        注：本方法**不**加锁，由 ``query()`` 在锁内调用；
        也作为锁路径异常时的回退（best-effort，重复 RAG 调用是
        可接受的"过载"而非"丢数据"）。
        """
        # 二次检查：可能并发线程/任务已填充缓存
        cached, is_neg = await self._read_redis(cache_key)
        if cached is not None:
            self._hits_redis += 1
            return cached[:top_k]
        cached, is_neg = self._read_local(cache_key)
        if cached is not None:
            self._hits_local += 1
            return cached[:top_k]

        self._misses += 1
        start = time.monotonic()
        chunks = await self._call_rag(query, organ, top_k, profile)
        is_negative = len(chunks) == 0

        # 写回缓存（失败不阻塞业务）。
        # 顺序：先写 L2（同步、立即可见），再写 L1（异步）。这样在并发
        # 击穿防护场景下，任务 A 释放锁后，任务 B 二次检查 ``_read_local``
        # 一定能看到 L2 命中，避免重复 RAG 调用。
        self._write_local(cache_key, chunks, is_negative=is_negative)
        await self._write_redis(cache_key, chunks, is_negative=is_negative)
        logger.debug(
            "knowledge_cache_miss_rag_latency_ms",
            key=cache_key,
            kb_version=kb_version,
            latency_ms=round((time.monotonic() - start) * 1000.0, 2),
            n_results=len(chunks),
            is_negative=is_negative,
        )
        return chunks[:top_k]

    async def health_check(self) -> bool:
        """RAG 健康即视为本智能体健康；Redis 健康是 bonus。"""
        try:
            return await self._rag.health_check()
        except Exception as exc:
            logger.warning(
                "knowledge_rag_health_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return False

    async def close(self) -> None:
        """释放 L2 缓存 + 清理击穿锁字典（Redis 连接由全局 ``RedisPool.close()`` 负责）。"""
        try:
            self._local_cache.clear()
        except Exception as exc:
            logger.warning(
                "knowledge_close_local_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        try:
            async with self._key_locks_guard:
                self._key_locks.clear()
        except Exception as exc:
            logger.warning(
                "knowledge_close_locks_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
        # 安全读取 local_cache_size（用于结构化日志；缓存损坏时不影响清理结果）
        try:
            cache_size = len(self._local_cache)
        except Exception:
            cache_size = -1
        logger.info(
            "knowledge_closed",
            local_cache_size=cache_size,
        )

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
            "version_mismatch_count": self._version_mismatch_count,
            "local_cache_size": len(self._local_cache),
            "local_cache_capacity": self._local_cache.maxsize,
            "redis_ttl_seconds": self._redis_ttl,
            "current_kb_version": self._current_kb_version(),
            "negative_cache_hits": self._negative_cache_hits,
            "breakdown_lock_wait_count": self._breakdown_lock_wait_count,
        }

    def clear_local_cache(self) -> None:
        """清空进程内 LRU 缓存（用于测试和运维）。"""
        self._local_cache.clear()


__all__ = [
    "DEFAULT_KB_VERSION",
    "DEFAULT_KEY_LOCK_TIMEOUT_SECONDS",
    "DEFAULT_LOCAL_CACHE_CAPACITY",
    "DEFAULT_LOCAL_CACHE_TTL_SECONDS",
    "DEFAULT_NEGATIVE_CACHE_TTL_SECONDS",
    "DEFAULT_REDIS_TTL_SECONDS",
    "DEFAULT_TTL_JITTER_RATIO",
    "KnowledgeAgentImpl",
    "_TTLLRUCache",
]
