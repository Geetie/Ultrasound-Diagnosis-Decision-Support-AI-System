"""审计链补测（quality-debt，核心模块 app/services；66.4% -> 目标高位）。

审计链的价值全在**不可篡改**上，而"能不能篡改"只能靠链结构本身判断：
- 每条记录的 `hash_prev` 必须等于同会话上一条的 `hash_self`（断链＝可被悄悄删改）；
- `hash_self` 必须把 detail 按固定顺序参与哈希（否则同一内容、不同 key 顺序会算出两个哈希，
  校验时就无法区分"合法"与"被改过"）；
- 分片锁必须按会话隔离，否则并发写入会交错形成断链；锁表还必须有上限，
  否则长跑服务的会话数会把内存吃穿。

本文件覆盖 `log_action` 链写入本体、`_get_last_hash`、`_ensure_db` 与分片锁工具，
全部离线（fake db），不需要真实 PG。
"""
import asyncio
import hashlib
import json
from datetime import timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import audit_service as audit_module
from app.services.audit_service import (
    AuditService,
    _get_chain_lock,
    release_chain_lock,
)

FIXED_NOW = audit_module.datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class _FrozenDateTime(audit_module.datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _ChainedDb:
    """把最近写入的记录回读给 `_get_last_hash`，用于验证链式衔接。"""

    def __init__(self):
        self.added = []

    async def execute(self, statement):
        return _Result(self.added[-1] if self.added else None)

    def add(self, instance):
        self.added.append(instance)


@pytest.fixture(autouse=True)
def _clean_chain_locks(monkeypatch):
    """锁表是模块级全局：不清理会跨用例残留（并影响并发断言）。"""
    monkeypatch.setattr(audit_module, "_chain_locks", {})
    monkeypatch.setattr(audit_module, "_chain_lock_order", [])


@pytest.fixture
def deterministic(monkeypatch):
    """冻结 uuid 与时钟，让 hash_self 可复现（否则无法断言哈希口径）。"""
    monkeypatch.setattr(audit_module.uuid, "uuid4", lambda: "fix-log-id")
    monkeypatch.setattr(audit_module, "datetime", _FrozenDateTime)


class TestChainLock:
    async def test_same_session_shares_lock(self):
        first = await _get_chain_lock("sess_1")
        second = await _get_chain_lock("sess_1")

        assert first is second, "同会话必须串行写入，否则并发会交错形成断链"

    async def test_different_sessions_get_different_locks(self):
        assert await _get_chain_lock("sess_1") is not await _get_chain_lock("sess_2")

    async def test_none_maps_to_global_key(self):
        assert await _get_chain_lock(None) is await _get_chain_lock(None)
        assert audit_module._chain_locks.get("__global__") is not None

    async def test_lock_table_is_bounded(self, monkeypatch):
        """长跑服务的会话数会无限增长，锁表必须有上限（否则内存被吃穿）。"""
        monkeypatch.setattr(audit_module, "_MAX_CHAIN_LOCKS", 3)

        for index in range(5):
            await _get_chain_lock(f"sess_{index}")

        assert len(audit_module._chain_locks) <= 3
        assert "sess_0" not in audit_module._chain_locks, "应淘汰最早的键"

    async def test_recently_used_lock_survives_eviction(self, monkeypatch):
        """命中要刷新顺序：刚用过的会话不该在下一秒被淘汰。"""
        monkeypatch.setattr(audit_module, "_MAX_CHAIN_LOCKS", 2)
        await _get_chain_lock("sess_a")
        await _get_chain_lock("sess_b")
        await _get_chain_lock("sess_a")  # 刷新 sess_a

        await _get_chain_lock("sess_c")  # 触发淘汰

        assert "sess_a" in audit_module._chain_locks
        assert "sess_b" not in audit_module._chain_locks

    async def test_release_removes_lock(self):
        await _get_chain_lock("sess_1")

        await release_chain_lock("sess_1")

        assert "sess_1" not in audit_module._chain_locks
        assert "sess_1" not in audit_module._chain_lock_order

    async def test_release_missing_lock_is_noop(self):
        await release_chain_lock("never_seen")


class TestEnsureDb:
    async def test_uses_injected_session(self):
        session = MagicMock()

        assert await AuditService(session)._ensure_db() is session

    async def test_acquires_session_lazily(self):
        """未注入时按需取会话，并且要缓存下来（否则每条日志开一个会话）。"""
        session = MagicMock()

        async def _generate():
            yield session

        with patch("app.config.database.get_session", _generate):
            service = AuditService()
            assert await service._ensure_db() is session
            assert service.db is session

    async def test_raises_when_no_session_available(self):
        """取不到会话必须明确报错——静默丢弃审计日志等于审计链条断裂。"""

        async def _empty():
            if False:  # pragma: no cover - 只为构成异步生成器
                yield None

        with (
            patch("app.config.database.get_session", _empty),
            pytest.raises(RuntimeError, match="Failed to obtain database session"),
        ):
            await AuditService()._ensure_db()


class TestGetLastHash:
    async def test_returns_previous_hash(self):
        previous = MagicMock()
        previous.hash_self = "PREV-HASH"
        db = MagicMock()
        db.execute = AsyncMock(return_value=_Result(previous))

        assert await AuditService(db)._get_last_hash("sess_1") == "PREV-HASH"

    async def test_returns_none_on_empty_chain(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_Result(None))

        assert await AuditService(db)._get_last_hash() is None


class TestLogActionChain:
    async def test_first_record_has_no_previous_hash(self, deterministic):
        db = _ChainedDb()
        log = await AuditService(db).log_action(action="session_created", actor="system")

        assert log.hash_prev is None
        assert log.hash_self

    async def test_second_record_links_to_first(self, deterministic):
        """链式衔接是审计的全部意义：断链意味着历史可被悄悄删改。"""
        db = _ChainedDb()
        service = AuditService(db)

        first = await service.log_action(action="a", actor="system")
        second = await service.log_action(action="b", actor="system")

        assert second.hash_prev == first.hash_self

    async def test_hash_covers_action_actor_and_target(self, deterministic):
        db = _ChainedDb()
        service = AuditService(db)

        base = await service.log_action(action="a", actor="system")
        different_actor = await service.log_action(action="a", actor="other")
        with_target = await service.log_action(
            action="a", actor="system", target_type="patient", target_id="P-1"
        )

        assert len({base.hash_self, different_actor.hash_self, with_target.hash_self}) == 3

    async def test_detail_is_hashed_with_sorted_keys(self, deterministic):
        """detail 必须按 key 排序参与哈希。

        否则 `{"a":1,"b":2}` 与 `{"b":2,"a":1}`（同一内容、不同插入顺序）会得到两个
        不同哈希，校验时无法区分"内容没变"与"被改过"。

        注意这里必须用**无前序**的库：第一版我沿用了链式库，第二条记录的 hash_prev
        已经变成第一条的 hash_self，哈希不同是被 prev 带偏的，与 detail 无关。
        """
        db = MagicMock()
        db.execute = AsyncMock(return_value=_Result(None))
        service = AuditService(db)

        first = await service.log_action(
            action="a", actor="system", detail={"alpha": 1, "beta": 2}
        )
        reordered = await service.log_action(
            action="a", actor="system", detail={"beta": 2, "alpha": 1}
        )
        changed = await service.log_action(
            action="a", actor="system", detail={"alpha": 1, "beta": 3}
        )

        assert first.hash_self == reordered.hash_self
        assert first.hash_self != changed.hash_self

    async def test_hash_matches_documented_recipe(self, deterministic):
        """按实现里写明的拼接口径独立算一遍，锁住哈希公式。"""
        db = _ChainedDb()
        log = await AuditService(db).log_action(
            action="a", actor="system", detail={"k": "v"}
        )

        expected_content = (
            f"fix-log-id-a-system-{FIXED_NOW.isoformat()}"
            f"-{json.dumps({'k': 'v'}, sort_keys=True, ensure_ascii=False)}"
        )
        assert log.hash_self == hashlib.sha256(expected_content.encode()).hexdigest()

    async def test_record_persisted_with_metadata(self, deterministic):
        db = _ChainedDb()

        log = await AuditService(db).log_action(
            action="consent_granted",
            actor="doctor_1",
            session_id="sess_1",
            target_type="patient",
            target_id="P-1",
            detail={"field": "name"},
            log_type="operation",
        )

        assert db.added == [log]
        assert log.session_id == "sess_1"
        assert log.log_type == "operation"
        assert log.created_at == FIXED_NOW

    async def test_locks_are_isolated_per_session(self):
        """不同会话各用一把锁：共用一把会让无关会话互相排队（也是跨链污染的入口）。"""
        assert await _get_chain_lock("sess_1") is not await _get_chain_lock("sess_2")

    async def test_concurrent_writes_are_serialized(self, deterministic):
        """并发写同一会话必须串行：交错会形成两条记录指向同一个 prev（链分叉）。"""
        db = _ChainedDb()
        service = AuditService(db)

        async def _write(tag):
            return await service.log_action(
                action=tag, actor="system", session_id="s"
            )

        first, second = await asyncio.gather(_write("x"), _write("y"))

        assert len(db.added) == 2
        assert db.added[1].hash_prev == db.added[0].hash_self, (
            "并发下没串行：第二条没有链到第一条"
        )
        assert {first.hash_self, second.hash_self} == {
            db.added[0].hash_self,
            db.added[1].hash_self,
        }
