"""并发与性能审查回归（2026-10-03，app/security 侧）。

- ``RedisRefreshTokenStore.rotate_refresh_token`` 原是「GET → 判 revoked → MULTI 写」
  的 check-then-act：``pipeline(transaction=True)`` 只保证**写批次**原子，判据在事务
  之外。两个并发 rotate 会同时读到 ``revoked=false``，各自生成一个新 token ——
  同一 refresh token 派生出**两个有效后继**，reuse 检测被永久绕过（令牌复制）。
  现用 per-token ``asyncio.Lock`` 串行化（单 worker 部署闭合；跨 worker 需 Lua/WATCH，
  已登记为残留风险）。
- ``desensitization`` 的规则发布/令牌持久化原先是裸 ``ensure_future``：不持引用
  → 可能被 GC 静默丢弃；异常无人消费；无上限。
"""
import asyncio

import pytest

from app.security import desensitization as desens_module
from app.security import refresh_token as rt_module
from app.security.refresh_token import RedisRefreshTokenStore

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _reset_rotate_locks():
    store = RedisRefreshTokenStore()
    with store._rotate_locks_guard:
        store._rotate_locks.clear()
    yield


class TestRotateIsSerializedPerToken:
    async def test_concurrent_rotates_do_not_overlap(self, monkeypatch):
        """同一 token 的并发 rotate 必须串行：内层实现的并发峰值只能是 1。"""
        store = RedisRefreshTokenStore()
        inflight = 0
        peak = 0
        order: list[str] = []

        async def _fake_locked(old_token_id: str) -> str:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            order.append("enter")
            await asyncio.sleep(0.02)
            inflight -= 1
            order.append("exit")
            return "new-token"

        monkeypatch.setattr(store, "_rotate_refresh_token_locked", _fake_locked)
        results = await asyncio.gather(
            *(store.rotate_refresh_token("tok-A") for _ in range(5))
        )
        assert results == ["new-token"] * 5
        assert peak == 1
        # 进入/退出严格交替，不存在交叉
        assert order == ["enter", "exit"] * 5

    async def test_different_tokens_are_not_serialized(self, monkeypatch):
        """不同 token 之间不得互相阻塞（否则会成为全局串行点）。"""
        store = RedisRefreshTokenStore()
        inflight = 0
        peak = 0

        async def _fake_locked(old_token_id: str) -> str:
            nonlocal inflight, peak
            inflight += 1
            peak = max(peak, inflight)
            await asyncio.sleep(0.02)
            inflight -= 1
            return f"new-{old_token_id}"

        monkeypatch.setattr(store, "_rotate_refresh_token_locked", _fake_locked)
        await asyncio.gather(
            store.rotate_refresh_token("tok-A"),
            store.rotate_refresh_token("tok-B"),
            store.rotate_refresh_token("tok-C"),
        )
        assert peak == 3

    async def test_exception_propagates_and_lock_is_released(self, monkeypatch):
        """内层抛错（reuse 检测路径）必须原样抛出，且锁要释放给后续调用。"""
        store = RedisRefreshTokenStore()
        calls = []

        async def _boom(old_token_id: str) -> str:
            calls.append(old_token_id)
            raise ValueError("Refresh token has already been used")

        monkeypatch.setattr(store, "_rotate_refresh_token_locked", _boom)
        with pytest.raises(ValueError):
            await store.rotate_refresh_token("tok-A")
        with pytest.raises(ValueError):
            await store.rotate_refresh_token("tok-A")
        assert calls == ["tok-A", "tok-A"]

    async def test_lock_registry_is_bounded(self, monkeypatch):
        """锁字典有上界，长跑进程不得因轮换次数增长而无界膨胀。"""
        monkeypatch.setattr(rt_module, "_MAX_ROTATE_LOCKS", 4)
        store = RedisRefreshTokenStore()
        for i in range(50):
            lock = store._rotate_locks.get(f"tok-{i}")
            assert lock is None or lock in store._rotate_locks.values()
        # 直接构造 50 个锁后触发一次回收
        for i in range(50):
            store._rotate_locks[f"tok-{i}"] = asyncio.Lock()

        async def _noop(old_token_id: str) -> str:
            return "ok"

        store._rotate_refresh_token_locked = _noop
        await store.rotate_refresh_token("tok-new")
        assert len(store._rotate_locks) <= 5


class TestDesensitizationBackgroundTasks:
    @pytest.fixture(autouse=True)
    def _reset_tasks(self):
        desens_module._BACKGROUND_TASKS.clear()
        yield
        for task in list(desens_module._BACKGROUND_TASKS):
            task.cancel()
        desens_module._BACKGROUND_TASKS.clear()

    async def test_task_is_tracked_until_done(self):
        gate = asyncio.Event()

        async def _work():
            await gate.wait()

        desens_module._spawn_background(_work())
        assert len(desens_module._BACKGROUND_TASKS) == 1
        gate.set()
        await asyncio.sleep(0.01)
        assert len(desens_module._BACKGROUND_TASKS) == 0

    async def test_exception_is_consumed(self):
        async def _boom():
            raise RuntimeError("publish failed")

        desens_module._spawn_background(_boom())
        await asyncio.sleep(0.01)
        assert len(desens_module._BACKGROUND_TASKS) == 0

    async def test_saturation_drops_instead_of_growing(self, monkeypatch):
        monkeypatch.setattr(desens_module, "_MAX_BACKGROUND_TASKS", 0)

        async def _work():  # pragma: no cover - 不应执行
            raise AssertionError("饱和时不应调度")

        desens_module._spawn_background(_work())
        await asyncio.sleep(0)
        assert len(desens_module._BACKGROUND_TASKS) == 0
