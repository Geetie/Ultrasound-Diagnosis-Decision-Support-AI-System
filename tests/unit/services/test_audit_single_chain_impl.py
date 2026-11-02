"""A.134 / G50 余项：审计链遍历只能有**一份**实现。

G50 指出"两实现混写"：`audit_service.verify_all_chains` 曾内联复制一份与
`audit_chain/verify_utils.verify_chain_integrity` 相同的遍历循环。两份一旦边界条件不同，
同一份数据会给出不同结论 —— 审计工具最不该出现的不确定性。

本组守卫：①`verify_all_chains` 必须委托共享实现；②源码里不得再出现内联的
`if log.hash_prev != prev_hash` 判定；③两条路径对同一数据结论一致（行为等价）。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from app.observability.audit_chain.verify_utils import verify_chain_integrity
from app.services import audit_service as svc_mod


def test_verify_all_chains_delegates_to_shared_impl():
    src = inspect.getsource(svc_mod.AuditService.verify_all_chains)
    assert "verify_chain_integrity" in src, "应委托共享实现，而不是自己再写一份遍历"
    assert "if log.hash_prev != prev_hash" not in src, "内联复制又回来了（两实现漂移风险）"


def test_shared_impl_detects_broken_chain():
    rows = [
        SimpleNamespace(log_id=1, hash_prev=None, hash_self="a"),
        SimpleNamespace(log_id=2, hash_prev="a", hash_self="b"),
    ]
    assert verify_chain_integrity(rows) == (True, None, None, None)

    broken = [
        SimpleNamespace(log_id=1, hash_prev=None, hash_self="a"),
        SimpleNamespace(log_id=2, hash_prev="WRONG", hash_self="b"),
    ]
    ok, broken_id, expected, actual = verify_chain_integrity(broken)
    assert ok is False and broken_id == 2 and expected == "a" and actual == "WRONG"


def test_shared_impl_rejects_non_genesis_first_row():
    rows = [SimpleNamespace(log_id=1, hash_prev="x", hash_self="a")]
    ok, broken_id, _, actual = verify_chain_integrity(rows)
    assert (ok, broken_id, actual) == (False, 1, "x")


@pytest.mark.asyncio
async def test_service_result_matches_shared_impl():
    """同一份数据：服务层输出与共享实现逐字段一致（防"两路径不同结论"）。"""
    rows = [
        SimpleNamespace(session_id="s1", log_id=1, hash_prev=None, hash_self="a"),
        SimpleNamespace(session_id="s1", log_id=2, hash_prev="a", hash_self="b"),
    ]
    expect = verify_chain_integrity(rows)

    from unittest.mock import AsyncMock, MagicMock

    db = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)

    out = await svc_mod.AuditService(db=db).verify_all_chains()
    assert out["s1"] == expect
