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
    DEFAULT_LOCAL_CACHE_CAPACITY,
    DEFAULT_REDIS_TTL_SECONDS,
    KnowledgeAgentImpl,
)
from app.models.schemas import Organ
from app.rag.protocols import RAGError, RAGErrorCode, RetrievalRequest

from tests.unit.agt.conftest import FakeRAGService, FakeRedis, make_chunk


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

    def test_valid_construction(self):
        rag = FakeRAGService()
        agent = KnowledgeAgentImpl(rag_service=rag, redis_client=None)
        assert agent is not None


# ----------------------------------------------------------------------
# Cache key
# ----------------------------------------------------------------------


class TestCacheKey:
    def test_deterministic(self):
        k1 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "甲状腺")
        k2 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "甲状腺")
        assert k1 == k2

    def test_organ_specific(self):
        k_t = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "x")
        k_l = KnowledgeAgentImpl._make_cache_key(Organ.LIVER, "x")
        assert k_t != k_l
        assert "thyroid" in k_t
        assert "liver" in k_l

    def test_query_specific(self):
        k1 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "alpha")
        k2 = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "beta")
        assert k1 != k2

    def test_key_format(self):
        k = KnowledgeAgentImpl._make_cache_key(Organ.CARDIAC, "cardiac")
        assert k.startswith("kb:cache:cardiac:")
        # sha1 16 hex chars
        assert len(k.split(":")[-1]) == 16


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
        cache_key = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, query)
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
        cache_key = KnowledgeAgentImpl._make_cache_key(Organ.THYROID, "q")
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
            redis_ttl_seconds=42,
        )
        await agent.query("q", Organ.THYROID)
        assert seen_ttls == [42]


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
        assert DEFAULT_REDIS_TTL_SECONDS == 300
        assert DEFAULT_LOCAL_CACHE_CAPACITY == 100
