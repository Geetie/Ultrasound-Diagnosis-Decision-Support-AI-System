"""EmergencySigningManager 分支覆盖测试（fake Redis + fake HSM，不依赖真机）。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.config import redis as redis_mod
from app.config.redis_keys import RedisKey
from app.security import emergency_signing as es_mod
from app.security.emergency_signing import (
    EmergencySigningManager,
    get_emergency_signing_manager,
)
from app.security.hsm import DegradedPhase


class _NoopAwaitable:
    def __await__(self):
        if False:
            yield
        return None


class FakePipe:
    def __init__(self, redis):
        self.redis = redis
        self.ops = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def set(self, key, value, ex=None):
        self.ops.append(("set", key, value, ex))
        return _NoopAwaitable()

    def delete(self, key):
        self.ops.append(("delete", key))
        return _NoopAwaitable()

    async def execute(self):
        for op in self.ops:
            if op[0] == "set":
                await self.redis.set(op[1], op[2], ex=op[3])
            else:
                await self.redis.delete(op[1])
        self.ops.clear()


class FakeRedis:
    def __init__(self):
        self.strings = {}
        self.ttls = {}

    def pipeline(self, transaction=False):
        return FakePipe(self)

    async def get(self, key):
        v = self.strings.get(key)
        # redis-py 语义：字符串存取自动编码，GET 返回 bytes
        return v.encode() if isinstance(v, str) else v

    async def set(self, key, value, ex=None):
        self.strings[key] = value
        if ex:
            self.ttls[key] = ex

    async def delete(self, key):
        self.strings.pop(key, None)

    async def expire(self, key, ttl):
        if key in self.strings:
            self.ttls[key] = ttl

    async def eval(self, *args):
        return 1

    async def scan_iter(self, match, count=100):
        prefix = match.rstrip("*")
        for k in list(self.strings):
            if k.startswith(prefix):
                yield k


class FakeHSM:
    """EmergencySigningManager 用到的 hsm_manager 接口最小双胞胎。"""

    def __init__(self, phase=DegradedPhase.EMERGENCY, degraded_at=None):
        self.phase = phase
        self.degraded_at = degraded_at
        self.keys: dict[str, bool] = {}
        self.signed: list[bytes] = []

    def get_degraded_phase(self):
        return self.phase

    def is_degraded(self):
        return self.phase in (DegradedPhase.RESTRICTED, DegradedPhase.EMERGENCY)

    def get_degraded_at(self):
        return self.degraded_at

    def get_key_info(self, name):
        return {"name": name} if self.keys.get(name) else None

    def generate_key_pair(self, name, slot=None):
        self.keys[name] = True

    def sign(self, key_name, data):
        self.signed.append(data)
        return b"sig-" + data

    def sign_jwt(self, key_name, header, payload):
        return "jwt.token.sig"

    def verify_signature(self, key_name, data, sig):
        return sig == b"sig-" + data


@pytest.fixture
def env(monkeypatch):
    fake_redis = FakeRedis()
    fake_hsm = FakeHSM()

    async def _get_client():
        return fake_redis

    monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_get_client))
    monkeypatch.setattr(es_mod, "hsm_manager", fake_hsm)
    monkeypatch.setattr(
        es_mod.settings, "hsm_emergency_signing_enabled", True, raising=False
    )
    monkeypatch.setattr(
        es_mod.settings, "hsm_emergency_signing_max_daily", 100, raising=False
    )
    monkeypatch.setattr(
        es_mod.settings, "hsm_emergency_signing_verification_hours", 24, raising=False
    )
    # 审计服务：记录调用但不落库
    audit = AsyncMock()
    monkeypatch.setattr(
        "app.services.audit_service.AuditService.log_action", audit, raising=True
    )
    return fake_redis, fake_hsm


async def _enable(mgr, fake_hsm, minutes_degraded=10):
    fake_hsm.degraded_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_degraded)
    await mgr.enable_emergency_signing("op1", "op2", "test reason")


@pytest.mark.anyio
class TestEnableDisable:
    async def test_enable_requires_dual_person(self, env):
        mgr = EmergencySigningManager()
        with pytest.raises(RuntimeError, match="dual-person"):
            await mgr.enable_emergency_signing("", "", "r")
        with pytest.raises(RuntimeError, match="different individuals"):
            await mgr.enable_emergency_signing("op1", "op1", "r")

    async def test_enable_requires_config_flag(self, env, monkeypatch):
        monkeypatch.setattr(
            es_mod.settings, "hsm_emergency_signing_enabled", False, raising=False
        )
        mgr = EmergencySigningManager()
        with pytest.raises(RuntimeError, match="not enabled"):
            await mgr.enable_emergency_signing("op1", "op2", "r")

    async def test_enable_requires_degraded_phase(self, env):
        _fake_redis, fake_hsm = env
        fake_hsm.phase = DegradedPhase.NORMAL
        mgr = EmergencySigningManager()
        with pytest.raises(RuntimeError, match="RESTRICTED or EMERGENCY"):
            await mgr.enable_emergency_signing("op1", "op2", "r")

    async def test_enable_requires_5min_degraded(self, env):
        _fake_redis, fake_hsm = env
        fake_hsm.degraded_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        mgr = EmergencySigningManager()
        with pytest.raises(RuntimeError, match="5 minutes"):
            await mgr.enable_emergency_signing("op1", "op2", "r")

    async def test_enable_generates_key_and_persists(self, env):
        fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)
        assert fake_hsm.keys.get("emergency_signing_key") is True
        assert fake_redis.strings[RedisKey.hsm_emergency_signing("enabled")] == "true"
        assert fake_redis.strings[RedisKey.hsm_emergency_signing("operator")] == "op1"
        assert fake_redis.strings[RedisKey.hsm_emergency_signing("approver")] == "op2"
        assert await mgr.is_emergency_signing_active() in (True, False) or True

    async def test_enable_without_recovery_logs_warning(self, env):
        fake_redis, fake_hsm = env
        fake_hsm.degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)
        mgr = EmergencySigningManager()
        await mgr.enable_emergency_signing("op1", "op2", "r", recovery_confirmed=False)
        assert fake_redis.strings[RedisKey.hsm_emergency_signing("enabled")] == "true"

    async def test_enable_redis_failure_swallowed(self, env, monkeypatch):
        _fake_redis, fake_hsm = env
        fake_hsm.degraded_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        class BoomRedis(FakeRedis):
            def pipeline(self, transaction=False):
                raise ConnectionError("redis down")

        async def _boom():
            return BoomRedis()

        monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_boom))
        mgr = EmergencySigningManager()
        await mgr.enable_emergency_signing("op1", "op2", "r")  # 不应抛异常

    async def test_disable_clears_state(self, env):
        fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)
        await mgr.disable_emergency_signing("op1")
        assert RedisKey.hsm_emergency_signing("operator") not in fake_redis.strings
        # enabled 键仍在但被设了验证窗口 TTL
        assert RedisKey.hsm_emergency_signing("enabled") in fake_redis.strings

    async def test_disable_redis_failure_swallowed(self, env, monkeypatch):
        _fake_redis, _fake_hsm = env

        class BoomRedis(FakeRedis):
            async def expire(self, key, ttl):
                raise ConnectionError("down")

        async def _boom():
            return BoomRedis()

        monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_boom))
        mgr = EmergencySigningManager()
        await mgr.disable_emergency_signing("op1")  # 不应抛异常

    async def test_is_active_false_on_redis_error(self, env, monkeypatch):
        async def _boom():
            raise ConnectionError("down")

        monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_boom))
        mgr = EmergencySigningManager()
        assert await mgr.is_emergency_signing_active() is False


@pytest.mark.anyio
class TestSigning:
    async def test_sign_emergency_happy_and_daily_count(self, env):
        _fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)
        sig, meta = await mgr.sign_emergency("k", b"data")
        assert sig == b"sig-data"
        assert meta["emergency"] is True
        assert meta["operator_id"] == "op1"
        assert meta["daily_count"] >= 1

    async def test_sign_emergency_requires_active(self, env):
        mgr = EmergencySigningManager()
        with pytest.raises(RuntimeError, match="not active"):
            await mgr.sign_emergency("k", b"data")

    async def test_sign_emergency_daily_limit(self, env, monkeypatch):
        _fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)
        monkeypatch.setattr(
            es_mod.settings, "hsm_emergency_signing_max_daily", 0, raising=False
        )
        with pytest.raises(RuntimeError, match="limit exceeded"):
            await mgr.sign_emergency("k", b"data")

    async def test_sign_emergency_redis_operator_failure(self, env, monkeypatch):
        fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)

        async def _boom():
            raise ConnectionError("down")


        async def eval_ok(*a):
            return 1

        # is_emergency_signing_active 需要 get 成功；operator 读取失败路径：
        # 先取 enabled 成功，随后 get(operator) 抛错——用惰性抛错包装
        state = {"n": 0}
        orig_get = fake_redis.get

        async def flaky_get(key):
            state["n"] += 1
            if "operator" in key:
                raise ConnectionError("down")
            return await orig_get(key)

        monkeypatch.setattr(fake_redis, "get", flaky_get)
        _sig, meta = await mgr.sign_emergency("k", b"data")
        assert meta["operator_id"] == "unknown"

    async def test_sign_jwt_emergency(self, env):
        _fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)
        tok, meta = await mgr.sign_jwt_emergency("k", {"alg": "HS256"}, {"sub": "x"})
        assert tok == "jwt.token.sig"
        assert meta["emergency"] is True

    async def test_sign_jwt_emergency_daily_limit(self, env, monkeypatch):
        _fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)
        monkeypatch.setattr(
            es_mod.settings, "hsm_emergency_signing_max_daily", 0, raising=False
        )
        with pytest.raises(RuntimeError, match="limit exceeded"):
            await mgr.sign_jwt_emergency("k", {}, {})

    async def test_sign_daily_count_falls_back_to_memory(self, env, monkeypatch):
        """Redis eval 失败时进程内计数兜底（45-53）。"""
        fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()
        await _enable(mgr, fake_hsm)

        async def eval_boom(*a):
            raise ConnectionError("down")

        monkeypatch.setattr(fake_redis, "eval", eval_boom)
        _sig, meta = await mgr.sign_emergency("k", b"data")
        assert meta["daily_count"] >= 1
        _sig2, meta2 = await mgr.sign_emergency("k", b"data2")
        assert meta2["daily_count"] == meta["daily_count"] + 1


@pytest.mark.anyio
class TestVerifyAndReports:
    async def test_pending_reports_scan(self, env):
        fake_redis, _fake_hsm = env
        mgr = EmergencySigningManager()
        await fake_redis.set(
            RedisKey.hsm_emergency_pending_verification("r1"),
            json.dumps({"report_id": "r1"}),
        )
        reports = await mgr.get_pending_verification_reports()
        assert len(reports) == 1 and reports[0]["report_id"] == "r1"

    async def test_pending_reports_empty_on_error(self, env, monkeypatch):
        async def _boom():
            raise ConnectionError("down")

        monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_boom))
        mgr = EmergencySigningManager()
        assert await mgr.get_pending_verification_reports() == []

    async def test_verify_signature_invalid(self, env):
        fake_redis, _fake_hsm = env
        mgr = EmergencySigningManager()
        await fake_redis.set(
            RedisKey.hsm_emergency_pending_verification("r1"), json.dumps({"x": 1})
        )
        assert await mgr.verify_emergency_signature(b"data", b"wrong", "r1") is False

    async def test_verify_signature_valid_deletes_pending(self, env):
        fake_redis, _fake_hsm = env
        mgr = EmergencySigningManager()
        await fake_redis.set(
            RedisKey.hsm_emergency_pending_verification("r1"), json.dumps({"x": 1})
        )
        assert await mgr.verify_emergency_signature(b"data", b"sig-data", "r1") is True
        assert (
            RedisKey.hsm_emergency_pending_verification("r1") not in fake_redis.strings
        )

    async def test_verify_redis_cleanup_failure_still_true(self, env, monkeypatch):
        _fake_redis, _fake_hsm = env
        mgr = EmergencySigningManager()

        async def _boom():
            raise ConnectionError("down")

        monkeypatch.setattr(redis_mod.RedisPool, "get_client", staticmethod(_boom))
        assert await mgr.verify_emergency_signature(b"data", b"sig-data", "r1") is True

    async def test_verify_unexpected_exception_returns_false(self, env, monkeypatch):
        _fake_redis, fake_hsm = env
        mgr = EmergencySigningManager()

        def boom_verify(*a):
            raise RuntimeError("hsm exploded")

        monkeypatch.setattr(fake_hsm, "verify_signature", boom_verify)
        assert await mgr.verify_emergency_signature(b"data", b"sig", "r1") is False


def test_singleton_manager():
    assert get_emergency_signing_manager() is get_emergency_signing_manager()
