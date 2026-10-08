"""旋转宽限期（feature_list: refresh-token-grace-period）的回归测试。

背景
----
合法并发刷新（多标签页 / 双击）会把**已轮换**的旧 token 再次送到 rotate/verify。
宽限期之前的语义一律判 reuse → 撤销整个 family → 全设备踢下线
（真机 6 进程验证实测：恰好 1 成功、其余失败原因全是 family revoked）。

宽限期语义（settings.refresh_token_grace_period_s，默认 30s，0=关闭）：
  窗口内 + 后继链可达 + 族未撤销 → 返回后继（同一个新 token，**不新建旋转** ——
  那会让 family 分叉，等于绕过 reuse 检测）；
  窗口外 / 无后继（登出、显式吊销） / 族已撤销 → reuse 原语义（撤族）。
  绝不放松的是：**族已撤销后再出示仍然全部拒绝**。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config.redis_keys import RedisKey
from app.config.settings import settings
from app.security.refresh_token import (
    MemoryRefreshTokenStore,
    RedisRefreshTokenStore,
)

USER_INFO = {"username": "u1", "role": "ULTRASOUND_DOCTOR"}


@pytest.fixture(autouse=True)
def _reset_memory_singleton():
    store = MemoryRefreshTokenStore()
    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()
    yield
    store._store.clear()
    store._family_store.clear()
    store._user_tokens.clear()


@pytest.fixture(autouse=True)
def _grace_30s(monkeypatch):
    """每个用例显式声明宽限配置 —— 测试钉的是语义，不依赖全局默认值。"""
    monkeypatch.setattr(settings, "refresh_token_grace_period_s", 30)


@pytest.fixture
def fake_redis():
    from tests.unit.sec.test_refresh_token_redis import FakeAsyncRedis

    return FakeAsyncRedis()


@pytest.fixture(autouse=True)
def _reset_redis_singleton():
    RedisRefreshTokenStore._instance = None
    yield
    RedisRefreshTokenStore._instance = None


async def _seed_token(fake_redis, token_id: str, family_id: str = "fam-g") -> None:
    now = datetime.now(timezone.utc)
    data = {
        "token_id": token_id,
        "user_id": "u1",
        "user_info": dict(USER_INFO),
        "family_id": family_id,
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "revoked": False,
        "revoked_at": None,
        "last_used_at": None,
    }
    await fake_redis.set(RedisKey.auth_token(token_id), json.dumps(data))
    await fake_redis.set(RedisKey.auth_token_family(token_id), family_id)


# ---------- Memory store（同步路径） ----------


class TestMemoryGrace:
    def test_concurrent_rotate_returns_same_successor(self):
        store = MemoryRefreshTokenStore()
        t1 = store.create_refresh_token("u1", USER_INFO)
        t2 = store.rotate_refresh_token(t1)
        # 多标签页同时刷新：第二个携带旧 token 的请求拿到**同一个**后继
        assert store.rotate_refresh_token(t1) == t2
        # family 完好：后继仍然可用
        assert store.verify_refresh_token(t2) is not None

    def test_verify_within_grace_returns_successor_data(self):
        store = MemoryRefreshTokenStore()
        t1 = store.create_refresh_token("u1", USER_INFO)
        t2 = store.rotate_refresh_token(t1)
        data = store.verify_refresh_token(t1)
        assert data is not None
        assert data["token_id"] == t2

    def test_chained_rotation_within_grace(self):
        store = MemoryRefreshTokenStore()
        t1 = store.create_refresh_token("u1", USER_INFO)
        t2 = store.rotate_refresh_token(t1)
        t3 = store.rotate_refresh_token(t2)
        # 三个标签页排队刷新：最旧的 token 也要能沿后继链追到最新的活 token
        assert store.rotate_refresh_token(t1) == t3

    def test_reuse_beyond_grace_revokes_family(self):
        store = MemoryRefreshTokenStore()
        t1 = store.create_refresh_token("u1", USER_INFO)
        t2 = store.rotate_refresh_token(t1)
        # 窗口已过：把 revoked_at 拨回 31s 前（直接操纵存储，不引入时钟魔法）
        store._store[t1]["revoked_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=31)
        ).isoformat()
        with pytest.raises(ValueError, match="family"):
            store.rotate_refresh_token(t1)
        assert store.verify_refresh_token(t2) is None  # 全族被撤

    def test_logout_revoked_token_gets_no_grace(self):
        """登出（无后继的显式吊销）不吃宽限 —— 否则吊销形同虚设。"""
        store = MemoryRefreshTokenStore()
        t1 = store.create_refresh_token("u1", USER_INFO)
        assert store.revoke_refresh_token(t1) is True
        with pytest.raises(ValueError):
            store.rotate_refresh_token(t1)
        assert store.verify_refresh_token(t1) is None

    def test_grace_disabled_strict_semantics(self, monkeypatch):
        monkeypatch.setattr(settings, "refresh_token_grace_period_s", 0)
        store = MemoryRefreshTokenStore()
        t1 = store.create_refresh_token("u1", USER_INFO)
        t2 = store.rotate_refresh_token(t1)
        with pytest.raises(ValueError):
            store.rotate_refresh_token(t1)
        assert store.verify_refresh_token(t2) is None


# ---------- Redis store（生产路径，FakeAsyncRedis） ----------


class TestRedisGrace:
    @pytest.mark.anyio
    async def test_concurrent_rotate_returns_same_successor(self, fake_redis):
        store = RedisRefreshTokenStore()
        store._redis_client = fake_redis
        await _seed_token(fake_redis, "t1")
        t2 = await store.rotate_refresh_token("t1")
        assert await store.rotate_refresh_token("t1") == t2
        assert await store.verify_refresh_token(t2) is not None

    @pytest.mark.anyio
    async def test_verify_within_grace_returns_successor_data(self, fake_redis):
        store = RedisRefreshTokenStore()
        store._redis_client = fake_redis
        await _seed_token(fake_redis, "t1")
        t2 = await store.rotate_refresh_token("t1")
        data = await store.verify_refresh_token("t1")
        assert data is not None
        assert data["token_id"] == t2

    @pytest.mark.anyio
    async def test_reuse_beyond_grace_revokes_family(self, fake_redis):
        store = RedisRefreshTokenStore()
        store._redis_client = fake_redis
        await _seed_token(fake_redis, "t1")
        t2 = await store.rotate_refresh_token("t1")
        # 窗口已过：直接改写旧 token 的 revoked_at
        raw = json.loads(fake_redis.strings[RedisKey.auth_token("t1")])
        raw["revoked_at"] = (datetime.now(timezone.utc) - timedelta(seconds=31)).isoformat()
        fake_redis.strings[RedisKey.auth_token("t1")] = json.dumps(raw)

        with pytest.raises(ValueError, match="family"):
            await store.rotate_refresh_token("t1")
        assert await store.verify_refresh_token(t2) is None
        # 族撤销标记真实存在 —— verify 的 family_already_revoked 快路径可命中
        assert await fake_redis.exists(RedisKey.auth_family_revoked("fam-g"))

    @pytest.mark.anyio
    async def test_cross_process_grace(self, fake_redis):
        """多 worker 部署下的真实场景：标签页 A 的轮换落在 worker1，
        标签页 B 携带旧 token 的刷新落在 worker2 —— 两个实例共享 Redis，
        宽限判定必须同样生效。"""
        s1 = RedisRefreshTokenStore()
        s1._redis_client = fake_redis
        s2 = RedisRefreshTokenStore()
        s2._redis_client = fake_redis

        await _seed_token(fake_redis, "t1")
        t2 = await s1.rotate_refresh_token("t1")
        assert await s2.rotate_refresh_token("t1") == t2
