"""A.50.7 E12 摸排修复：幂等键并发竞态（check-then-set → 原子 SET NX）。

修复前：并发双请求同 idempotency_key 都在 GET 时 miss → 各自生成 request_id
落库入队（真机探针实测 200/200 两个 id）——幂等键防双击/重试的意义尽失。
修复：miss 分支改 SET NX 原子占位，占位失败者读占位值返回对手的 request_id。
"""
from __future__ import annotations

import pytest

from app.config.redis import RedisPool

pytestmark = pytest.mark.track_api


class _FakeRedisClient:
    """最小 SET 语义替身：记录值，NX 按 key 存在性判定。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key: str, value: str, nx: bool = False, ex: int | None = None):
        if nx and key in self.store:
            return None  # redis-py：NX 失败返回 None
        self.store[key] = value
        return True

    async def setex(self, key: str, ttl: int, value: str):
        self.store[key] = value
        return True


@pytest.mark.asyncio
async def test_set_nx_first_write_wins(monkeypatch: pytest.MonkeyPatch):
    fake = _FakeRedisClient()

    async def _get_client():
        return fake

    monkeypatch.setattr(RedisPool, "get_client", classmethod(lambda cls: _get_client()))

    assert await RedisPool.set_nx("idem:1", 600, "first") is True
    assert await RedisPool.set_nx("idem:1", 600, "second") is False
    assert fake.store["idem:1"] == "first"  # 后写不得覆盖占位


@pytest.mark.asyncio
async def test_set_nx_redis_error_returns_false(monkeypatch: pytest.MonkeyPatch):
    """Redis 故障降级为 False（调用方走 miss 分支正常生成——fail-open 语义）。"""
    class _BrokenClient:
        async def set(self, *a, **k):
            raise RuntimeError("down")

    monkeypatch.setattr(RedisPool, "get_client", classmethod(lambda cls: _BrokenClient()))
    assert await RedisPool.set_nx("idem:2", 600, "v") is False


class TestEndpointSourceOrdering:
    def test_miss_branch_uses_atomic_nx(self):
        """源码守卫：幂等 miss 分支必须走 SET NX 占位，不得只有 setex。"""
        import inspect
        from pathlib import Path

        src = Path(
            inspect.getfile(__import__("app.api.v1.diagnosis", fromlist=["x"]))
        ).read_text(encoding="utf-8")
        assert "RedisPool.set_nx(" in src, "幂等 miss 分支缺原子占位（并发竞态回归）"
        # 占位值必须含 request_id（失败者要能读对手 id 返回）
        placeholder_block = src[src.find("placeholder = json.dumps"):]
        assert '"request_id": request_id' in placeholder_block[:200]
