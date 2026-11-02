"""A.119 / G44：跨链锚存证必须真的发生（此前只"构造"不"写"）。

缺口实测：
1. `start_audit_chain_periodic_tasks` 在 app/ 内**零调用**；
2. 函数体里 `AuditCrossChain(db)` 只构造、`record_cross_chain()` 从未调用；
3. `async for db in db_session_factory()` 对 sessionmaker 不成立（接线必踩）。

本组守卫：
1) 一轮 = 校验 + 写锚，且锚带上一锚 hash（形成链）；
2) 校验结果为断链时记 critical（但**仍然**写锚，不能因为发现断链就不留证）；
3) 单轮失败不让周期任务退出；
4) lifespan 启动/停止都接线。
"""

from __future__ import annotations

import asyncio
import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.observability.audit_chain import periodic


class _FakeDB:
    def __init__(self, latest_hash=None):
        self.committed = False
        self.latest_hash = latest_hash
        self.execute = AsyncMock(return_value=SimpleNamespace(first=lambda: (latest_hash,) if latest_hash else None))

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_cycle_verifies_and_records_anchor(monkeypatch):
    db = _FakeDB(latest_hash="prev123")
    verified: list[int] = []
    recorded: list[tuple] = []

    class _Verifier:
        def __init__(self, _db):
            pass

        async def verify_all(self):
            verified.append(1)
            return {
                "all_valid": True,
                "session_chains": {"total": 3, "broken": []},
                "critical_chains": {"total": 1, "broken": []},
            }

    class _CrossChain:
        def __init__(self, _db):
            pass

        async def record_cross_chain(self, start, end, prev):
            recorded.append((start, end, prev))
            return "anchor-hash"

    monkeypatch.setattr(periodic, "AuditChainVerifier", _Verifier)
    monkeypatch.setattr(periodic, "AuditCrossChain", _CrossChain)

    out = await periodic.run_audit_chain_cycle(
        db, window_hours=1, now=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)
    )

    assert verified, "没有跑完整性校验"
    assert recorded, "只构造了 AuditCrossChain，没有真的写锚（G44 原样）"
    start, end, prev = recorded[0]
    assert prev == "prev123"
    assert end.hour == 12 and (end - start).total_seconds() == 3600
    assert out["anchor"] == "anchor-hash"
    assert db.committed is True


@pytest.mark.asyncio
async def test_cycle_still_records_anchor_when_chain_broken(monkeypatch):
    db = _FakeDB()
    recorded: list[int] = []

    class _Verifier:
        def __init__(self, _db):
            pass

        async def verify_all(self):
            return {
                "all_valid": False,
                "session_chains": {"total": 1, "broken": ["s1"]},
                "critical_chains": {"total": 0, "broken": []},
            }

    class _CrossChain:
        def __init__(self, _db):
            pass

        async def record_cross_chain(self, start, end, prev):
            recorded.append(1)
            return "h"

    monkeypatch.setattr(periodic, "AuditChainVerifier", _Verifier)
    monkeypatch.setattr(periodic, "AuditCrossChain", _CrossChain)

    await periodic.run_audit_chain_cycle(db, window_hours=1)
    assert recorded, "发现断链时更应留证（锚不能因为断链就不写）"


@pytest.mark.asyncio
async def test_periodic_loop_survives_failure(monkeypatch):
    calls = {"n": 0}

    async def boom(db, *, window_hours, now=None):
        calls["n"] += 1
        raise RuntimeError("db down")

    class _CM:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *exc):
            return False

    def factory():
        return _CM()

    monkeypatch.setattr(periodic, "run_audit_chain_cycle", boom)
    monkeypatch.setattr(periodic, "_periodic_running", True, raising=False)
    stop = asyncio.Event()
    task = asyncio.create_task(
        periodic._loop(factory, window_hours=1, interval_s=0.01, stop=stop)
    )
    await asyncio.sleep(0.06)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)
    periodic._periodic_running = False
    assert calls["n"] >= 2, "单轮失败后周期任务退出了（缺口会静默复发）"


def test_lifespan_wires_start_and_stop():
    from app import main as main_mod

    src = inspect.getsource(main_mod)
    assert "start_audit_chain_periodic_tasks(" in src
    assert "stop_audit_chain_periodic_tasks" in src
