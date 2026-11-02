"""A.131 / G50：审计链周期校验改为**增量**（不再每小时全表扫描）。

缺口：活路径 `audit_service.verify_all_chains()` 无参调用会把全表审计日志读进内存、
按会话分组；数据量涨上去后每小时一次全表扫描（同时 `verifier.verify_all` 是另一份实现，
G50 指出的"两实现混写"）。

本组守卫：
1) 传 `since` → 先按时间挑受影响会话，**只取这些会话**的完整链（链必须从头算）；
2) 无受影响会话 → 直接返回 {}，不再查第二遍；
3) 不传 `since` → 保持全量（首轮/手工调用语义不变）；
4) 周期任务：首轮全量、其后增量且带 60s 重叠（容忍时钟漂移，宁可多校验不漏校验）。
"""

from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import audit_service as svc_mod
from app.services.audit_service import AuditService


def _db(*results):
    db = MagicMock()
    db.execute = AsyncMock(side_effect=list(results))
    return db


def _row(session_id, hash_prev=None, hash_self="h"):
    return SimpleNamespace(session_id=session_id, hash_prev=hash_prev, hash_self=hash_self, log_id=1)


@pytest.mark.asyncio
async def test_incremental_picks_only_touched_sessions():
    # 第 1 次查询：受影响会话；第 2 次查询：这些会话的完整链
    ids_result = MagicMock()
    ids_result.scalars.return_value.all.return_value = ["s1"]
    chain_result = MagicMock()
    chain_result.scalars.return_value.all.return_value = [_row("s1", None, "a"), _row("s1", "a", "b")]
    db = _db(ids_result, chain_result)

    service = AuditService(db=db)
    out = await service.verify_all_chains(since=datetime.now(timezone.utc) - timedelta(hours=1))

    assert out == {"s1": (True, None, None, None)}
    assert db.execute.await_count == 2
    # 第二遍必须按会话过滤（不是全表）
    second_sql = str(db.execute.await_args_list[1].args[0])
    assert "SESSION_ID IN" in second_sql.upper()


@pytest.mark.asyncio
async def test_no_touched_sessions_skips_second_query():
    ids_result = MagicMock()
    ids_result.scalars.return_value.all.return_value = []
    db = _db(ids_result)

    service = AuditService(db=db)
    assert await service.verify_all_chains(since=datetime.now(timezone.utc)) == {}
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_full_mode_still_supported():
    chain_result = MagicMock()
    chain_result.scalars.return_value.all.return_value = [_row("s9", None, "x")]
    db = _db(chain_result)

    service = AuditService(db=db)
    out = await service.verify_all_chains()
    assert out == {"s9": (True, None, None, None)}
    assert db.execute.await_count == 1


@pytest.mark.asyncio
async def test_broken_chain_still_reported_in_incremental_mode():
    ids_result = MagicMock()
    ids_result.scalars.return_value.all.return_value = ["s1"]
    chain_result = MagicMock()
    # 首条 hash_prev 非空 → 断链
    chain_result.scalars.return_value.all.return_value = [_row("s1", "bad", "b")]
    db = _db(ids_result, chain_result)

    out = await AuditService(db=db).verify_all_chains(since=datetime.now(timezone.utc))
    assert out["s1"][0] is False


def test_periodic_uses_incremental_after_first_run():
    src = inspect.getsource(svc_mod.start_periodic_verification)
    assert "_last_verified_at" in src
    assert "scan_mode=\"incremental\" if since else \"full\"" in src or 'scan_mode="incremental" if since else "full"' in src
    assert "_VERIFY_OVERLAP" in src
    # 重叠窗口必须存在且为正（容忍时钟漂移）
    assert svc_mod._VERIFY_OVERLAP.total_seconds() > 0
