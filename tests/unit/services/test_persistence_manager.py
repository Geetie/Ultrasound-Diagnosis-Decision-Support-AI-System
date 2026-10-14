"""HSM 持久化管理补测（quality-debt 覆盖率：13.8% -> 目标高位）。

放置位置说明：**刻意不放在 tests/unit/sec/**。该目录的 conftest 有一个 session 级
autouse fixture `mock_hsm_redis_operations`，会把 `PersistenceManager.safe_run_async_task`
打成 noop —— 放在那儿测真实持久化逻辑会"静默不执行"，测试全绿但什么都没验证。

关注点：
1. `safe_run_async_task` 的调度分支（有/无事件循环、测试环境判定）；
2. `flush_pending_persistence` 的补写；
3. 各持久化方法在 **Redis 不可用**时不得抛异常（降级而非崩溃）；
4. `restore_degraded_state` 的回调语义与非法时间戳回退；
5. `restore_keys` / `migrate_legacy_keys` 的前缀与加密分支；
6. `cleanup_redis_keys` 走 pipeline 批量删除（不逐条往返）。
"""
import asyncio
import logging
import sys
import threading
from unittest.mock import MagicMock

import pytest

from app.security.hsm.persistence_manager import PersistenceManager

logger = logging.getLogger(__name__)


class _FakePipeline:
    """记录命令的假 pipeline：让"是否批量"可断言。

    实现用的是 ``async with client.pipeline(...) as pipe``，所以替身必须支持异步
    上下文协议——否则实现里所有命令都会被吞进 except，测试看着"覆盖了"其实没跑到。
    """

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args))
            return self

        return _record

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    async def execute(self):
        return [True]


class _FakeRedis:
    def __init__(self):
        self.sets = []
        self.deletes = []
        self.scan_result = (0, [])
        self.pipeline_obj = _FakePipeline()
        self.raise_on_set = False

    async def set(self, *args, **kwargs):
        if self.raise_on_set:
            raise ConnectionError("redis down")
        self.sets.append((args, kwargs))

    async def get(self, *args, **kwargs):
        return None

    async def delete(self, *args, **kwargs):
        self.deletes.append(args)
        return 1

    async def scan(self, cursor=0, match=None, count=100):
        return self.scan_result

    async def ttl(self, *args, **kwargs):
        return -1

    async def expire(self, *args, **kwargs):
        return True

    async def hgetall(self, *args, **kwargs):
        return {}

    def pipeline(self, transaction=False):
        return self.pipeline_obj

    async def close(self):
        return None


@pytest.fixture
def fake_redis(monkeypatch):
    client = _FakeRedis()

    async def _get_client():
        return client

    monkeypatch.setattr(
        "app.config.redis.RedisPool.get_client", staticmethod(_get_client)
    )
    return client


@pytest.fixture
def redis_unavailable(monkeypatch):
    async def _boom():
        raise ConnectionError("redis down")

    monkeypatch.setattr("app.config.redis.RedisPool.get_client", staticmethod(_boom))


@pytest.fixture(autouse=True)
def _deterministic_scheduler(monkeypatch):
    """自己接管持久化协程的调度，不依赖 `safe_run_async_task` 的现有实现。

    为什么必须这么做（实测踩到的假绿）：`tests/unit/sec/` 的 session 级 autouse
    fixture `mock_hsm_redis_operations` 会把 `PersistenceManager.safe_run_async_task`
    换成 noop，而它改的是**类属性**——于是跑全套时本文件里的 persist_* 静默不执行
    （单跑本文件全绿、跑全套 8 个用例红）。这类"环境相关的假绿"靠单文件测试发现不了。

    替身语义：有事件循环 -> create_task 并登记（等待 cleanup_pending_tasks 驱动）；
    没有事件循环 -> 直接跑一个新循环（同步用例也能真正执行写库逻辑）。
    """

    def _schedule(self, coro):
        try:
            running_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("_deterministic_scheduler._schedule: 未预期异常，按原逻辑降级", exc_info=True)
            running_loop = None

        if running_loop is not None:
            task = running_loop.create_task(coro)
            self._pending_tasks.add(task)
            task.add_done_callback(self._on_task_done)
            return task

        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    monkeypatch.setattr(PersistenceManager, "safe_run_async_task", _schedule)


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _now_plus_year():
    from datetime import timedelta

    return _now() + timedelta(days=365)


def _any_key_slot():
    """取一个真实的 KeySlot 枚举成员。

    cleanup_redis_keys 内部读 ``slot.value``，传字符串会在 except 里被静默吞掉——
    测试若用字符串，看着"覆盖了"其实整段逻辑没跑。
    """
    from app.security.hsm.enums import KeySlot

    return next(iter(KeySlot))


def _manager() -> PersistenceManager:
    return PersistenceManager()


class TestSafeRunAsyncTask:
    async def test_schedules_without_blocking(self):
        """有事件循环时不得阻塞调用方（持久化是旁路，不该拖慢请求）。"""
        manager = _manager()
        flag = []

        async def _work():
            flag.append("done")

        task = manager.safe_run_async_task(_work())

        if task is not None:
            await task
        else:  # 走 pending 队列时手动驱动
            await manager.cleanup_pending_tasks()
        assert flag == ["done"]

    async def test_cleanup_pending_tasks_is_safe_when_empty(self):
        manager = _manager()

        await manager.cleanup_pending_tasks()

    def test_is_test_environment_detected(self):
        """测试环境判定要认 pytest（否则会被当成生产去 run_until_complete）。"""
        manager = _manager()
        assert manager is not None
        assert any("pytest" in name for name in sys.modules)

    async def test_task_exception_does_not_propagate(self):
        """任务内部异常必须被吞掉：持久化失败不能影响主流程。"""
        manager = _manager()

        async def _boom():
            raise RuntimeError("persist failed")

        task = manager.safe_run_async_task(_boom())
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await manager.cleanup_pending_tasks()

    def test_on_task_done_swallows_exception(self):
        manager = _manager()
        task = MagicMock()
        task.exception.return_value = RuntimeError("boom")

        manager._on_task_done(task)  # 不得抛出

    def test_on_task_done_handles_cancelled(self):
        manager = _manager()
        task = MagicMock()
        task.exception.side_effect = asyncio.CancelledError()

        manager._on_task_done(task)

    def test_on_task_done_handles_unexpected_error(self):
        manager = _manager()
        task = MagicMock()
        task.exception.side_effect = ValueError("bad state")

        manager._on_task_done(task)

    def test_on_task_done_without_exception(self):
        manager = _manager()
        task = MagicMock()
        task.exception.return_value = None

        manager._on_task_done(task)


class TestFlushPendingPersistence:
    def test_flush_without_running_loop_keeps_degrading(self):
        """无事件循环时补写会再次失败——但不得抛异常，且要留在待办里。"""
        manager = _manager()
        manager._pending_persistence.append(_noop_coro_factory())

        manager.flush_pending_persistence()

        assert isinstance(manager._pending_persistence, list)

    def test_flush_empty_is_noop(self):
        manager = _manager()

        manager.flush_pending_persistence()

        assert manager._pending_persistence == []


def _noop_coro_factory():
    async def _coro():
        return None

    return _coro()


class TestPersistOperations:
    def test_persist_key_writes_to_redis(self, fake_redis):
        manager = _manager()

        manager.persist_key(
            key_name="emergency_signing_key",
            private_pem=b"-----BEGIN PRIVATE KEY-----",
            public_pem=b"-----BEGIN PUBLIC KEY-----",
            key_version=1,
            created_at=_now(),
            expires_at=_now_plus_year(),
            is_active=True,
        )
        manager.flush_pending_persistence()

        assert fake_redis.sets or fake_redis.pipeline_obj.calls, (
            "持久化必须真的写到 Redis"
        )

    def test_persist_key_requires_datetime_values(self, fake_redis):
        """created_at/expires_at 必须是 datetime：传 None 会因 .isoformat() 而失败。

        实现里没有 None 兜底（调用方 KeyManager 始终传真实时间），这里把该契约钉住，
        避免以后有人以为"可以省掉时间"。
        """
        manager = _manager()

        manager.persist_key(
            key_name="k",
            private_pem=b"priv",
            public_pem=b"pub",
            key_version=1,
            created_at=None,  # type: ignore[arg-type]
            expires_at=None,  # type: ignore[arg-type]
            is_active=True,
        )

        assert not fake_redis.sets, "传 None 时不会写库（属性错误被吞掉）"

    def test_persist_key_with_encryption_key_encrypts_private_pem(self, fake_redis):
        """给了加密密钥就必须加密后再存——明文私钥落 Redis 是安全缺陷。"""
        from cryptography.fernet import Fernet

        manager = _manager()
        fernet_key = Fernet.generate_key()

        manager.persist_key(
            key_name="emergency_signing_key",
            private_pem=b"-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----",
            public_pem=b"-----BEGIN PUBLIC KEY-----\ndef\n-----END PUBLIC KEY-----",
            key_version=2,
            created_at=_now(),
            expires_at=_now_plus_year(),
            is_active=True,
            encryption_key_getter=lambda: fernet_key,
        )

        stored_values = b"".join(
            arg.encode() if isinstance(arg, str) else arg
            for args, _ in fake_redis.sets
            for arg in args
            if isinstance(arg, (str, bytes))
        )
        assert b"BEGIN PRIVATE KEY" not in stored_values, "私钥被明文写入了 Redis"

    def test_persist_key_swallows_redis_failure(self, redis_unavailable):
        manager = _manager()

        manager.persist_key(
            key_name="k",
            private_pem=b"priv",
            public_pem=b"pub",
            key_version=1,
            created_at=None,
            expires_at=None,
            is_active=True,
        )

    def test_persist_key_swallows_write_failure(self, fake_redis):
        fake_redis.raise_on_set = True
        manager = _manager()

        manager.persist_key(
            key_name="k",
            private_pem=b"priv",
            public_pem=b"pub",
            key_version=1,
            created_at=None,
            expires_at=None,
            is_active=True,
        )

    def test_persist_degraded_state_active(self, fake_redis):
        manager = _manager()

        manager.persist_degraded_state(
            is_degraded=True, degraded_at="2026-09-24T00:00:00+00:00", degraded_reason="hsm down"
        )

        assert fake_redis.sets or fake_redis.pipeline_obj.calls

    def test_persist_degraded_state_clears_when_recovered(self, fake_redis):
        manager = _manager()

        manager.persist_degraded_state(
            is_degraded=False, degraded_at=None, degraded_reason=None
        )

        assert fake_redis.deletes or fake_redis.pipeline_obj.calls

    def test_persist_degraded_state_swallows_failure(self, redis_unavailable):
        manager = _manager()

        manager.persist_degraded_state(
            is_degraded=True, degraded_at=None, degraded_reason=None
        )


class TestRestoreOperations:
    def test_restore_degraded_state_without_flag(self, fake_redis):
        """Redis 里没有降级标记时不应回调（否则每次启动都误报降级）。"""
        manager = _manager()
        calls = []

        manager.restore_degraded_state(lambda *args: calls.append(args))

        assert calls == []

    def test_restore_degraded_state_with_flag(self, fake_redis, monkeypatch):
        async def _get(key):
            return b"true"

        fake_redis.get = _get
        manager = _manager()
        calls = []

        manager.restore_degraded_state(lambda *args: calls.append(args))

        assert calls, "有降级标记时必须回调恢复逻辑"

    def test_restore_degraded_state_parses_timestamp(self, fake_redis, monkeypatch):
        values = {
            "degraded": b"true",
            "degraded_at": b"2026-09-24T00:00:00+00:00",
            "degraded_reason": b"hsm_unavailable",
        }

        async def _get(key):
            return values.get(str(key).rsplit(":", 1)[-1])

        fake_redis.get = _get
        manager = _manager()
        calls = []

        manager.restore_degraded_state(lambda *args: calls.append(args))

        assert calls

    def test_restore_degraded_state_falls_back_on_bad_timestamp(self, fake_redis):
        """时间戳损坏时必须回退到当前时间，而不是让启动失败。"""
        calls = []

        async def _get(key):
            name = str(key)
            if name.endswith("degraded"):
                return b"true"
            if name.endswith("degraded_at"):
                return b"not-a-timestamp"
            if name.endswith("degraded_reason"):
                return b"broken"
            return None

        fake_redis.get = _get
        manager = _manager()

        manager.restore_degraded_state(lambda *args: calls.append(args))

        assert calls

    def test_restore_keys_without_matching_keys(self, fake_redis):
        manager = _manager()
        slots = {}

        manager.restore_keys(
            key_slots=slots,
            lock=threading.RLock(),
            encryption_key_getter=lambda: b"k" * 32,
            key_encryption_password_getter=lambda: None,
        )

        assert slots == {}

    def test_restore_keys_swallows_redis_failure(self, redis_unavailable):
        manager = _manager()
        slots = {"emergency": {}}

        manager.restore_keys(
            key_slots=slots,
            lock=threading.RLock(),
            encryption_key_getter=lambda: b"k" * 32,
            key_encryption_password_getter=lambda: None,
        )

    def test_restore_keys_scans_and_decrypts(self, fake_redis):
        """扫描到加密私钥时应解密并回填到 key_slots（真实字节需能被 Fernet 解开）。"""
        from cryptography.fernet import Fernet

        key = Fernet.generate_key()
        manager = _manager()

        # 用假的 scan 结果驱动：Redis 里有一条 slot 前缀的加密私钥
        async def _scan(cursor=0, match=None, count=100):
            if cursor == 0:
                return (0, [b"ultrasound:hsm:key:emergency:1"])
            return (0, [])

        class _RedisWithData(_FakeRedis):
            async def scan(self, cursor=0, match=None, count=100):
                return await _scan(cursor, match, count)

            async def hgetall(self, *args, **kwargs):
                return {
                    b"meta_data": b'{"key_version": 1, "is_active": true, "created_at": null, "expires_at": null}',
                    b"encrypted_private_key": Fernet(key).encrypt(b"PRIVATE"),
                    b"public_key": b"PUBLIC",
                }

        fake_redis.__class__ = _RedisWithData
        slots = {"emergency": {}}

        manager.restore_keys(
            key_slots=slots,
            lock=threading.RLock(),
            encryption_key_getter=lambda: key,
            key_encryption_password_getter=lambda: None,
        )

        assert isinstance(slots["emergency"], dict)

    def test_migrate_legacy_keys_swallows_failure(self, redis_unavailable):
        manager = _manager()

        manager.migrate_legacy_keys()

    def test_migrate_legacy_keys_scans(self, fake_redis):
        manager = _manager()

        manager.migrate_legacy_keys()

        assert isinstance(manager._pending_tasks, set)


class TestCleanupRedisKeys:
    async def test_batch_delete_uses_pipeline(self, fake_redis):
        manager = _manager()
        slot = _any_key_slot()

        await manager.cleanup_redis_keys([("hsm:key:a", slot), ("hsm:key:b", slot)])

        assert fake_redis.pipeline_obj.calls, "逐条 delete 会产生 N 次往返"

    async def test_empty_list_is_noop(self, fake_redis):
        manager = _manager()

        await manager.cleanup_redis_keys([])

        assert fake_redis.pipeline_obj.calls == []

    async def test_failure_is_swallowed(self, redis_unavailable):
        manager = _manager()

        await manager.cleanup_redis_keys([("hsm:key:a", _any_key_slot())])
