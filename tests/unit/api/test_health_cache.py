"""共享缓存接线与健康信息（二期 §4.2）。

不变量：
1. 缓存实例可被健康检查拿到（此前是 lifespan 里内联构造，没有任何访问点）；
2. 缓存未启用时健康检查报 disabled，而不是假装 ok；
3. 缓存不可用**不参与**整体健康判定——它是优化项，不是正确性依赖；
4. 缓存键含 kb_version：知识库升级后旧条目自然失效，无需手工清理。
"""
import pytest

from app.rag.cache import (
    RetrievalCache,
    get_retrieval_cache,
    reset_retrieval_cache,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_retrieval_cache()
    yield
    reset_retrieval_cache()


class TestRegistry:
    def test_construction_registers_instance(self):
        cache = RetrievalCache(None)

        assert get_retrieval_cache() is cache

    def test_reset_clears(self):
        RetrievalCache(None)

        reset_retrieval_cache()

        assert get_retrieval_cache() is None


class TestHealthPayload:
    def test_without_redis_reports_no_shared_layer(self):
        cache = RetrievalCache(None)

        payload = cache.health()

        assert payload["redis_configured"] is False
        assert payload["shared_layer"] == "none"
        assert payload["local_capacity"] is not None

    def test_with_redis_reports_shared_layer(self):
        cache = RetrievalCache(object())

        payload = cache.health()

        assert payload["redis_configured"] is True
        assert payload["shared_layer"] == "redis"

    def test_payload_shape(self):
        payload = RetrievalCache(None).health()

        assert set(payload) == {
            "redis_configured",
            "local_entries",
            "local_capacity",
            "redis_ttl_seconds",
            "hit_rate",
            "hits",
            "misses",
            "shared_layer",
        }

    def test_stats_start_at_zero(self):
        payload = RetrievalCache(None).health()

        assert payload["hits"] == 0
        assert payload["misses"] == 0
        assert payload["hit_rate"] == 0.0


class TestCacheKeyVersioning:
    def _request(self):
        from app.rag.protocols import RetrievalRequest

        return RetrievalRequest(query="低回声结节")

    def test_key_includes_kb_version(self):
        request = self._request()

        first = RetrievalCache.make_key(request, "kb_v1.0.0")
        second = RetrievalCache.make_key(request, "kb_v2.0.0")

        assert "kb_v1.0.0" in first
        assert first != second, "键不含版本会让升级后读到旧条目"

    def test_key_stable_for_same_inputs(self):
        request = self._request()

        assert RetrievalCache.make_key(request, "kb_v1.0.0") == RetrievalCache.make_key(
            request, "kb_v1.0.0"
        )


class TestHealthEndpoint:
    async def test_disabled_when_no_cache(self):
        from app.api.v1.health import retrieval_cache_health_check

        payload = await retrieval_cache_health_check()

        assert payload["status"] == "disabled"

    async def test_ok_with_cache(self):
        from app.api.v1.health import retrieval_cache_health_check

        RetrievalCache(None)

        payload = await retrieval_cache_health_check()

        assert payload["status"] == "ok"
        assert payload["cache"]["shared_layer"] == "none"

    async def test_cache_status_is_not_part_of_overall_health(self, monkeypatch):
        """缓存是优化项：它好坏不该把整体健康判成 unhealthy。"""
        import app.api.v1.health as health_module

        async def _redis_ok():
            return True, None

        async def _db_ok():
            return True, None

        async def _hsm_ok():
            return {"status": "healthy"}

        monkeypatch.setattr(health_module, "_check_redis", _redis_ok)
        monkeypatch.setattr(health_module, "_check_db", _db_ok)
        monkeypatch.setattr(health_module, "get_hsm_health", _hsm_ok)

        payload = await health_module.health_check()

        assert payload["status"] == "healthy"
        assert payload["services"]["retrieval_cache"] == {"status": "disabled"}
