"""RAG 检索结果缓存单测（键设计 / 载荷裁剪 / 降级 / 服务集成）。"""
import json

import pytest

from app.knowledge.protocols import KnowledgeChunk
from app.rag.cache import (
    CACHE_KEY_PREFIX,
    NEGATIVE_MARKER,
    RetrievalCache,
    _LocalTTLCache,
)
from app.rag.protocols import (
    EnhancedQuery,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)
from app.rag.reranker import HeuristicReranker
from app.rag.service import RAGServiceImpl

pytestmark = pytest.mark.track_rag

KB = "kb_v1.0.0"


# ---------------- 测试替身 ----------------
def _chunk(chunk_id="c1", with_embedding=True):
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version=KB,
        organ="thyroid",
        content=f"TI-RADS 内容 {chunk_id}",
        metadata={"classifications": ["TI-RADS 4A"]},
        embedding=[0.1] * 8 if with_embedding else None,
        source_doc_id="doc1",
    )


def _scored(chunk_id="c1", score=0.9):
    return ScoredChunk(
        chunk=_chunk(chunk_id),
        score=score,
        rerank_score=score,
        retrieval_source=RetrievalMethod.HYBRID,
    )


def _result(chunks=None, method=RetrievalMethod.HYBRID):
    return RetrievalResult(chunks=chunks if chunks is not None else [_scored()],
                           retrieval_method=method)


def _request(**overrides):
    base = dict(query="甲状腺结节 TI-RADS 4A", organ="thyroid", top_k=20,
                min_score_threshold=0.0)
    base.update(overrides)
    return RetrievalRequest(**base)


class _FakeRedisClient:
    def __init__(self, store=None, fail=False):
        self.store = store if store is not None else {}
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        if self.fail:
            raise ConnectionError("redis down")
        self.store[key] = value


class _FakeRedisFactory:
    def __init__(self, client):
        self._client = client
        self.calls = 0

    async def get_client(self):
        self.calls += 1
        return self._client


class _BrokenFactory:
    async def get_client(self):
        raise RuntimeError("cannot connect")


# ---------------- 键设计 ----------------
class TestCacheKey:
    def test_key_contains_organ_and_kb_version(self):
        key = RetrievalCache.make_key(_request(), KB)

        assert key.startswith(f"{CACHE_KEY_PREFIX}:thyroid:v{KB}:")

    def test_key_stable_for_identical_request(self):
        assert RetrievalCache.make_key(_request(), KB) == RetrievalCache.make_key(_request(), KB)

    def test_trace_ids_do_not_affect_key(self):
        first = _request(request_id="req_1", session_id="sess_1")
        second = _request(request_id="req_2", session_id="sess_2")

        assert RetrievalCache.make_key(first, KB) == RetrievalCache.make_key(second, KB)

    @pytest.mark.parametrize(
        "overrides",
        [
            {"query": "另一个问题"},
            {"organ": "liver"},
            {"top_k": 5},
            {"use_reranking": False},
            {"use_query_enhancement": False},
            {"use_bm25_fallback": False},
            {"use_multimodal": False},
            {"use_llm_enhancement": True},
            {"min_score_threshold": 0.7},
            {"image_format": "png"},
            {"extra": {"filter": "a"}},
        ],
    )
    def test_semantically_different_requests_get_different_keys(self, overrides):
        assert RetrievalCache.make_key(_request(**overrides), KB) != RetrievalCache.make_key(
            _request(), KB
        )

    def test_kb_version_change_invalidates_key(self):
        assert RetrievalCache.make_key(_request(), "kb_v1.0.0") != RetrievalCache.make_key(
            _request(), "kb_v2.0.0"
        )

    def test_extra_dict_order_is_irrelevant(self):
        first = _request(extra={"a": 1, "b": 2})
        second = _request(extra={"b": 2, "a": 1})

        assert RetrievalCache.make_key(first, KB) == RetrievalCache.make_key(second, KB)

    def test_organ_none_uses_all(self):
        key = RetrievalCache.make_key(_request(organ=None), KB)

        assert f"{CACHE_KEY_PREFIX}:all:v{KB}:" in key


class TestCacheability:
    def test_frame_requests_not_cacheable(self):
        request = _request(image_data=b"\x00\x01")

        assert RetrievalCache.is_cacheable(request) is False

    def test_text_requests_cacheable(self):
        assert RetrievalCache.is_cacheable(_request()) is True


# ---------------- L2 本地层 ----------------
class TestLocalLayer:
    @pytest.mark.asyncio
    async def test_set_then_get_hits_local(self):
        cache = RetrievalCache()
        key = "k1"

        await cache.set(key, _result())
        hit = await cache.get(key)

        assert hit is not None
        assert len(hit.chunks) == 1
        assert cache.stats()["hits_local"] == 1

    @pytest.mark.asyncio
    async def test_miss_counted(self):
        cache = RetrievalCache()

        assert await cache.get("absent") is None
        assert cache.stats()["misses"] == 1

    @pytest.mark.asyncio
    async def test_embedding_excluded_from_cached_payload(self):
        cache = RetrievalCache()
        key = "k1"
        assert _result().chunks[0].chunk.embedding is not None

        await cache.set(key, _result())
        hit = await cache.get(key)

        # 契约：命中返回的 chunk.embedding 恒为 None（载荷裁剪）
        assert hit.chunks[0].chunk.embedding is None
        # 其余字段完整保留
        assert hit.chunks[0].chunk.content.startswith("TI-RADS")
        assert hit.chunks[0].chunk.metadata["classifications"] == ["TI-RADS 4A"]
        assert hit.chunks[0].score == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_cached_object_not_aliased(self):
        cache = RetrievalCache()
        await cache.set("k1", _result())
        first = await cache.get("k1")

        first.chunks.append(_scored("c2"))
        first.chunks[0].chunk.content = "被调用方改写"

        second = await cache.get("k1")
        assert len(second.chunks) == 1
        assert second.chunks[0].chunk.content != "被调用方改写"

    @pytest.mark.asyncio
    async def test_negative_cache_roundtrip(self):
        cache = RetrievalCache()
        await cache.set("k1", RetrievalResult(chunks=[]))

        hit = await cache.get("k1")

        assert hit is not None
        assert hit.chunks == []
        assert cache.stats()["negative_hits"] == 1

    @pytest.mark.asyncio
    async def test_unserializable_payload_skips_store(self):
        cache = RetrievalCache()
        bad = _result()
        bad.chunks[0].chunk.metadata = {"obj": object()}  # 无法 JSON 序列化

        await cache.set("k1", bad)

        assert cache.stats()["store_skipped"] == 1
        assert await cache.get("k1") is None

    def test_local_ttl_expiry(self):
        local = _LocalTTLCache(maxsize=2, ttl_seconds=0.01)
        local.set("k", "v")

        assert local.get("k") == "v"

        import time as _time

        _time.sleep(0.02)
        assert local.get("k") is None

    def test_local_lru_eviction(self):
        local = _LocalTTLCache(maxsize=2, ttl_seconds=60)
        local.set("a", 1)
        local.set("b", 2)
        local.set("c", 3)

        assert local.get("a") is None
        assert local.get("c") == 3

    def test_invalid_local_params_rejected(self):
        with pytest.raises(ValueError):
            _LocalTTLCache(maxsize=0, ttl_seconds=1)
        with pytest.raises(ValueError):
            _LocalTTLCache(maxsize=1, ttl_seconds=0)


# ---------------- L1 Redis 层 ----------------
class TestRedisLayer:
    @pytest.mark.asyncio
    async def test_redis_hit_and_backfill(self):
        client = _FakeRedisClient()
        factory = _FakeRedisFactory(client)
        cache = RetrievalCache(factory, local_capacity=8)
        await cache.set("k1", _result())
        assert "k1" in client.store

        cache.clear_local()  # 强制走 L1
        hit = await cache.get("k1")

        assert hit is not None
        assert cache.stats()["hits_redis"] == 1
        # L1 命中应回填 L2
        assert cache.stats()["local_entries"] == 1

    @pytest.mark.asyncio
    async def test_redis_down_degrades_to_miss_without_raising(self):
        cache = RetrievalCache(_FakeRedisFactory(_FakeRedisClient(fail=True)))

        assert await cache.get("k1") is None
        assert cache.stats()["errors"] >= 1

        # 写入也不抛
        await cache.set("k1", _result())
        assert cache.stats()["stores"] == 1

    @pytest.mark.asyncio
    async def test_broken_factory_degrades(self):
        cache = RetrievalCache(_BrokenFactory())

        assert await cache.get("k1") is None

    @pytest.mark.asyncio
    async def test_corrupt_value_treated_as_miss(self):
        client = _FakeRedisClient(store={"k1": "{not-json"})
        cache = RetrievalCache(_FakeRedisFactory(client))

        assert await cache.get("k1") is None
        assert cache.stats()["errors"] >= 1

    @pytest.mark.asyncio
    async def test_negative_marker_in_redis(self):
        client = _FakeRedisClient(store={"k1": json.dumps([NEGATIVE_MARKER])})
        cache = RetrievalCache(_FakeRedisFactory(client))

        hit = await cache.get("k1")

        assert hit is not None
        assert hit.chunks == []

    @pytest.mark.asyncio
    async def test_ttl_jitter_applied(self):
        cache = RetrievalCache(redis_ttl_seconds=1000, jitter_ratio=0.5)
        ttls = {cache._ttl_for(is_negative=False) for _ in range(30)}

        # 抖动应产生多个不同取值，且落在 base ±50% 区间内
        assert len(ttls) > 1
        assert all(500 <= ttl <= 1500 for ttl in ttls)

    @pytest.mark.asyncio
    async def test_negative_ttl_used_for_empty_result(self):
        cache = RetrievalCache(negative_ttl_seconds=7, jitter_ratio=0.0)

        assert cache._ttl_for(is_negative=True) == 7
        assert cache._ttl_for(is_negative=False) == cache._redis_ttl


class TestStats:
    def test_hit_rate(self):
        cache = RetrievalCache()

        assert cache.stats()["hit_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_hit_rate_after_hits(self):
        cache = RetrievalCache()
        await cache.set("k1", _result())
        await cache.get("k1")
        await cache.get("absent")

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_stats_shape(self):
        cache = RetrievalCache()
        payload = cache.stats()

        for key in ("hits_redis", "hits_local", "misses", "negative_hits",
                    "stores", "store_skipped", "errors", "hit_rate", "local_entries"):
            assert key in payload


# ---------------- 服务集成 ----------------
class _StubEnhancer:
    def __init__(self):
        self.calls = 0

    async def enhance(self, query, organ_hint=None):
        self.calls += 1
        return EnhancedQuery(original=query, enhanced=f"{query} 扩展", organ=organ_hint)


class _StubRetriever:
    def __init__(self):
        self.calls = 0

    async def retrieve(self, request, enhanced_query=None):
        self.calls += 1
        return RetrievalResult(
            chunks=[_scored()],
            retrieval_method=RetrievalMethod.HYBRID,
            enhanced_query=enhanced_query,
        )

    async def health_check(self):
        return {"embedding": True, "vector_index": True}


class _StubReranker:
    async def rerank(self, query, chunks, top_k):
        return [
            ScoredChunk(
                chunk=chunk,
                score=0.95,
                rerank_score=0.95,
                retrieval_source=RetrievalMethod.HYBRID,
            )
            for chunk in chunks[:top_k]
        ]


class _StubBM25:
    def should_trigger(self, result):
        return False

    def trigger_reason(self, result):
        return "not-triggered"

    async def fallback_search(self, query, top_k):
        return []


def _service(cache=None, retriever=None, enhancer=None):
    return RAGServiceImpl(
        query_enhancer=enhancer or _StubEnhancer(),
        retriever=retriever or _StubRetriever(),
        reranker=_StubReranker(),
        heuristic_reranker=HeuristicReranker(),
        bm25_fallback=_StubBM25(),
        kb_version=KB,
        cache=cache,
    )


class TestServiceIntegration:
    @pytest.mark.asyncio
    async def test_second_identical_request_skips_pipeline(self):
        retriever = _StubRetriever()
        enhancer = _StubEnhancer()
        service = _service(cache=RetrievalCache(), retriever=retriever,
                              enhancer=enhancer)

        first = await service.retrieve(_request())
        second = await service.retrieve(_request())

        assert retriever.calls == 1   # 第二次命中缓存
        assert enhancer.calls == 1
        assert len(first.chunks) == 1
        assert len(second.chunks) == 1
        assert service.cache_stats()["hits_local"] == 1

    @pytest.mark.asyncio
    async def test_different_organ_not_shared(self):
        retriever = _StubRetriever()
        service = _service(cache=RetrievalCache(), retriever=retriever)

        await service.retrieve(_request())
        await service.retrieve(_request(organ="liver"))

        assert retriever.calls == 2

    @pytest.mark.asyncio
    async def test_image_request_bypasses_cache(self):
        retriever = _StubRetriever()
        service = _service(cache=RetrievalCache(), retriever=retriever)
        frame_request = _request(image_data=b"\x00\x01")

        await service.retrieve(frame_request)
        await service.retrieve(frame_request)

        assert retriever.calls == 2

    @pytest.mark.asyncio
    async def test_cache_hit_recomputes_latency_fields(self):
        service = _service(cache=RetrievalCache())
        await service.retrieve(_request())

        hit = await service.retrieve(_request())

        # 命中路径必须用本次耗时覆盖，避免 P99 统计失真
        assert hit.latency_ms >= 0.0
        assert hit.p99_violation is False

    @pytest.mark.asyncio
    async def test_cache_disabled_keeps_previous_behaviour(self):
        retriever = _StubRetriever()
        service = _service(cache=None, retriever=retriever)

        await service.retrieve(_request())
        await service.retrieve(_request())

        assert retriever.calls == 2
        assert service.cache_stats() is None

    @pytest.mark.asyncio
    async def test_kb_version_change_bypasses_cache(self):
        retriever = _StubRetriever()
        service = _service(cache=RetrievalCache(), retriever=retriever)

        await service.retrieve(_request())
        service.update_kb_version("kb_v2.0.0")
        await service.retrieve(_request())

        assert retriever.calls == 2

    @pytest.mark.asyncio
    async def test_cached_result_not_aliased_across_calls(self):
        service = _service(cache=RetrievalCache())
        await service.retrieve(_request())

        first = await service.retrieve(_request())
        first.chunks.clear()
        second = await service.retrieve(_request())

        assert len(second.chunks) == 1

    @pytest.mark.asyncio
    async def test_min_score_threshold_part_of_key(self):
        retriever = _StubRetriever()
        service = _service(cache=RetrievalCache(), retriever=retriever)

        await service.retrieve(_request(min_score_threshold=0.0))
        await service.retrieve(_request(min_score_threshold=0.99))

        assert retriever.calls == 2

    @pytest.mark.asyncio
    async def test_empty_result_cached_as_negative(self):
        class _EmptyRetriever(_StubRetriever):
            async def retrieve(self, request, enhanced_query=None):
                self.calls += 1
                return RetrievalResult(chunks=[], retrieval_method=RetrievalMethod.HYBRID)

        retriever = _EmptyRetriever()
        service = _service(cache=RetrievalCache(), retriever=retriever)

        first = await service.retrieve(_request())
        second = await service.retrieve(_request())

        assert first.chunks == []
        assert second.chunks == []
        assert retriever.calls == 1
