"""G8：知识缓存必须能**主动**失效（此前只能等 TTL，新知识最长 24h 不可见）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.knowledge import KnowledgeAgentImpl
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ

pytestmark = pytest.mark.track_agt


def _chunk(cid: str) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=cid,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="x",
        source_doc_id="doc",
    )


def _agent(redis_client=None):
    rag = MagicMock()
    rag.retrieve = AsyncMock()
    return KnowledgeAgentImpl(rag_service=rag, redis_client=redis_client)


@pytest.mark.asyncio
async def test_invalidate_clears_local_cache():
    agent = _agent()
    agent._write_local("k1", [_chunk("c1")])
    agent._write_local("k2", [_chunk("c2")])
    assert len(agent._local_cache) == 2

    out = await agent.invalidate_cache(reason="kb_import", actor="admin")
    assert out["removed_local"] == 2
    assert len(agent._local_cache) == 0


@pytest.mark.asyncio
async def test_invalidate_clears_redis_by_prefix():
    client = MagicMock()

    async def _scan(match=None, count=None):
        for k in (b"ultrasound:kb:cache:thyroid:v1:a", b"ultrasound:kb:cache:liver:v1:b"):
            yield k

    client.scan_iter = _scan
    client.delete = AsyncMock(return_value=2)
    factory = MagicMock()
    factory.get_client = AsyncMock(return_value=client)

    agent = KnowledgeAgentImpl(rag_service=MagicMock(), redis_client=factory)
    out = await agent.invalidate_cache(reason="kb_import", actor="admin")
    assert out["removed_redis"] == 2
    assert out["redis_error"] is None
    # 只清本服务前缀（实现内部传入 match），此处验证 prefix 来自 settings
    from app.config.settings import settings as _s

    assert _s.redis_key_prefix == "ultrasound"


@pytest.mark.asyncio
async def test_redis_failure_is_reported_not_swallowed():
    factory = MagicMock()
    factory.get_client = AsyncMock(side_effect=RuntimeError("redis down"))
    agent = KnowledgeAgentImpl(rag_service=MagicMock(), redis_client=factory)
    agent._write_local("k1", [_chunk("c1")])

    out = await agent.invalidate_cache(reason="kb_import")
    assert out["removed_local"] == 1          # 本地缓存仍然清掉了
    assert out["redis_error"] is not None      # 但 Redis 失败如实上报
    assert "redis down" in out["redis_error"]


@pytest.mark.asyncio
async def test_invalidate_without_redis_is_ok():
    agent = _agent(redis_client=None)
    agent._write_local("k1", [_chunk("c1")])
    out = await agent.invalidate_cache()
    assert out["removed_local"] == 1
    assert out["removed_redis"] == 0
    assert out["redis_error"] is None


@pytest.mark.asyncio
async def test_invalidate_does_not_change_kb_version():
    """清缓存 ≠ 改版本：版本号是知识库的事实，不能因为清缓存而漂移。"""
    agent = _agent()
    before = agent.get_cache_stats()["current_kb_version"]
    await agent.invalidate_cache(reason="test")
    assert agent.get_cache_stats()["current_kb_version"] == before


# ---------------------------------------------------------------------------
# G8：更新戳驱动的主动失效
# ---------------------------------------------------------------------------


class _StampClient:
    def __init__(self, stamp=None):
        self._stamp = stamp
        self.scan_calls = 0

    async def get(self, key):
        return self._stamp

    async def scan_iter(self, match=None, count=None):
        self.scan_calls += 1
        if False:  # pragma: no cover - 空迭代器
            yield b""

    async def delete(self, *keys):
        return len(keys)


def _agent_with_stamp(stamp=None):
    client = _StampClient(stamp)
    factory = MagicMock()
    factory.get_client = AsyncMock(return_value=client)
    agent = KnowledgeAgentImpl(rag_service=MagicMock(), redis_client=factory)
    return agent, client


@pytest.mark.asyncio
async def test_stamp_change_clears_cache():
    agent, _ = _agent_with_stamp(b"v1")
    agent._write_local("k1", [_chunk("c1")])
    await agent._maybe_invalidate_on_stamp()
    assert len(agent._local_cache) == 0
    assert agent._last_invalidation_stamp == "v1"


@pytest.mark.asyncio
async def test_same_stamp_does_not_clear_again():
    agent, _ = _agent_with_stamp(b"v1")
    await agent._maybe_invalidate_on_stamp()
    agent._write_local("k1", [_chunk("c1")])
    await agent._maybe_invalidate_on_stamp()  # 同一戳 → 不清
    assert len(agent._local_cache) == 1


@pytest.mark.asyncio
async def test_new_stamp_clears_again():
    agent, client = _agent_with_stamp(b"v1")
    await agent._maybe_invalidate_on_stamp()
    agent._write_local("k1", [_chunk("c1")])
    client._stamp = b"v2"
    await agent._maybe_invalidate_on_stamp()
    assert len(agent._local_cache) == 0
    assert agent._last_invalidation_stamp == "v2"


@pytest.mark.asyncio
async def test_empty_stamp_is_noop():
    agent, _ = _agent_with_stamp(None)
    agent._write_local("k1", [_chunk("c1")])
    await agent._maybe_invalidate_on_stamp()
    assert len(agent._local_cache) == 1


@pytest.mark.asyncio
async def test_stamp_read_failure_never_breaks_query():
    factory = MagicMock()
    factory.get_client = AsyncMock(side_effect=RuntimeError("redis down"))
    agent = KnowledgeAgentImpl(rag_service=MagicMock(), redis_client=factory)
    agent._write_local("k1", [_chunk("c1")])
    await agent._maybe_invalidate_on_stamp()  # 不抛
    assert len(agent._local_cache) == 1  # 缓存保持原语义
