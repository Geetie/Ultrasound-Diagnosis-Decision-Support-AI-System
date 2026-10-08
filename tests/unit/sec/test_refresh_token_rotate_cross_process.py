"""跨进程 rotate 互斥（并发审查 P0 残留项，2026-10-04 闭合）的回归测试。

背景
----
``rotate_refresh_token`` 是典型的 check-then-act：``pipeline(transaction=True)``
只保证**写批次**原子，而 ``GET`` 与 ``revoked`` 判定都在事务之外。单进程内靠
per-token ``asyncio.Lock`` 已闭合（``_rotate_guard`` 原有逻辑），但多 worker /
多实例部署时**两个进程各自的锁互不可见** → 同时读到 ``revoked=false`` →
同一 refresh token 派生出两个有效后继 → reuse 检测被永久绕过
（等价于把 7 天 refresh token 变成无限续期）。

修法：在 per-token 锁之内再抢一把 **Redis SET NX** 短锁（带 TTL 防进程被 kill
时死锁），抢不到就报错让客户端重试——**宁可这次刷新失败，也不能放过复制**。
Redis 不可用时同样保守失败，不静默降级到无跨进程保护的老行为。

本测试用两个 store 实例共享同一个 FakeAsyncRedis 来模拟跨进程。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.security import refresh_token as rt
from app.security.refresh_token import RedisRefreshTokenStore


@pytest.fixture
def fake_redis():
    from tests.unit.sec.test_refresh_token_redis import FakeAsyncRedis

    return FakeAsyncRedis()


@pytest.fixture(autouse=True)
def _reset_singleton():
    RedisRefreshTokenStore._instance = None
    yield
    RedisRefreshTokenStore._instance = None


def _make_store(fake_redis) -> RedisRefreshTokenStore:
    s = RedisRefreshTokenStore()
    s._redis_client = fake_redis
    return s


async def _seed_token(fake_redis, token_id="t-shared", user_id="u-cross"):
    now = datetime.now(timezone.utc)
    data = {
        "token_id": token_id,
        "user_id": user_id,
        "user_info": {"role": "ULTRASOUND_DOCTOR"},
        "family_id": "fam-cross",
        "created_at": now.isoformat(),
        "expires_at": (now + timedelta(days=7)).isoformat(),
        "revoked": False,
        "revoked_at": None,
        "last_used_at": None,
    }
    from app.config.redis_keys import RedisKey

    await fake_redis.set(RedisKey.auth_token(token_id), json.dumps(data))
    await fake_redis.set(RedisKey.auth_token_family(token_id), "fam-cross")
    return data


class TestCrossProcessRotateLock:
    @pytest.mark.anyio
    async def test_locked_token_rotate_rejected(self, fake_redis):
        """锁被占用时 rotate 必须失败，而不是派生出第二个后继。"""
        store = _make_store(fake_redis)
        await _seed_token(fake_redis)
        # 模拟"另一进程"正持有该 token 的轮换锁
        await fake_redis.set(f"{rt._ROTATE_LOCK_PREFIX}t-shared", "1",
                             ex=rt._REDIS_LOCK_TTL_S)
        with pytest.raises(RuntimeError, match="轮换"):
            await store.rotate_refresh_token("t-shared")

    @pytest.mark.anyio
    async def test_lock_released_after_success(self, fake_redis):
        """rotate 成功后锁必须释放（否则该 token 永久卡死）。"""
        store = _make_store(fake_redis)
        await _seed_token(fake_redis)
        new_id = await store.rotate_refresh_token("t-shared")
        assert new_id and new_id != "t-shared"
        assert f"{rt._ROTATE_LOCK_PREFIX}t-shared" not in fake_redis.strings

    @pytest.mark.anyio
    async def test_redis_down_fails_closed(self, fake_redis):
        """Redis 不可用时保守失败，不静默降级到无跨进程保护的老行为。"""

        class _Broken:
            async def set(self, *a, **kw):
                raise ConnectionError("redis down")

        store = _make_store(fake_redis)
        await _seed_token(fake_redis, token_id="t-down")
        store._redis_client = _Broken()
        with pytest.raises(RuntimeError, match="Redis 不可用"):
            await store.rotate_refresh_token("t-down")

    @pytest.mark.anyio
    async def test_reuse_detection_intact(self, fake_redis, monkeypatch):
        """闭合互斥后 reuse 语义不变：旧 token 第二次用仍被拒。

        宽限期（合法并发返回后继）关闭后本用例钉的是窗口外/关闭态的窃取检测；
        窗口内的跨进程宽限行为由 test_refresh_token_grace_period.py 覆盖。"""
        monkeypatch.setattr(rt.settings, "refresh_token_grace_period_s", 0)
        store = _make_store(fake_redis)
        await _seed_token(fake_redis, token_id="t-reuse")
        await store.rotate_refresh_token("t-reuse")
        with pytest.raises(ValueError, match=r"already been used|not found"):
            await store.rotate_refresh_token("t-reuse")

    @pytest.mark.anyio
    async def test_lock_key_is_scoped_per_token(self, fake_redis):
        """锁键按 token 隔离：A 的锁不能挡住 B 的 rotate。"""
        store = _make_store(fake_redis)
        await _seed_token(fake_redis, token_id="t-a")
        await _seed_token(fake_redis, token_id="t-b")
        await fake_redis.set(f"{rt._ROTATE_LOCK_PREFIX}t-a", "1",
                             ex=rt._REDIS_LOCK_TTL_S)
        new_id = await store.rotate_refresh_token("t-b")
        assert new_id and new_id != "t-b"
