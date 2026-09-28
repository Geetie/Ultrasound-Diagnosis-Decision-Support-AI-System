"""守卫：RedisPool 必须是**异步**客户端，且 `get_client()` 永不返回 None。

## 为什么需要它（2026-09-28 真机部署暴露）

`app/config/redis.py` 原先是：

    import redis.asyncio as redis      # 别名 redis -> redis.asyncio
    import redis.backoff               # ← 把顶层同步包 redis 绑到本模块的 redis
    import redis.retry

Python 的 `import a.b` 会把**顶层包 a** 绑定到当前命名空间 → `redis.Redis.from_url(...)`
造出的是**同步**客户端。于是所有 `await client.<cmd>()` 全炸在不同形态上
（`await ping()` 得 bool、`await xreadgroup()` 得 list、`async for scan_iter()`
得 generator、`await get()` 得 None），健康检查恒 503、消费者刷屏报错、
HSM 持久化失败、WS 订阅起不来 —— 四个子系统一起坏，根因却只有这一处。

单测之所以没抓到：全程用 mock，没人真的去构造客户端。所以这里**真的构造**一个。
"""
from __future__ import annotations

import ast
import pathlib

import pytest
import redis
import redis.asyncio as aioredis

from app.config import redis as redis_module
from app.config.redis import RedisPool

# 采集"真实实现"：根 conftest 的 fast_fail_redis 会把 RedisPool.get_client 换成
# 抛异常的替身，这里在**收集阶段**先把真函数存下来，测试里再用 monkeypatch 换回。
_REAL_GET_CLIENT = RedisPool.__dict__["get_client"]


def _real_get_client(monkeypatch) -> None:
    monkeypatch.setattr(RedisPool, "get_client", classmethod(_REAL_GET_CLIENT.__func__))


class TestNoNameShadowing:
    def test_模块不再用被遮蔽的导入写法(self):
        """注释里可以讲这个坑，但**真实的 import 语句**不许再出现。

        精确规则：`import a.b` 会把顶层包 `a` 绑定到当前命名空间，而
        `import a.b as c` / `from a.b import c` 都不会。所以危险形态只有
        「不带 as 的 `import redis.<sub>`」。
        """
        tree = ast.parse(pathlib.Path(redis_module.__file__).read_text(encoding="utf-8"))
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }

        assert "redis.asyncio" in imported, "必须用显式别名导入异步客户端"
        for shadowing in ("redis.backoff", "redis.retry", "redis.exceptions"):
            assert shadowing not in imported, (
                f"`import {shadowing}` 会把顶层同步包绑定到本模块的 `redis` 名字上，"
                "覆盖 `redis.asyncio` 别名 → 造出同步客户端 → 四个子系统一起坏"
            )

    def test_同步与异步客户端确实是两个类(self):
        assert aioredis.Redis is not redis.Redis


class TestBuildClient:
    def test_构造出的是异步客户端(self, monkeypatch):
        monkeypatch.setattr(
            aioredis.Redis, "from_url", classmethod(lambda cls, url, **kw: cls(host="127.0.0.1"))
        )
        RedisPool._client = None
        try:
            client = RedisPool._build_client()

            assert isinstance(client, aioredis.Redis)
        finally:
            RedisPool._client = None

    def test_同步客户端必须被拒绝(self, monkeypatch):
        """这正是真机上发生的事：构造出同步客户端后四个子系统一起坏。"""
        monkeypatch.setattr(
            aioredis.Redis, "from_url", classmethod(lambda cls, url, **kw: redis.Redis(host="127.0.0.1"))
        )

        with pytest.raises(TypeError, match=r"redis\.asyncio\.Redis"):
            RedisPool._build_client()


class TestPingCompat:
    """`ping()` 在 redis-py 5.0.1 上解析到同步实现：异步客户端返回协程、同步客户端返回 bool。"""

    @pytest.mark.asyncio
    async def test_接受协程形态(self):
        class _AsyncPing:
            async def ping(self) -> bool:
                return True

        assert await RedisPool._ping_once(_AsyncPing()) is True

    @pytest.mark.asyncio
    async def test_接受布尔形态(self):
        class _SyncPing:
            def ping(self) -> bool:
                return True

        assert await RedisPool._ping_once(_SyncPing()) is True

    @pytest.mark.asyncio
    async def test_探活失败不抛异常(self):
        class _Broken:
            def ping(self):
                raise RuntimeError("boom")

        assert await RedisPool._is_connected(_Broken()) is False


class TestGetClientNeverNone:
    @pytest.mark.asyncio
    async def test_探活判死之后必须重建而不是返回None(self, monkeypatch):
        """原实现把 `_client` 置空后直接 `return cls._client`（None）→
        调用方炸在 `'NoneType' object has no attribute 'xreadgroup'`。"""
        _real_get_client(monkeypatch)
        monkeypatch.setattr(RedisPool, "_is_connected", classmethod(lambda cls, c: _false_async()))
        monkeypatch.setattr(RedisPool, "_client", aioredis.Redis(host="127.0.0.1"))
        try:
            client = await RedisPool.get_client()

            assert client is not None, "探活判死之后必须重建客户端"
            assert isinstance(client, aioredis.Redis)
        finally:
            RedisPool._client = None

    @pytest.mark.asyncio
    async def test_首次调用即构造(self, monkeypatch):
        _real_get_client(monkeypatch)
        monkeypatch.setattr(RedisPool, "_client", None)
        try:
            client = await RedisPool.get_client()

            assert isinstance(client, aioredis.Redis)
        finally:
            RedisPool._client = None


async def _false_async() -> bool:
    return False
