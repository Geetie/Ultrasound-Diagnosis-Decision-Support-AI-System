"""审计链校验补测（quality-debt，核心模块 app/services）。

写入链（log_action）只是半个故事——**校验**才是让"不可篡改"成立的另一半：
链条断了却没有被例行校验发现，等于没有审计链。

覆盖：
- `verify_chain`：首条必须无 prev、中间断链要给出"期望哈希 vs 实际哈希"（定位篡改点）；
- `verify_all_chains`：按会话分组、各会话独立判定（None 会话=全局链）；
- 周期校验任务的启停守卫（重复启动不得叠加任务、stop 必须真正取消）。

任务体本身（真正跑校验的那段）由 lifespan 在生产中驱动，这里只测启停纪律——
用 sleep 打桩去强跑任务体需要全局替换 asyncio.sleep，侵入性大于收益。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import audit_service as audit_module
from app.services.audit_service import (
    AuditService,
    start_periodic_verification,
    stop_periodic_verification,
)


def _rows_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    return result


def _log(log_id, session_id, hash_prev, hash_self):
    return SimpleNamespace(
        log_id=log_id,
        session_id=session_id,
        hash_prev=hash_prev,
        hash_self=hash_self,
    )


@pytest.fixture(autouse=True)
def _reset_periodic(monkeypatch):
    """周期任务是模块级全局：不清理会跨用例残留（并让"重复启动"断言失效）。"""
    monkeypatch.setattr(audit_module, "_periodic_running", False)
    monkeypatch.setattr(audit_module, "_periodic_task", None)


class TestVerifyChain:
    async def test_empty_chain_is_valid(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_rows_result([]))

        assert await AuditService(db).verify_chain("sess_1") == (True, None, None, None)

    async def test_valid_chain_passes(self):
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=_rows_result(
                [
                    _log("l1", "s", None, "H1"),
                    _log("l2", "s", "H1", "H2"),
                ]
            )
        )

        assert await AuditService(db).verify_chain("s") == (True, None, None, None)

    async def test_first_record_must_not_have_previous(self):
        """首条带 prev 说明它前面还有记录——要么被删了，要么链被重放了。"""
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=_rows_result([_log("l1", "s", "SUSPICIOUS", "H1")])
        )

        valid, broken_id, expected, actual = await AuditService(db).verify_chain("s")

        assert valid is False
        assert broken_id == "l1"
        assert expected is None
        assert actual == "SUSPICIOUS"

    async def test_broken_link_locates_the_tamper_point(self):
        """断链必须给出"期望哈希 vs 实际哈希"，否则无法定位是哪条被改。"""
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=_rows_result(
                [
                    _log("l1", "s", None, "H1"),
                    _log("l2", "s", "FORGED", "H2"),
                ]
            )
        )

        valid, broken_id, expected, actual = await AuditService(db).verify_chain("s")

        assert valid is False
        assert broken_id == "l2"
        assert expected == "H1"
        assert actual == "FORGED"


class TestVerifyAllChains:
    async def test_reports_each_session_independently(self):
        """一个会话断链不能殃及其他会话的判定（否则告警会淹没真实问题）。"""
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=_rows_result(
                [
                    _log("a1", "sess_a", None, "HA1"),
                    _log("a2", "sess_a", "HA1", "HA2"),
                    _log("b1", "sess_b", None, "HB1"),
                    _log("b2", "sess_b", "TAMPERED", "HB2"),
                ]
            )
        )

        results = await AuditService(db).verify_all_chains()

        assert results["sess_a"][0] is True
        assert results["sess_b"] == (False, "b2", "HB1", "TAMPERED")

    async def test_none_session_forms_its_own_global_chain(self):
        """session_id 为空的记录（全局操作）自成一条链，不能混进别的会话。"""
        db = MagicMock()
        db.execute = AsyncMock(
            return_value=_rows_result(
                [
                    _log("g1", None, None, "HG1"),
                    _log("g2", None, "HG1", "HG2"),
                ]
            )
        )

        results = await AuditService(db).verify_all_chains()

        assert results[None] == (True, None, None, None)

    async def test_empty_database_is_valid(self):
        db = MagicMock()
        db.execute = AsyncMock(return_value=_rows_result([]))

        assert await AuditService(db).verify_all_chains() == {}


class TestPeriodicVerificationLifecycle:
    async def test_start_creates_task(self):
        await start_periodic_verification(interval_hours=1)
        try:
            assert audit_module._periodic_running is True
            assert audit_module._periodic_task is not None
        finally:
            await stop_periodic_verification()

    async def test_double_start_does_not_stack_tasks(self):
        """重复启动会叠加校验任务：同一会话被并发校验，结果互相踩。"""
        await start_periodic_verification(interval_hours=1)
        first = audit_module._periodic_task
        try:
            await start_periodic_verification(interval_hours=1)
            assert audit_module._periodic_task is first
        finally:
            await stop_periodic_verification()

    async def test_stop_cancels_and_resets(self):
        await start_periodic_verification(interval_hours=1)

        await stop_periodic_verification()

        assert audit_module._periodic_running is False
        assert audit_module._periodic_task is None

    async def test_stop_without_start_is_safe(self):
        await stop_periodic_verification()
