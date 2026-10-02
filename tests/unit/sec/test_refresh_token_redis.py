"""RedisRefreshTokenStore 分支覆盖测试（注入 FakeAsyncRedis，不依赖真实 Redis）。

覆盖 wiring-audit 后仍缺失的 Redis 路径：rotate 全分支、revoke 家族/全用户、
cleanup 扫描、模块级分发函数。真实 Redis 语义由服务器侧真机测试另行覆盖。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config.redis_keys import RedisKey
from app.config.settings import settings
from app.security.refresh_token import (
    RedisRefreshTokenStore,
    get_refresh_token_count,
    get_user_token_count,
    revoke_all_user_tokens,
    revoke_oldest_user_token,
    revoke_refresh_token,
    revoke_token_family,
    rotate_refresh_token,
    verify_refresh_token,
)

USER_INFO = {"username": "u1", "role": "ULTRASOUND_DOCTOR"}


class _NoopAwaitable:
    """await pipe.set(...) 与 pipe.set(...)（缓冲）两种用法的兼容垫片。"""

    def __await__(self):
        if False:
            yield
        return None


class FakePipe:
    """缓冲命令的 pipeline：命令同步入队（redis.asyncio 缓冲语义），
    被 await 时返回 no-op awaitable；execute() 统一执行。
    事务与非事务行为一致（本套测试不依赖 MULTI 原子性）。"""

    def __init__(self, redis: FakeAsyncRedis, transactional: bool):
        self.redis = redis
        self.transactional = transactional
        self.ops: list[tuple] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def set(self, key, value, ex=None):
        self.ops.append(("set", key, value, ex))
        return _NoopAwaitable()

    def setex(self, key, ttl, value):
        self.ops.append(("setex", key, ttl, value))
        return _NoopAwaitable()

    def sadd(self, key, member):
        self.ops.append(("sadd", key, member))
        return _NoopAwaitable()

    def srem(self, key, member):
        self.ops.append(("srem", key, member))
        return _NoopAwaitable()

    def expire(self, key, ttl):
        self.ops.append(("expire", key, ttl))
        return _NoopAwaitable()

    def delete(self, key):
        self.ops.append(("delete", key))
        return _NoopAwaitable()

    def get(self, key):
        self.ops.append(("get", key))
        return _NoopAwaitable()

    def ttl(self, key):
        self.ops.append(("ttl", key))
        return _NoopAwaitable()

    async def execute(self):
        results = []
        for op in self.ops:
            name = op[0]
            if name == "get":
                results.append(await self.redis.get(op[1]))
            elif name == "ttl":
                results.append(await self.redis.ttl(op[1]))
            else:
                if name == "set":
                    await self.redis.set(op[1], op[2], ex=op[3])
                elif name == "setex":
                    await self.redis.setex(op[1], op[2], op[3])
                else:
                    await getattr(self.redis, name)(*op[1:])
        self.ops.clear()
        return results


class FakeAsyncRedis:
    """最小异步 Redis 双胞胎：字符串/集合/TTL/扫描，语义对齐 redis-py asyncio。"""

    def __init__(self):
        self.strings: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.sets: dict[str, set[str]] = {}

    def pipeline(self, transaction: bool = False):
        return FakePipe(self, transaction)

    async def get(self, key):
        return self.strings.get(key)

    async def set(self, key, value, ex=None):
        self.strings[key] = value
        if ex:
            self.ttls[key] = ex
        else:
            self.ttls.pop(key, None)

    async def setex(self, key, ttl, value):
        self.strings[key] = value
        self.ttls[key] = ttl

    async def ttl(self, key):
        return self.ttls.get(key, -1)

    async def exists(self, key):
        return 1 if key in self.strings else 0

    async def delete(self, key):
        self.strings.pop(key, None)
        self.ttls.pop(key, None)
        self.sets.pop(key, None)

    async def sadd(self, key, member):
        self.sets.setdefault(key, set()).add(member)

    async def srem(self, key, member):
        s = self.sets.get(key)
        if s:
            s.discard(member)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def expire(self, key, ttl):
        if key in self.strings or key in self.sets:
            self.ttls[key] = ttl

    async def eval(self, *args):
        return 1

    async def scan_iter(self, match, count=100):
        for k in list(self.strings):
            if k.startswith(match.rstrip("*")):
                yield k


@pytest.fixture
def store():
    RedisRefreshTokenStore._instance = None
    s = RedisRefreshTokenStore()
    fake = FakeAsyncRedis()
    s._redis_client = fake
    yield s
    RedisRefreshTokenStore._instance = None


def _expired_data(user_id="u1", family_id="f1"):
    now = datetime.now(timezone.utc)
    return {
        "token_id": "t-old",
        "user_id": user_id,
        "user_info": USER_INFO,
        "family_id": family_id,
        "created_at": (now - timedelta(days=30)).isoformat(),
        "expires_at": (now - timedelta(days=1)).isoformat(),
        "revoked": False,
        "revoked_at": None,
        "last_used_at": None,
    }


@pytest.mark.anyio
class TestRotate:
    async def test_rotate_not_found_no_family(self, store):
        with pytest.raises(ValueError, match="not found"):
            await store.rotate_refresh_token("missing")

    async def test_rotate_reuse_via_family_key(self, store):
        store._redis_client.strings[RedisKey.auth_token_family("t-old")] = "f1"
        with pytest.raises(ValueError, match="already been used"):
            await store.rotate_refresh_token("t-old")

    async def test_rotate_corrupted_data(self, store):
        store._redis_client.strings[RedisKey.auth_token("t-old")] = "{bad json"
        with pytest.raises(ValueError, match="corrupted"):
            await store.rotate_refresh_token("t-old")

    async def test_rotate_revoked_with_family(self, store):
        d = dict(_expired_data(), expires_at=datetime.now(timezone.utc).isoformat())
        d["revoked"] = True
        store._redis_client.strings[RedisKey.auth_token("t-old")] = json.dumps(d)
        with pytest.raises(ValueError, match="already been used"):
            await store.rotate_refresh_token("t-old")

    async def test_rotate_revoked_no_family(self, store):
        d = dict(_expired_data(family_id=None), expires_at=datetime.now(timezone.utc).isoformat())
        d["revoked"] = True
        d.pop("family_id")
        store._redis_client.strings[RedisKey.auth_token("t-old")] = json.dumps(d)
        with pytest.raises(ValueError, match="already revoked"):
            await store.rotate_refresh_token("t-old")

    async def test_rotate_expired(self, store):
        store._redis_client.strings[RedisKey.auth_token("t-old")] = json.dumps(
            _expired_data()
        )
        with pytest.raises(ValueError, match="expired"):
            await store.rotate_refresh_token("t-old")

    async def test_rotate_happy_path(self, store):
        now = datetime.now(timezone.utc)
        d = {
            "token_id": "t-old",
            "user_id": "u1",
            "user_info": USER_INFO,
            "family_id": "f1",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        fake = store._redis_client
        fake.strings[RedisKey.auth_token("t-old")] = json.dumps(d)
        fake.ttls[RedisKey.auth_token("t-old")] = 3600

        new_id = await store.rotate_refresh_token("t-old")

        old = json.loads(fake.strings[RedisKey.auth_token("t-old")])
        assert old["revoked"] is True
        new = json.loads(fake.strings[RedisKey.auth_token(new_id)])
        assert new["family_id"] == "f1"
        assert new["revoked"] is False
        assert new_id in fake.sets[RedisKey.auth_user_tokens("u1")]
        assert fake.strings[RedisKey.auth_token_family(new_id)] == "f1"

    async def test_rotate_happy_path_no_ttl(self, store):
        now = datetime.now(timezone.utc)
        d = {
            "token_id": "t-old",
            "user_id": "u1",
            "user_info": USER_INFO,
            "family_id": "f1",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "revoked": False,
            "revoked_at": None,
            "last_used_at": None,
        }
        store._redis_client.strings[RedisKey.auth_token("t-old")] = json.dumps(d)
        assert store._redis_client.ttls.get(RedisKey.auth_token("t-old")) is None
        new_id = await store.rotate_refresh_token("t-old")
        assert json.loads(
            store._redis_client.strings[RedisKey.auth_token(new_id)]
        )["revoked"] is False


@pytest.mark.anyio
class TestRevoke:
    async def test_revoke_corrupted(self, store):
        store._redis_client.strings[RedisKey.auth_token("t-old")] = "{bad"
        assert await store.revoke_refresh_token("t-old") is False

    async def test_revoke_already_revoked(self, store):
        d = dict(_expired_data(), revoked=True)
        store._redis_client.strings[RedisKey.auth_token("t-old")] = json.dumps(d)
        assert await store.revoke_refresh_token("t-old") is True

    async def test_revoke_no_ttl_branch(self, store):
        d = _expired_data()
        d["expires_at"] = datetime.now(timezone.utc).isoformat()
        store._redis_client.strings[RedisKey.auth_token("t-old")] = json.dumps(d)
        assert await store.revoke_refresh_token("t-old") is True
        assert json.loads(
            store._redis_client.strings[RedisKey.auth_token("t-old")]
        )["revoked"] is True

    async def test_revoke_family_corrupted_and_no_ttl(self, store):
        fake = store._redis_client
        d = _expired_data()
        d["expires_at"] = datetime.now(timezone.utc).isoformat()
        fake.strings[RedisKey.auth_token("t1")] = json.dumps(d)
        fake.strings[RedisKey.auth_token("t2")] = "{bad"
        fake.sets[RedisKey.auth_family_tokens("f1")] = {"t1", "t2"}
        count = await store.revoke_token_family("f1")
        assert count == 1
        assert fake.strings.get(RedisKey.auth_family_revoked("f1")) == "1"

    async def test_revoke_family_empty(self, store):
        assert await store.revoke_token_family("f-none") == 0

    async def test_revoke_family_all_revoked_sets_flag_direct(self, store):
        fake = store._redis_client
        d = dict(_expired_data(), revoked=True)
        fake.strings[RedisKey.auth_token("t1")] = json.dumps(d)
        fake.sets[RedisKey.auth_family_tokens("f1")] = {"t1"}
        count = await store.revoke_token_family("f1")
        assert count == 0
        assert fake.strings.get(RedisKey.auth_family_revoked("f1")) == "1"

    async def test_revoke_all_user_corrupted_and_no_ttl(self, store):
        fake = store._redis_client
        d = _expired_data()
        d["expires_at"] = datetime.now(timezone.utc).isoformat()
        fake.strings[RedisKey.auth_token("t1")] = json.dumps(d)
        fake.strings[RedisKey.auth_token("t2")] = "{bad"
        fake.sets[RedisKey.auth_user_tokens("u1")] = {"t1", "t2"}
        count = await store.revoke_all_user_tokens("u1")
        assert count == 1
        assert RedisKey.auth_user_tokens("u1") not in fake.sets

    async def test_revoke_all_user_empty(self, store):
        assert await store.revoke_all_user_tokens("u-none") == 0

    async def test_revoke_all_user_all_revoked_direct_delete(self, store):
        fake = store._redis_client
        d = dict(_expired_data(), revoked=True)
        fake.strings[RedisKey.auth_token("t1")] = json.dumps(d)
        fake.sets[RedisKey.auth_user_tokens("u1")] = {"t1"}
        count = await store.revoke_all_user_tokens("u1")
        assert count == 0
        assert RedisKey.auth_user_tokens("u1") not in fake.sets


@pytest.mark.anyio
class TestCleanupAndDispatch:
    async def test_cleanup_expired_and_corrupted(self, store):
        fake = store._redis_client
        fake.strings[RedisKey.auth_token("t-exp")] = json.dumps(_expired_data())
        fake.strings[RedisKey.auth_token("t-bad")] = "{bad"
        fake.sets[RedisKey.auth_user_tokens("u1")] = {"t-exp"}
        n = await store.cleanup_expired_tokens()
        assert n == 1
        assert RedisKey.auth_token("t-exp") not in fake.strings
        assert RedisKey.auth_token("t-bad") not in fake.strings

    async def test_module_dispatch_redis_paths(self, store, monkeypatch):
        import app.security.refresh_token as mod

        monkeypatch.setattr(mod, "refresh_token_store", store)
        token_id = await store.create_refresh_token("u1", USER_INFO)
        assert await verify_refresh_token(token_id) is not None
        assert await get_user_token_count("u1") >= 1
        assert await rotate_refresh_token(token_id)
        assert await revoke_refresh_token(token_id) is True
        assert await revoke_all_user_tokens("u1") >= 0
        assert await revoke_token_family("f-any") >= 0
        counts = await get_refresh_token_count()
        assert isinstance(counts, dict)

    async def test_module_dispatch_revoke_oldest(self, store, monkeypatch):
        import app.security.refresh_token as mod

        monkeypatch.setattr(mod, "refresh_token_store", store)
        await store.create_refresh_token("u1", USER_INFO)
        assert isinstance(await revoke_oldest_user_token("u1"), bool)

    async def test_session_limit_enforcement(self, store, monkeypatch):
        monkeypatch.setattr(
            settings, "max_concurrent_sessions_per_user", 2, raising=False
        )
        await store.create_refresh_token("u1", USER_INFO)
        await store.create_refresh_token("u1", USER_INFO)
        third = await store.create_refresh_token("u1", USER_INFO)
        # 建第三个时最旧的被撤销：活跃数不超过上限
        active = 0
        for tid in store._redis_client.sets.get(
            RedisKey.auth_user_tokens("u1"), set()
        ):
            raw = store._redis_client.strings.get(RedisKey.auth_token(tid))
            if raw and not json.loads(raw).get("revoked"):
                active += 1
        assert active <= 2 and third
        monkeypatch.setattr(
            settings,
            "max_concurrent_sessions_per_user",
            5,
            raising=False,
        )


@pytest.mark.anyio
class TestRedisLazyInitAndDispatchElse:
    async def test_lazy_redis_client_init(self, monkeypatch):
        """单例首次使用时经 RedisPool.get_client 懒初始化（393-394）。"""
        from app.config import redis as redis_mod

        RedisRefreshTokenStore._instance = None
        try:
            s = RedisRefreshTokenStore()
            fake = FakeAsyncRedis()

            async def _fake_get_client():
                return fake

            monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_fake_get_client))
            token_id = await s.create_refresh_token("u1", USER_INFO)
            assert RedisKey.auth_token(token_id) in fake.strings
        finally:
            RedisRefreshTokenStore._instance = None

    async def test_dispatch_memory_branches(self, monkeypatch):
        """模块级分发函数的 Memory else 分支（940/961/982 等）。"""
        from app.security import refresh_token as mod
        from app.security.refresh_token import MemoryRefreshTokenStore

        m = MemoryRefreshTokenStore()
        monkeypatch.setattr(mod, "refresh_token_store", m)
        tid = await mod.create_refresh_token("u1", USER_INFO)
        assert await mod.rotate_refresh_token(tid)
        assert await mod.cleanup_expired_tokens() >= 0
        assert await mod.revoke_oldest_user_token("u-none") is False

    async def test_dispatch_redis_cleanup_and_mode(self, store, monkeypatch):
        """Redis 分支剩余行：cleanup 分发（958-959）与 get_storage_mode redis（994）。"""
        from app.security import refresh_token as mod

        monkeypatch.setattr(mod, "refresh_token_store", store)
        assert await mod.cleanup_expired_tokens() >= 0
        assert mod.get_storage_mode() == "redis"


class TestMemoryRotateBranches:
    """MemoryRefreshTokenStore rotate 的复用/撤销分支（163-180）。"""

    def test_rotate_not_found_with_family_key(self):
        from app.security.refresh_token import MemoryRefreshTokenStore

        m = MemoryRefreshTokenStore()
        m._family_store["t-old"] = "f1"
        with pytest.raises(ValueError, match="already been used"):
            m.rotate_refresh_token("t-old")

    def test_rotate_not_found_plain(self):
        from app.security.refresh_token import MemoryRefreshTokenStore

        m = MemoryRefreshTokenStore()
        with pytest.raises(ValueError, match="not found"):
            m.rotate_refresh_token("missing")

    def test_rotate_revoked_with_and_without_family(self):
        from app.security.refresh_token import MemoryRefreshTokenStore

        m = MemoryRefreshTokenStore()
        now = datetime.now(timezone.utc)
        d = {
            "token_id": "t-old",
            "user_id": "u1",
            "user_info": USER_INFO,
            "family_id": "f1",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(days=7)).isoformat(),
            "revoked": True,
            "revoked_at": None,
            "last_used_at": None,
        }
        m._store["t-old"] = d
        m._user_tokens["u1"] = ["t-old"]
        with pytest.raises(ValueError, match="already been used"):
            m.rotate_refresh_token("t-old")

        d2 = dict(d)
        d2.pop("family_id")
        m._store["t-old2"] = d2
        with pytest.raises(ValueError, match="already revoked"):
            m.rotate_refresh_token("t-old2")

    def test_rotate_new_user_bucket(self):
        """user 无桶时新建列表（210）。"""
        from app.security.refresh_token import MemoryRefreshTokenStore

        m = MemoryRefreshTokenStore()
        tid = m.create_refresh_token("u-new", USER_INFO)
        new_tid = m.rotate_refresh_token(tid)
        assert new_tid in m._user_tokens["u-new"]
