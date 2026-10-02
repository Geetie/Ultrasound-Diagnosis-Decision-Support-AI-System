"""脱敏还原服务补测（quality-debt，核心模块 `app/security/desensitization_restore.py`）。

为什么放在 `tests/unit/data/` 而不是 `tests/unit/sec/`
----------------------------------------------------
`tests/unit/sec/conftest.py` 有 session 级 autouse fixture，会把本模块最需要验证的两个
方法**永久打桩**：`_get_or_create_aes_key`（固定返回随机 key）与
`_check_rate_limit_persistent`（固定返回 True）。在 sec 目录内写这些测试会**静默假绿**：
单跑通过、断言却根本没碰到真实实现。放在不带该 fixture 的目录里，才真的测到东西。

本文件覆盖的是"密钥生命周期"这一段——它决定**数据能不能解回来**，属于最不能靠
"看起来没报错"来判断的部分：
- Redis 里已有可解封装的密钥 -> 必须复用，不能每次新建（否则历史数据永久解不开）；
- 版本不匹配 -> 必须走旧版本密钥回退，取不到时**明确报错**而不是返回乱码；
- 落盘失败 -> 生产环境必须拒绝加密（拿不到可持久化的密钥就加密＝不可逆数据丢失）。
"""
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.redis_keys import RedisKey
from app.security import desensitization_restore as dr_module
from app.security.desensitization_restore import (
    DesensitizationRestoreService,
    get_desensitization_restore_service,
)
from app.security.hsm.enums import KeySlot

KEY_NAME = "desens_mapping_key"

# 在**采集期**（任何 fixture 建立之前）把真实实现抓下来。
#
# 为什么必须这么做：`tests/unit/sec/conftest.py` 的 `mock_desensitization_restore_redis`
# 是 **session 级 autouse**，它把下面这两个方法打在**类**上；xdist 按文件分发时，
# 只要同一 worker 先跑了 sec 目录，补丁就会跨目录生效——于是本文件"单跑全绿、跑全套红"。
# 仅仅换个目录并不能躲开它（这一点我第一版判断错了，实测 10 个用例在全套里失败）。
# 抓下来的函数引用不受后续 monkeypatch 影响，再由下面的 autouse fixture 装回去。
_REAL_GET_OR_CREATE_AES_KEY = DesensitizationRestoreService._get_or_create_aes_key
_REAL_PERSISTENT_RATE_LIMIT = DesensitizationRestoreService._check_rate_limit_persistent


@pytest.fixture(autouse=True)
def _use_real_key_and_ratelimit(monkeypatch):
    monkeypatch.setattr(
        DesensitizationRestoreService,
        "_get_or_create_aes_key",
        _REAL_GET_OR_CREATE_AES_KEY,
    )
    monkeypatch.setattr(
        DesensitizationRestoreService,
        "_check_rate_limit_persistent",
        _REAL_PERSISTENT_RATE_LIMIT,
    )


class _FakePipeline:
    def __init__(self, parent):
        self._parent = parent
        self.commands = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def set(self, key, value, ex=None):
        self.commands.append(("set", key, value, ex))
        return self

    async def execute(self):
        for command in self.commands:
            if command[0] == "set":
                self._parent.store[command[1]] = command[2]
        return [True] * len(self.commands)


class _FakeRedis:
    def __init__(self, store=None):
        self.store = dict(store or {})
        self.pipeline_obj = _FakePipeline(self)
        self.fail_pipeline = False
        # 跨进程限流用 EVAL 计数，行为由用例指定
        self.eval_result = 1
        self.eval_error = None
        self.eval_calls = []

    async def get(self, key):
        return self.store.get(key)

    def pipeline(self, transaction=False):
        if self.fail_pipeline:
            raise ConnectionError("redis pipeline unavailable")
        return self.pipeline_obj

    async def eval(self, script, numkeys, key, *args):
        self.eval_calls.append((key, args))
        if self.eval_error is not None:
            raise self.eval_error
        return self.eval_result


class _FakeHSM:
    """可逆的加解封替身：encrypt 加前缀，decrypt 去前缀。"""

    def __init__(self, key_info=None):
        self.key_info = key_info
        self.generated = []
        self.decrypt_error = None

    def get_key_info(self, name):
        return self.key_info

    def generate_key_pair(self, name, slot=None):
        self.generated.append((name, slot))
        self.key_info = {"key_version": 1}
        return self.key_info

    def encrypt(self, name, plaintext):
        return b"wrapped:" + plaintext

    def decrypt(self, name, wrapped):
        if self.decrypt_error is not None:
            raise self.decrypt_error
        if not wrapped.startswith(b"wrapped:"):
            raise ValueError("bad wrapping")
        return wrapped[len(b"wrapped:") :]


@pytest.fixture
def hsm(monkeypatch):
    fake = _FakeHSM(key_info={"key_version": 1})
    monkeypatch.setattr(dr_module, "hsm_manager", fake)
    return fake


@pytest.fixture
def redis(monkeypatch):
    """内层再 patch：root conftest 的 fast_fail_redis 会让真实客户端一律抛错。"""
    fake = _FakeRedis()

    async def _get_client():
        return fake

    monkeypatch.setattr(
        "app.config.redis.RedisPool.get_client", staticmethod(_get_client)
    )
    return fake


@pytest.fixture
def service():
    return DesensitizationRestoreService()


def _aes_key_bytes(value: bytes = b"k") -> bytes:
    """铺满 32 字节的 AES-256 密钥。

    第一版直接返回入参，于是 `_aes_key_bytes(b"o")` 只有 1 字节，
    AESGCM 直接抛 "key must be 128/192/256 bits" —— 测试自己造了个非法密钥。
    """
    return (value * 32)[:32]


class TestEnsureHsmKey:
    def test_generates_when_missing(self, service, hsm):
        hsm.key_info = None

        service._ensure_hsm_key()

        assert hsm.generated == [(KEY_NAME, KeySlot.DESENSITIZATION_MAPPING)]

    def test_keeps_existing_key(self, service, hsm):
        service._ensure_hsm_key()

        assert hsm.generated == [], "已有密钥不得重新生成（会导致历史数据无法解密）"


class TestGetOrCreateAesKey:
    async def test_reuses_wrapped_key_from_redis(self, service, hsm, redis):
        """Redis 里已有可解封装的密钥时必须复用。"""
        redis.store[RedisKey.hsm_desens_aes_key(1)] = b"wrapped:" + _aes_key_bytes()
        redis.store[RedisKey.HSM_DESENS_AES_KEY_VERSION] = b"1"

        key, version = await service._get_or_create_aes_key()

        assert key == _aes_key_bytes()
        assert version == 1
        assert not redis.pipeline_obj.commands, "复用旧密钥时不应再写 Redis"

    async def test_creates_and_stores_when_absent(self, service, hsm, redis):
        key, version = await service._get_or_create_aes_key()

        assert len(key) == 32
        assert version == 1
        stored = redis.pipeline_obj.commands
        assert len(stored) == 2, "包裹后的密钥与版本号必须一起写入"
        assert RedisKey.hsm_desens_aes_key(1) in redis.store
        assert redis.store[RedisKey.HSM_DESENS_AES_KEY_VERSION] == "1"  # 按 str 写入

    async def test_version_mismatch_creates_new_key(self, service, hsm, redis):
        """版本不匹配说明 HSM 换过密钥：旧封装已不可用，必须新建并覆盖。"""
        redis.store[RedisKey.hsm_desens_aes_key(1)] = b"wrapped:stale"
        redis.store[RedisKey.HSM_DESENS_AES_KEY_VERSION] = b"9"

        key, version = await service._get_or_create_aes_key()

        assert key != b"stale"
        assert version == 1

    async def test_unwrap_failure_falls_back_to_new_key(self, service, hsm, redis):
        redis.store[RedisKey.hsm_desens_aes_key(1)] = b"wrapped:" + _aes_key_bytes()
        redis.store[RedisKey.HSM_DESENS_AES_KEY_VERSION] = b"1"
        hsm.decrypt_error = RuntimeError("hsm says no")

        key, _ = await service._get_or_create_aes_key()

        assert key != _aes_key_bytes(), "解封装失败必须新建密钥，而不是返回坏值"

    async def test_redis_unavailable_still_returns_key(self, service, hsm, monkeypatch):
        """Redis 不可用时仍要能工作（降级为进程内密钥），不能因此拒绝服务。"""

        async def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr(
            "app.config.redis.RedisPool.get_client", staticmethod(_boom)
        )

        key, version = await service._get_or_create_aes_key()

        assert len(key) == 32
        assert version == 1

    async def test_store_failure_does_not_raise_in_test_env(self, service, hsm, redis):
        """测试环境（PYTEST_CURRENT_TEST 存在）容忍落盘失败，否则无法本地开发。"""
        redis.fail_pipeline = True

        key, _ = await service._get_or_create_aes_key()

        assert len(key) == 32

    async def test_store_failure_raises_in_production(self, service, hsm, redis, monkeypatch):
        """生产环境**必须**拒绝加密：密钥没落盘就加密＝不可逆的数据丢失。

        这条守卫只在"没有 TEST_MODE / PYTEST_CURRENT_TEST"时生效，所以必须显式
        清掉这两个环境变量才能验证——否则它永远不会被触发（本地跑全绿、生产才炸）。
        """
        monkeypatch.delenv("TEST_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        redis.fail_pipeline = True

        with pytest.raises(RuntimeError, match="Cannot persist AES key"):
            await service._get_or_create_aes_key()

    async def test_no_key_infrastructure_at_all_raises_in_production(
        self, service, hsm, monkeypatch
    ):
        monkeypatch.delenv("TEST_MODE", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)

        async def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr(
            "app.config.redis.RedisPool.get_client", staticmethod(_boom)
        )

        with pytest.raises(RuntimeError):
            await service._get_or_create_aes_key()


class TestGetAesKeyCache:
    async def test_uses_cache_within_ttl(self, service, monkeypatch):
        service._key_cache = _aes_key_bytes()
        service._key_cache_at = datetime.now(timezone.utc)
        service._current_key_version = 7
        fetcher = AsyncMock()
        monkeypatch.setattr(service, "_get_or_create_aes_key", fetcher)

        key, version = await service._get_aes_key()

        assert key == _aes_key_bytes()
        assert version == 7
        assert fetcher.await_count == 0, "TTL 内不应重复取密钥（每取一次都要访问 HSM）"

    async def test_refreshes_after_ttl(self, service, hsm, redis):
        service._key_cache = _aes_key_bytes(b"old")
        service._key_cache_at = datetime.now(timezone.utc) - timedelta(minutes=10)

        key, _ = await service._get_aes_key()

        assert key != _aes_key_bytes(b"old")

    async def test_concurrent_refresh_keeps_winner(self, service, monkeypatch):
        """并发刷新时后到者要采用已有的缓存，而不是覆盖（否则前一个加密结果解不开）。"""
        service._key_cache = None
        service._key_cache_at = None

        async def _fill_cache():
            service._key_cache = _aes_key_bytes(b"winner")
            service._key_cache_at = datetime.now(timezone.utc)
            service._current_key_version = 3
            return _aes_key_bytes(b"loser"), 3

        monkeypatch.setattr(service, "_get_or_create_aes_key", _fill_cache)

        key, version = await service._get_aes_key()

        assert key == _aes_key_bytes(b"winner")
        assert version == 3, "版本号也要取胜者的，否则后续解密会按错的版本去找密钥"


class TestEncryptDecrypt:
    async def test_roundtrip(self, service, hsm, redis):
        ciphertext, iv, version = await service._encrypt_value("张三")

        assert await service._decrypt_value(ciphertext, iv, version) == "张三"

    async def test_same_plaintext_differs_each_time(self, service, hsm, redis):
        """随机 IV 是必须的：固定 IV 下相同明文密文相同，等于泄露"这两个值相等"。"""
        first, _, _ = await service._encrypt_value("张三")
        second, _, _ = await service._encrypt_value("张三")

        assert first != second

    async def test_old_version_decrypts_with_stored_key(self, service, hsm, redis):
        """历史数据落在旧密钥版本上时必须能解回来（迁移期最常见的情况）。

        构造要点：当前 HSM 版本是 2，旧数据用的是版本 1，且版本 1 的封装仍在 Redis 里。
        """
        old_key = _aes_key_bytes(b"o")
        hsm.key_info = {"key_version": 2}
        redis.store[RedisKey.hsm_desens_aes_key(1)] = b"wrapped:" + old_key

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv = os.urandom(12)
        ciphertext = AESGCM(old_key).encrypt(iv, "历史记录".encode(), None)

        assert await service._decrypt_value(ciphertext, iv, 1) == "历史记录"

    async def test_old_version_unavailable_raises(self, service, hsm, redis):
        """旧密钥取到但解不开时必须明确报错，不能返回乱码或空串让上层当成功。

        注意：只有"拿到了封装却解不开"才走 RuntimeError 分支；封装整个缺失时
        代码会继续往下走正常解密路径（那条路径下密文必然解不开，抛 InvalidTag）。
        第一版我按"缺失"构造，结果断言的是一个根本走不到的分支。
        """
        hsm.key_info = {"key_version": 2}
        redis.store[RedisKey.hsm_desens_aes_key(1)] = b"broken-wrapping"

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        iv = os.urandom(12)
        ciphertext = AESGCM(_aes_key_bytes()).encrypt(iv, b"payload", None)

        with pytest.raises(RuntimeError, match="key_version"):
            await service._decrypt_value(ciphertext, iv, 1)


class TestSingleton:
    def test_returns_same_instance(self, monkeypatch):
        monkeypatch.setattr(dr_module, "_desensitization_restore_instance", None)

        assert get_desensitization_restore_service() is get_desensitization_restore_service()

    def test_reset_creates_new_instance(self, monkeypatch):
        monkeypatch.setattr(dr_module, "_desensitization_restore_instance", None)
        first = get_desensitization_restore_service()

        monkeypatch.setattr(dr_module, "_desensitization_restore_instance", None)

        assert get_desensitization_restore_service() is not first

    def test_instances_have_independent_rate_limit_counters(self):
        """限流计数是实例状态：共享会让两个场景互相把对方限流掉。"""
        first = DesensitizationRestoreService()
        second = DesensitizationRestoreService()

        first._rate_limit_counter["x"] = 1

        assert second._rate_limit_counter == {}


class TestRateLimit:
    async def test_allows_under_limit(self, service, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 5)

        assert await service._check_rate_limit() is True

    async def test_blocks_over_limit(self, service, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 2)

        assert await service._check_rate_limit() is True
        assert await service._check_rate_limit() is True
        assert await service._check_rate_limit() is False, "超过上限必须拒绝（防止批量拉取病史）"

    async def test_window_reset_allows_again(self, service, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 1)
        await service._check_rate_limit()
        assert await service._check_rate_limit() is False

        # 把窗口起点推到过去，模拟跨窗口
        service._rate_limit_window_start = datetime.now(timezone.utc) - timedelta(hours=2)

        assert await service._check_rate_limit() is True

    async def test_async_lock_is_per_instance(self):
        """asyncio 锁必须每实例一个：复用会绑死到旧事件循环（本项目修过同类死锁）。"""
        assert DesensitizationRestoreService()._async_lock is not (
            DesensitizationRestoreService()._async_lock
        )


class TestPersistentRateLimit:
    """跨进程限流（Redis 计数）。

    这段被 sec 目录的 session-autouse fixture 整体打桩（`_check_rate_limit_persistent`
    直接 return True），所以**只有在本目录里才测得到**。

    设计要点：先在进程内挡一次（`_check_rate_limit`），再查 Redis；Redis 计数超限时把
    进程内那次自增**回退**掉，避免"Redis 说超了、本地却已经算了一次"的双重计费。
    Redis 自身故障时**放行**（fail-open）：限流是保护措施，不能因为它挂了就把还原
    功能整体停掉——那会让医生在紧急情况下看不到既往记录。
    """

    async def test_process_gate_blocks_before_redis(self, service, monkeypatch):
        monkeypatch.setattr(service, "_check_rate_limit", AsyncMock(return_value=False))
        get_client = AsyncMock()
        monkeypatch.setattr(
            "app.config.redis.RedisPool.get_client", get_client
        )

        assert await service._check_rate_limit_persistent() is False
        get_client.assert_not_awaited()

    async def test_within_limit_allows(self, service, redis, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 10)
        monkeypatch.setattr(service, "_check_rate_limit", AsyncMock(return_value=True))
        redis.eval_result = 10

        assert await service._check_rate_limit_persistent() is True

    async def test_over_limit_blocks_and_alerts(self, service, redis, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 5)
        monkeypatch.setattr(service, "_check_rate_limit", AsyncMock(return_value=True))
        redis.eval_result = 6
        exceeded = AsyncMock()
        monkeypatch.setattr(service, "_handle_rate_limit_exceeded", exceeded)

        assert await service._check_rate_limit_persistent() is False
        assert exceeded.await_count == 1

    async def test_local_counter_compensated_on_redis_rejection(self, service, redis, monkeypatch):
        """Redis 判定超限时要回退本地自增，否则本地窗口会提前被自己撑满。

        必须让**真实的** `_check_rate_limit` 参与（它负责自增）：第一版我把它打桩成
        直接返回 True，于是本地计数压根没加过，补偿再减一反而把种子值改小了。
        同时要预设窗口起点，否则窗口重置会把种子计数清空。
        """
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 5)
        hour_key = datetime.now(timezone.utc).strftime("%Y%m%d%H")
        service._rate_limit_window_start = datetime.now(timezone.utc)
        service._rate_limit_counter[hour_key] = 3
        redis.eval_result = 99
        monkeypatch.setattr(service, "_handle_rate_limit_exceeded", AsyncMock())

        assert await service._check_rate_limit_persistent() is False

        assert service._rate_limit_counter[hour_key] == 3, "应回退到调用前的计数"

    async def test_redis_down_fails_open(self, service, monkeypatch):
        """Redis 不可用时必须放行（限流是保护措施，不是可用性前提）。"""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 1)
        monkeypatch.setattr(service, "_check_rate_limit", AsyncMock(return_value=True))

        async def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr(
            "app.config.redis.RedisPool.get_client", staticmethod(_boom)
        )

        assert await service._check_rate_limit_persistent() is True

    async def test_eval_failure_fails_open(self, service, redis, monkeypatch):
        """EVAL 失败（脚本被禁用等）同样放行，只留日志。"""
        from app.config.settings import settings

        monkeypatch.setattr(settings, "desensitization_restore_rate_limit", 1)
        monkeypatch.setattr(service, "_check_rate_limit", AsyncMock(return_value=True))
        redis.eval_error = RuntimeError("EVAL disabled")

        assert await service._check_rate_limit_persistent() is True


class TestRateLimitAlerting:
    async def test_alert_failure_is_swallowed(self, service, monkeypatch):
        """告警通道失败不能把限流本身变成异常。"""
        monkeypatch.setattr(
            "app.security.restore_rate_monitor.get_restore_rate_monitor",
            MagicMock(side_effect=RuntimeError("monitor down")),
        )

        await service._raise_rate_limit_alert(count=10, limit=1, hour_key="2026-09-24-10")

    async def test_handle_exceeded_survives_monitor_error(self, service, monkeypatch):
        monkeypatch.setattr(
            "app.security.restore_rate_monitor.get_restore_rate_monitor",
            MagicMock(side_effect=RuntimeError("monitor down")),
        )

        await service._handle_rate_limit_exceeded(count=10, limit=1, hour_key="h")
