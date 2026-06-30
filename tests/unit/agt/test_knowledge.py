"""Unit tests for app.agents.knowledge (T-GA-AGT-03).

Covers:
- Constructor: invalid args rejected
- Cache key derivation: deterministic, organ-specific
- query(): L1 (Redis) hit
- query(): L2 (local LRU) hit on L1 miss
- query(): RAG fallback on full miss
- query(): Redis outage degrades to L2 only (no exception raised)
- query(): input validation (empty query, zero top_k)
- query(): chunks truncated to top_k
- health_check(): RAG healthy / unhealthy
- get_cache_stats() / clear_local_cache()
- TTL: write uses configured TTL
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.knowledge import (
    DEFAULT_KB_VERSION,
    DEFAULT_LOCAL_CACHE_CAPACITY,
    DEFAULT_LOCAL_CACHE_TTL_SECONDS,
    DEFAULT_REDIS_TTL_SECONDS,
    KnowledgeAgentImpl,
)
from app.models.schemas import Organ
from app.rag.protocols import RAGError, RAGErrorCode, RetrievalRequest

from tests.unit.agt.conftest import FakeRAGService, FakeRedis, make_chunk


def agent_kb_version(agent):
    """测试辅助：获取 agent 当前的 KB 版本号（agent 为 None 时使用默认值）。"""
    if agent is None:
        return DEFAULT_KB_VERSION
    return agent._current_kb_version()


# ----------------------------------------------------------------------
# Constructor
# ----------------------------------------------------------------------


class TestConstructor:
    def test_requires_rag(self):
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=None)

    def test_invalid_ttl_rejected(self):
        rag = FakeRAGService()
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=rag, redis_ttl_seconds=0)
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=rag, redis_ttl_seconds=-1)

    def test_invalid_capacity_rejected(self):
        rag = FakeRAGService()
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=rag, local_cache_capacity=0)
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=rag, local_cache_capacity=-1)

    def test_invalid_local_ttl_rejected(self):
        rag = FakeRAGService()
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=rag, local_cache_ttl_seconds=0)
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(rag_service=rag, local_cache_ttl_seconds=-1)

    def test_l2_ttl_must_be_le_l1_ttl(self):
        """设计文档 §2.3：L2 TTL ≤ L1 TTL（一致性约束）。"""
        rag = FakeRAGService()
        with pytest.raises(ValueError) as exc:
            KnowledgeAgentImpl(
                rag_service=rag,
                redis_ttl_seconds=60,
                local_cache_ttl_seconds=120,  # 违反约束
            )
        assert "must be" in str(exc.value)

    def test_valid_construction(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        assert agent is not None


# ----------------------------------------------------------------------
# Cache key
# ----------------------------------------------------------------------


class TestCacheKey:
    _V = "kb_v1.0.0"

    def test_deterministic(self):
        k1 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "甲状腺", self._V)
        k2 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "甲状腺", self._V)
        assert k1 == k2

    def test_organ_specific(self):
        k_t = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "x", self._V)
        k_l = KnowledgeAgentImpl._make_cache_key(Organ.LIVER, "x", self._V)
        assert k_t != k_l
        assert "thyroid" in k_t
        assert "liver" in k_l

    def test_query_specific(self):
        k1 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "alpha", self._V)
        k2 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "beta", self._V)
        assert k1 != k2

    def test_key_format(self):
        """缓存键格式：``kb:cache:{organ}:v{kb_version}:{sha1(query)[:16]}``

        与 [01-系统架构设计.md] §2.3 缓存键格式要求一致：KB 版本号嵌入键名。
        """
        k = KnowledgeAgentImpl._make_cache_key(Organ.CARDIAC, "cardiac", self._V)
        # 形如 "kb:cache:cardiac:vkb_v1.0.0:xxxxxxxxxxxxxxxx"
        parts = k.split(":")
        assert parts[0] == "kb"
        assert parts[1] == "cache"
        assert parts[2] == "cardiac"
        # 版本段以 "v" 开头
        assert parts[3].startswith("v")
        # sha1 16 hex chars
        assert len(parts[-1]) == 16
        assert k.startswith("kb:cache:cardiac:v")

    def test_version_specific(self):
        """不同 KB 版本 → 不同键 → 实现"自然过期失效"（设计文档 §2.3）。"""
        k_v1 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "q", "kb_v1.0.0")
        k_v2 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "q", "kb_v2.0.0")
        assert k_v1 != k_v2
        # 验证版本号确实嵌入键
        assert "kb_v1.0.0" in k_v1
        assert "kb_v2.0.0" in k_v2


# ----------------------------------------------------------------------
# query() — happy path / cache
# ----------------------------------------------------------------------


class TestQueryCacheMiss:
    @pytest.mark.asyncio
    async def test_redis_miss_then_rag_then_write_back(self):
        rag = FakeRAGService(chunks=[make_chunk("c1", "thyroid", "ti-rads info")])
        redis = FakeRedis()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        result = await agent.query("甲状腺结节", Organ.THYROID, top_k=5)
        assert len(result) == 1
        assert result[0].chunk_id == "c1"
        assert rag.retrieve_call_count == 1
        # Should have written to both L1 and L2
        assert redis.setex_count == 1
        stats = agent.get_cache_stats()
        assert stats["misses"] == 1
        assert stats["hits_redis"] == 0
        assert stats["hits_local"] == 0
        assert stats["local_cache_size"] == 1

    @pytest.mark.asyncio
    async def test_redis_hit_skips_rag(self):
        chunk = make_chunk("c1", "thyroid", "ti-rads info")
        rag = FakeRAGService(chunks=[chunk])
        redis = FakeRedis()

        # Pre-populate L1 (note: query string must match the upcoming query)
        query = "甲状腺结节"
        version = agent_kb_version(None)
        cache_key = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, query, version)
        payload = json.dumps([chunk.model_dump()])
        redis._store[cache_key] = payload.encode("utf-8")

        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)
        result = await agent.query(query, Organ.THYROID, top_k=5)
        assert len(result) == 1
        assert rag.retrieve_call_count == 0  # RAG not called
        stats = agent.get_cache_stats()
        assert stats["hits_redis"] == 1
        assert stats["misses"] == 0

    @pytest.mark.asyncio
    async def test_local_lru_hit_on_redis_miss(self):
        chunk = make_chunk("c1", "liver", "li-rads info")
        rag = FakeRAGService(chunks=[chunk])
        redis = FakeRedis()  # empty
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        # First call: miss → RAG → write L1 + L2
        await agent.query("肝占位", Organ.LIVER, top_k=5)
        # Wipe L1 to simulate TTL expiry; L2 still has it
        redis._store.clear()

        # Second call: L1 miss, L2 hit
        result = await agent.query("肝占位", Organ.LIVER, top_k=5)
        assert len(result) == 1
        # RAG only called once (first call)
        assert rag.retrieve_call_count == 1
        stats = agent.get_cache_stats()
        assert stats["hits_local"] == 1
        # First call was MISS on both L1 and L2 (got result via RAG)
        # Second call was MISS on L1 (we wiped it) but HIT on L2
        assert stats["misses"] == 1
        assert stats["hits_redis"] == 0

    @pytest.mark.asyncio
    async def test_top_k_truncation(self):
        chunks = [make_chunk(f"c{i}", "thyroid", f"content {i}") for i in range(10)]
        rag = FakeRAGService(chunks=chunks)
        redis = FakeRedis()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        result = await agent.query("q", Organ.THYROID, top_k=3)
        assert len(result) == 3
        assert [c.chunk_id for c in result] == ["c0", "c1", "c2"]


# ----------------------------------------------------------------------
# query() — error handling
# ----------------------------------------------------------------------


class TestQueryErrors:
    @pytest.mark.asyncio
    async def test_empty_query_rejected(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        with pytest.raises(ValueError):
            await agent.query("", Organ.THYROID)
        with pytest.raises(ValueError):
            await agent.query("   ", Organ.THYROID)

    @pytest.mark.asyncio
    async def test_zero_top_k_rejected(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        with pytest.raises(ValueError):
            await agent.query("q", Organ.THYROID, top_k=0)
        with pytest.raises(ValueError):
            await agent.query("q", Organ.THYROID, top_k=-1)

    @pytest.mark.asyncio
    async def test_redis_outage_falls_back_to_l2(self):
        # RAG returns 2 chunks
        chunks = [make_chunk(f"c{i}", "thyroid") for i in range(2)]
        rag = FakeRAGService(chunks=chunks)
        # Redis is broken on get AND set
        redis = FakeRedis(raise_on_get=True, raise_on_set=True)
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        # First call: should NOT raise, should fall back through RAG → L2
        result1 = await agent.query("q", Organ.THYROID, top_k=5)
        assert len(result1) == 2
        assert rag.retrieve_call_count == 1

        # Second call: L1 still broken, but L2 has the entry → L2 hit
        result2 = await agent.query("q", Organ.THYROID, top_k=5)
        assert len(result2) == 2
        assert rag.retrieve_call_count == 1  # RAG still only called once
        stats = agent.get_cache_stats()
        assert stats["hits_local"] == 1
        assert stats["misses"] == 1

    @pytest.mark.asyncio
    async def test_rag_error_propagates(self):
        rag = FakeRAGService(
            raise_exc=RAGError(
                error_code=RAGErrorCode.RETRIEVAL_FAILED,
                message="backend down",
            )
        )
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=FakeRedis())
        with pytest.raises(RAGError):
            await agent.query("q", Organ.THYROID)

    @pytest.mark.asyncio
    async def test_redis_value_corrupt_treated_as_miss(self):
        chunk = make_chunk("c1", "thyroid")
        rag = FakeRAGService(chunks=[chunk])
        redis = FakeRedis()
        cache_key = KnowledgeAgentImpl._make_cache_key(
            Organ.THYROID, "q", DEFAULT_KB_VERSION
        )
        redis._store[cache_key] = b"not valid json {"

        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)
        result = await agent.query("q", Organ.THYROID)
        assert len(result) == 1
        # RAG should have been called because corrupt cache = miss
        assert rag.retrieve_call_count == 1

    @pytest.mark.asyncio
    async def test_no_redis_factory_skips_l1(self):
        chunks = [make_chunk("c1", "thyroid")]
        rag = FakeRAGService(chunks=chunks)
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        result = await agent.query("q", Organ.THYROID)
        assert len(result) == 1
        assert rag.retrieve_call_count == 1
        # L2 should be populated even without L1
        stats = agent.get_cache_stats()
        assert stats["local_cache_size"] == 1


# ----------------------------------------------------------------------
# TTL
# ----------------------------------------------------------------------


class TestTTL:
    @pytest.mark.asyncio
    async def test_default_ttl(self):
        chunks = [make_chunk("c1")]
        rag = FakeRAGService(chunks=chunks)
        redis = FakeRedis()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        await agent.query("q", Organ.THYROID)
        assert redis.setex_count == 1
        # We can't directly inspect the TTL set, but we can confirm no exception

    @pytest.mark.asyncio
    async def test_custom_ttl_used(self):
        """L1 TTL 自定义值：使用满足 L1 ≥ L2 约束的值（如 60 ≥ 30）。"""
        chunks = [make_chunk("c1")]
        rag = FakeRAGService(chunks=chunks)

        # Use a tracking Redis to inspect the TTL
        seen_ttls: list = []

        class TrackingRedis(FakeRedis):
            async def setex(self, key, ttl, value):
                seen_ttls.append(ttl)
                await super().setex(key, ttl, value)

        redis = TrackingRedis()
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=redis,
            redis_ttl_seconds=60,
            local_cache_ttl_seconds=30,  # 必须 ≤ 60
            jitter_ratio=0.0,  # 显式禁用 TTL 抖动以做精确断言
        )
        await agent.query("q", Organ.THYROID)
        assert seen_ttls == [60]

    @pytest.mark.asyncio
    async def test_default_l1_ttl_is_24h(self):
        """L1 默认 TTL 必须 = 86400s（24h），与设计文档 §2.3 对齐。"""
        chunks = [make_chunk("c1")]
        rag = FakeRAGService(chunks=chunks)
        seen_ttls: list = []

        class TrackingRedis(FakeRedis):
            async def setex(self, key, ttl, value):
                seen_ttls.append(ttl)
                await super().setex(key, ttl, value)

        redis = TrackingRedis()
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=redis,
            jitter_ratio=0.0,  # 显式禁用 TTL 抖动以做精确断言
        )
        await agent.query("q", Organ.THYROID)
        assert seen_ttls == [86400]

    @pytest.mark.asyncio
    async def test_l2_ttl_eviction(self):
        """L2 TTL 过期：手动注入已过期 entry，访问时视为 miss 并重新查询 RAG。"""
        rag = FakeRAGService(chunks=[make_chunk("c1")])
        # L2 TTL = 1s 便于测试
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=None,
            local_cache_ttl_seconds=1,
        )
        # 第一次查询：RAG → L2
        await agent.query("q", Organ.THYROID)
        assert rag.retrieve_call_count == 1
        # 立即再查：L2 命中
        await agent.query("q", Organ.THYROID)
        assert rag.retrieve_call_count == 1
        # 等待 L2 过期
        import time as _t
        _t.sleep(1.1)
        # 再次查询：L2 过期 → RAG
        await agent.query("q", Organ.THYROID)
        assert rag.retrieve_call_count == 2


# ----------------------------------------------------------------------
# KB 版本校验（设计文档 §2.3 版本一致性）
# ----------------------------------------------------------------------


class TestKBVersionResolver:
    @pytest.mark.asyncio
    async def test_kb_version_in_cache_key(self):
        """KB 版本变化 → 缓存键变化 → 自然失效。"""
        # 用 v1 写入缓存
        v1_chunks = [make_chunk("c_v1", "thyroid", "v1 content")]
        v1_rag = FakeRAGService(chunks=v1_chunks)
        v1_redis = FakeRedis()
        v1_agent = KnowledgeAgentImpl(
            rag_service=v1_rag,
            redis_client=v1_redis,
            kb_version_resolver=lambda: "kb_v1.0.0",
        )
        result1 = await v1_agent.query("q", Organ.THYROID)
        assert result1[0].chunk_id == "c_v1"
        # 用 v2 模拟 KB 升级 → 旧缓存键不再命中
        v2_chunks = [make_chunk("c_v2", "thyroid", "v2 content")]
        v2_rag = FakeRAGService(chunks=v2_chunks)
        v2_redis = FakeRedis()
        # 把 v1 的缓存复制到 v2 的 redis（模拟 Redis 跨版本复用）
        v2_redis._store.update(v1_redis._store)
        v2_agent = KnowledgeAgentImpl(
            rag_service=v2_rag,
            redis_client=v2_redis,
            kb_version_resolver=lambda: "kb_v2.0.0",
        )
        result2 = await v2_agent.query("q", Organ.THYROID)
        # 应当调用 RAG 获取 v2 内容
        assert v2_rag.retrieve_call_count == 1
        assert result2[0].chunk_id == "c_v2"

    @pytest.mark.asyncio
    async def test_resolver_failure_falls_back_to_default(self):
        """resolver 抛异常 → 回退到默认版本（不阻塞业务）。"""
        chunks = [make_chunk("c1")]
        rag = FakeRAGService(chunks=chunks)
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=None,
            kb_version_resolver=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        # 不应抛异常
        result = await agent.query("q", Organ.THYROID)
        assert len(result) == 1
        # 版本应回退到默认
        assert agent._current_kb_version() == DEFAULT_KB_VERSION

    def test_get_cache_stats_includes_version(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=None,
            kb_version_resolver=lambda: "kb_v3.1.4",
        )
        stats = agent.get_cache_stats()
        assert stats["current_kb_version"] == "kb_v3.1.4"
        assert stats["redis_ttl_seconds"] == DEFAULT_REDIS_TTL_SECONDS


# ----------------------------------------------------------------------
# health_check
# ----------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_healthy(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        assert await agent.health_check() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self):
        class BrokenRag(FakeRAGService):
            async def health_check(self):
                return False
        agent = KnowledgeAgentImpl(rag_service=BrokenRag(), redis_client=None)
        assert await agent.health_check() is False

    @pytest.mark.asyncio
    async def test_rag_raises_returns_false(self):
        class CrashingRag(FakeRAGService):
            async def health_check(self):
                raise ConnectionError("rag down")
        agent = KnowledgeAgentImpl(rag_service=CrashingRag(), redis_client=None)
        assert await agent.health_check() is False


# ----------------------------------------------------------------------
# Cache management
# ----------------------------------------------------------------------


class TestCacheManagement:
    @pytest.mark.asyncio
    async def test_clear_local_cache(self):
        chunks = [make_chunk("c1"), make_chunk("c2")]
        rag = FakeRAGService(chunks=chunks)
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        await agent.query("q1", Organ.THYROID)
        await agent.query("q2", Organ.THYROID)
        assert agent.get_cache_stats()["local_cache_size"] == 2

        agent.clear_local_cache()
        assert agent.get_cache_stats()["local_cache_size"] == 0

    def test_get_cache_stats_initial(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        stats = agent.get_cache_stats()
        assert stats["hits_redis"] == 0
        assert stats["hits_local"] == 0
        assert stats["misses"] == 0
        assert stats["total"] == 0
        assert stats["hit_rate"] == 0.0
        assert stats["local_cache_size"] == 0
        assert stats["local_cache_capacity"] == DEFAULT_LOCAL_CACHE_CAPACITY

    def test_constants(self):
        """默认值必须与 [01-系统架构设计.md] §2.3 完全一致。"""
        assert DEFAULT_REDIS_TTL_SECONDS == 86400  # L1 TTL = 24h
        assert DEFAULT_LOCAL_CACHE_CAPACITY == 50  # L2 容量 ≤ 50
        assert DEFAULT_LOCAL_CACHE_TTL_SECONDS == 3600  # L2 TTL = 1h


# ----------------------------------------------------------------------
# 击穿防护 (Cache Breakdown)
# ----------------------------------------------------------------------


class TestBreakdownProtection:
    """击穿防护：同一 key 并发查询只触发一次 RAG。"""

    @pytest.mark.asyncio
    async def test_concurrent_queries_single_rag_call(self):
        """10 个并发查询同一 key → RAG 仅被调用 1 次。

        验证：击穿防护锁 + L2 写顺序优化（先 L2 后 L1）能保证
        并发任务在二次检查阶段一定能看到 L2 命中。
        """
        import asyncio

        call_count = 0
        call_lock = asyncio.Lock()

        class SlowRAG(FakeRAGService):
            async def retrieve(self, request):
                nonlocal call_count
                async with call_lock:
                    call_count += 1
                await asyncio.sleep(0.05)
                return await super().retrieve(request)

        rag = SlowRAG(chunks=[make_chunk("c1", "thyroid")])
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=None,
            enable_breakdown_lock=True,
        )

        # 10 个并发任务
        tasks = [agent.query("q", Organ.THYROID) for _ in range(10)]
        results = await asyncio.gather(*tasks)
        # 每个任务都拿到正确结果
        assert all(len(r) == 1 and r[0].chunk_id == "c1" for r in results)
        # RAG 只被调用一次（击穿防护生效）
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_different_keys_not_serialized(self):
        """不同 key 的并发查询不互相阻塞 → 各自独立命中 RAG。"""
        import asyncio

        rag = FakeRAGService(chunks=[make_chunk("c1", "thyroid")])
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        tasks = [
            agent.query(f"q_{i}", Organ.THYROID) for i in range(5)
        ]
        await asyncio.gather(*tasks)
        # 5 个不同的 key → 5 次 RAG 调用
        assert rag.retrieve_call_count == 5

    @pytest.mark.asyncio
    async def test_breakdown_lock_disabled(self):
        """``enable_breakdown_lock=False`` 时不同 key 各自打 RAG（不会因锁串行化）。

        注：相同 key 的并发查询即使 ``enable_breakdown_lock=False`` 也会因为
        L2 缓存共享而只触发一次 RAG（任务间可见性），所以用不同 key 验证。
        """
        import asyncio

        chunks = [make_chunk("c1", "thyroid")]
        rag = FakeRAGService(chunks=chunks)
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=None,
            enable_breakdown_lock=False,
        )

        # 不同 key：5 个并发 → 5 次 RAG
        tasks = [
            agent.query(f"q_{i}", Organ.THYROID) for i in range(5)
        ]
        await asyncio.gather(*tasks)
        assert rag.retrieve_call_count == 5

    def test_get_cache_stats_records_lock_wait(self):
        """``breakdown_lock_wait_count`` 在打 stats 时的存在性。"""
        agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(),
            redis_client=None,
        )
        stats = agent.get_cache_stats()
        assert "breakdown_lock_wait_count" in stats
        assert "negative_cache_hits" in stats


# ----------------------------------------------------------------------
# 穿透防护 (Cache Penetration)
# ----------------------------------------------------------------------


class TestPenetrationProtection:
    """穿透防护：RAG 空结果写入负缓存，避免重复打 RAG。"""

    @pytest.mark.asyncio
    async def test_negative_cache_written_on_empty_result(self):
        """RAG 返回空 → 负缓存写入 → 二次查询不触发 RAG。"""
        rag = FakeRAGService(chunks=[])  # 始终返回空
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)

        r1 = await agent.query("nonexistent", Organ.THYROID)
        assert r1 == []
        assert rag.retrieve_call_count == 1

        r2 = await agent.query("nonexistent", Organ.THYROID)
        assert r2 == []
        # 二次命中 L2 负缓存 → RAG 不再被调用
        assert rag.retrieve_call_count == 1

    @pytest.mark.asyncio
    async def test_negative_cache_in_redis(self):
        """负缓存同时写入 Redis。"""
        rag = FakeRAGService(chunks=[])
        redis = FakeRedis()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)

        await agent.query("nonexistent", Organ.THYROID)
        # Redis 应有负缓存条目
        assert redis.setex_count == 1
        # 检查写入的是负缓存标记
        stored = next(iter(redis._store.values()))
        assert b"__NEG__" in stored

    @pytest.mark.asyncio
    async def test_negative_cache_ttl_uses_short_ttl(self):
        """负缓存 TTL 应使用 ``negative_cache_ttl_seconds``（远小于正缓存 TTL）。"""
        seen_ttls = []

        class TrackingRedis(FakeRedis):
            async def setex(self, key, ttl, value):
                seen_ttls.append(ttl)
                await super().setex(key, ttl, value)

        rag = FakeRAGService(chunks=[])
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=TrackingRedis(),
            jitter_ratio=0.0,
            negative_cache_ttl_seconds=42,
        )
        await agent.query("nonexistent", Organ.THYROID)
        # 负缓存 TTL 必须是 42
        assert seen_ttls == [42]

    @pytest.mark.asyncio
    async def test_redis_negative_cache_hit_skips_rag(self):
        """Redis 中预置负缓存 → 二次查询不调用 RAG。"""
        # 第一次写入负缓存
        rag = FakeRAGService(chunks=[])
        redis = FakeRedis()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=redis)
        await agent.query("nonexistent", Organ.THYROID)
        assert rag.retrieve_call_count == 1

        # 第二次（L2 清除）— 模拟 L2 过期但 L1 还有负缓存
        agent.clear_local_cache()
        await agent.query("nonexistent", Organ.THYROID)
        # L1 负缓存命中 → RAG 不调用
        assert rag.retrieve_call_count == 1


# ----------------------------------------------------------------------
# 雪崩防护 (Cache Avalanche)
# ----------------------------------------------------------------------


class TestAvalancheProtection:
    """雪崩防护：写入 TTL ±jitter_ratio 随机扰动。"""

    def test_jitter_disabled_returns_base_ttl(self):
        """``jitter_ratio=0.0`` 时 TTL 等于基准。"""
        agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(),
            redis_client=None,
            jitter_ratio=0.0,
        )
        assert agent._jitter_ttl(100) == 100
        assert agent._jitter_ttl(86400) == 86400

    def test_jitter_within_ratio(self):
        """``jitter_ratio=0.1`` 时 TTL 在 [base*0.9, base*1.1] 之间。"""
        agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(),
            redis_client=None,
            jitter_ratio=0.1,
        )
        # 100 次抽样都在范围内
        for _ in range(100):
            ttl = agent._jitter_ttl(1000)
            assert 900 <= ttl <= 1100

    def test_jitter_invalid_ratio_rejected(self):
        """jitter_ratio 必须 ∈ [0.0, 1.0)。"""
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(
                rag_service=FakeRAGService(),
                redis_client=None,
                jitter_ratio=-0.1,
            )
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(
                rag_service=FakeRAGService(),
                redis_client=None,
                jitter_ratio=1.0,
            )
        with pytest.raises(ValueError):
            KnowledgeAgentImpl(
                rag_service=FakeRAGService(),
                redis_client=None,
                jitter_ratio=2.0,
            )

    @pytest.mark.asyncio
    async def test_negative_cache_ttl_jittered(self):
        """负缓存写入 TTL 也会被 jitter（用真实抽样）。"""
        seen_ttls = []

        class TrackingRedis(FakeRedis):
            async def setex(self, key, ttl, value):
                seen_ttls.append(ttl)
                await super().setex(key, ttl, value)

        rag = FakeRAGService(chunks=[])
        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=TrackingRedis(),
            jitter_ratio=0.2,  # ±20% 抖动
        )
        # 多次写入
        for i in range(20):
            await agent.query(f"miss_{i}", Organ.THYROID)
        # 至少应该有抖动效果（不全相等）
        unique_ttls = set(seen_ttls)
        assert len(unique_ttls) > 1, f"TTL should jitter, got {seen_ttls}"
        # 所有 TTL 应在 [neg_ttl*0.8, neg_ttl*1.2] 范围内
        for ttl in seen_ttls:
            assert 60 * 0.8 <= ttl <= 60 * 1.2


# ----------------------------------------------------------------------
# close() / 资源释放
# ----------------------------------------------------------------------


class TestCloseResourceRelease:
    """``close()`` 应释放 L2 缓存 + 击穿锁字典。"""

    @pytest.mark.asyncio
    async def test_close_clears_local_cache(self):
        agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(chunks=[make_chunk("c1")]),
            redis_client=None,
        )
        await agent.query("q", Organ.THYROID)
        assert agent.get_cache_stats()["local_cache_size"] == 1
        await agent.close()
        assert agent.get_cache_stats()["local_cache_size"] == 0

    @pytest.mark.asyncio
    async def test_close_clears_key_locks(self):
        agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(chunks=[make_chunk("c1")]),
            redis_client=None,
        )
        await agent.query("q", Organ.THYROID)
        # 触发锁的创建
        await agent._get_key_lock("test_key")
        assert "test_key" in agent._key_locks
        await agent.close()
        assert "test_key" not in agent._key_locks

    @pytest.mark.asyncio
    async def test_close_swallows_exceptions(self):
        """``close()`` 在清理过程中任何异常都不应抛出。"""

        class BrokenCache:
            def clear(self):
                raise RuntimeError("simulated")

        agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(),
            redis_client=None,
        )
        agent._local_cache = BrokenCache()  # type: ignore[assignment]
        # 不应抛异常
        await agent.close()


# ----------------------------------------------------------------------
# KB 版本动态获取（与 RAG service 集成）
# ----------------------------------------------------------------------


class TestKBDynamicVersion:
    """KB 版本动态获取（与 ``RAGServiceImpl.current_kb_version()`` 集成）。"""

    @pytest.mark.asyncio
    async def test_kb_version_from_rag_service(self):
        """``RAGService`` 暴露 ``current_kb_version()`` 时，缓存键使用其返回值。"""
        rag = FakeRAGService()
        rag.current_kb_version = lambda: "kb_v_from_rag"  # type: ignore[attr-defined]

        agent = KnowledgeAgentImpl(
            rag_service=rag,
            redis_client=None,
            kb_version_resolver=lambda: rag.current_kb_version(),  # type: ignore[attr-defined]
        )
        assert agent._current_kb_version() == "kb_v_from_rag"

    @pytest.mark.asyncio
    async def test_kb_version_change_invalidates_cache(self):
        """KB 版本从 v1 → v2 → 旧 L2 缓存条目不再命中。"""
        # 用 v1 写入
        v1_agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(chunks=[make_chunk("c1", "thyroid", "v1")]),
            redis_client=None,
            kb_version_resolver=lambda: "kb_v1.0.0",
        )
        await v1_agent.query("q", Organ.THYROID)

        # 用 v2 模拟 — 同一 RAG 服务但 KB 版本升级
        v2_agent = KnowledgeAgentImpl(
            rag_service=FakeRAGService(chunks=[make_chunk("c2", "thyroid", "v2")]),
            redis_client=None,
            kb_version_resolver=lambda: "kb_v2.0.0",
        )
        result = await v2_agent.query("q", Organ.THYROID)
        # v2 agent 拿不到 v1 的 L2 缓存（键不同）→ 调用 RAG 拿 v2 内容
        assert result[0].chunk_id == "c2"
        assert v2_agent.get_cache_stats()["misses"] == 1


# ----------------------------------------------------------------------
# RAG service 的 KB 版本管理 API
# ----------------------------------------------------------------------


class TestRAGServiceKBVersionAPI:
    """``RAGServiceImpl.current_kb_version()`` / ``update_kb_version()`` 接口契约。"""

    def test_current_kb_version_returns_default(self):
        """``RAGServiceImpl`` 默认从 settings 读取 KB 版本。"""
        from app.config.settings import settings as _settings
        from app.rag.service import RAGServiceImpl

        # 用 None + 仅传 None-safe 的依赖构造器 → 不行（需要更多依赖）
        # 改为直接测试默认值
        # 改用 mock 化的 dependencies 构造实例
        rag = RAGServiceImpl(
            query_enhancer=None,  # type: ignore[arg-type]
            retriever=None,  # type: ignore[arg-type]
            reranker=None,  # type: ignore[arg-type]
            heuristic_reranker=None,  # type: ignore[arg-type]
            bm25_fallback=None,  # type: ignore[arg-type]
        )
        assert rag.current_kb_version() == _settings.phase0a_mock_kb_version

    def test_update_kb_version_changes_value(self):
        from app.rag.service import RAGServiceImpl

        rag = RAGServiceImpl(
            query_enhancer=None,  # type: ignore[arg-type]
            retriever=None,  # type: ignore[arg-type]
            reranker=None,  # type: ignore[arg-type]
            heuristic_reranker=None,  # type: ignore[arg-type]
            bm25_fallback=None,  # type: ignore[arg-type]
        )
        old = rag.current_kb_version()
        # 升级到新版本
        assert rag.update_kb_version("kb_v2.0.0") is True
        assert rag.current_kb_version() == "kb_v2.0.0"
        # 相同版本 → 不变
        assert rag.update_kb_version("kb_v2.0.0") is False
        assert rag.current_kb_version() == "kb_v2.0.0"
        # 非法格式 → False
        assert rag.update_kb_version("not-a-version") is False
        assert rag.current_kb_version() == "kb_v2.0.0"
        # 空字符串 → False
        assert rag.update_kb_version("") is False
        assert rag.current_kb_version() == "kb_v2.0.0"
        # 验证回退到 v1 也能工作
        assert rag.update_kb_version("kb_v1.0.0") is True
        assert rag.current_kb_version() == "kb_v1.0.0"
        # sanity: 与初始值不同
        assert rag.current_kb_version() != old or old == "kb_v1.0.0"
