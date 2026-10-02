"""KnowledgeAgentImpl 周边：TTL 缓存边界 / 构造器校验 / 档位归一化。"""
from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.knowledge import (
    KnowledgeAgentImpl,
    _TTLLRUCache,
)
from tests.unit.agt.conftest import FakeRAGService


class TestTTLLRUCache:
    def test_invalid_args(self):
        with pytest.raises(ValueError, match="maxsize"):
            _TTLLRUCache(0, 10)
        with pytest.raises(ValueError, match="ttl_seconds"):
            _TTLLRUCache(10, 0)

    def test_getitem_expired_or_missing_raises_keyerror(self):
        c = _TTLLRUCache(4, 10)
        with pytest.raises(KeyError):
            c["missing"]
        c["k"] = "v"
        # 手动拨过期
        c._expires["k"] = time.monotonic() - 1
        with pytest.raises(KeyError):
            c["k"]

    def test_delitem(self):
        c = _TTLLRUCache(4, 10)
        c["k"] = "v"
        del c["k"]
        assert "k" not in c

    def test_items_skips_expired(self):
        c = _TTLLRUCache(4, 10)
        c["alive"] = 1
        c["dead"] = 2
        c._expires["dead"] = time.monotonic() - 1
        assert dict(c.items()) == {"alive": 1}

    def test_contains_lazy_evict(self):
        c = _TTLLRUCache(4, 10)
        c["k"] = "v"
        c._expires["k"] = time.monotonic() - 1
        assert "k" not in c
        assert len(c) == 0


def _make_agent(**kw):
    base = dict(
        rag_service=FakeRAGService(),
        redis_client=MagicMock(),
        kb_version_resolver=lambda: "kb_v1.3.0",
        redis_ttl_seconds=60,
        local_cache_capacity=10,
        local_cache_ttl_seconds=30,
    )
    base.update(kw)
    return KnowledgeAgentImpl(**base)


class TestConstructorValidation:
    @pytest.mark.parametrize(
        "kw, match",
        [
            ({"negative_cache_ttl_seconds": 0}, "negative_cache_ttl_seconds"),
            ({"key_lock_timeout_seconds": 0}, "key_lock_timeout_seconds"),
            (
                {
                    "negative_cache_ttl_seconds": 120,
                },
                "negative cache TTL",
            ),
        ],
    )
    def test_invalid_configs(self, kw, match):
        params = {**kw}
        if match == "negative cache TTL":
            params["negative_cache_ttl_seconds"] = 120
            params["redis_ttl_seconds"] = 60
        with pytest.raises(ValueError, match=match):
            _make_agent(**params)

    def test_negative_cache_ttl_above_redis_rejected(self):
        with pytest.raises(ValueError, match="negative cache TTL"):
            _make_agent(redis_ttl_seconds=60, negative_cache_ttl_seconds=120)


class TestNormalizeProfile:
    def test_object_with_as_retrieval_kwargs(self):
        profile = SimpleNamespace(
            as_retrieval_kwargs=lambda: {"top_k": 5, "full_validation": True}
        )
        out = KnowledgeAgentImpl._normalize_profile(profile)
        assert out == {"top_k": 5}  # 非检索字段被丢弃

    def test_object_raises_returns_none(self):
        def boom():
            raise RuntimeError("bad profile")

        profile = SimpleNamespace(as_retrieval_kwargs=boom)
        assert KnowledgeAgentImpl._normalize_profile(profile) is None

    def test_falsy_returns_none(self):
        assert KnowledgeAgentImpl._normalize_profile(None) is None
        assert KnowledgeAgentImpl._normalize_profile({}) is None


class TestLocalWriteEdge:
    def test_write_local_none_chunks_early_return(self):
        agent = _make_agent()
        agent._write_local("k", None)  # 不应抛
        assert "k" not in agent._local_cache
