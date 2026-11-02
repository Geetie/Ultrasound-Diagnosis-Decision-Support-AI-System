"""A.117 / G46：关键操作必须写**列**，否则会被冷归档当普通记录搬走。

缺口实测：`CriticalOperationAudit` 只把 `is_critical_operation=True` 塞进 `detail`；
`AuditLog.is_critical_operation` 列保持 ORM 默认 False。而冷归档选会话用的是
`is_critical_operation.is_(False)` → 关键审计链会被整条冷归档，
与 `cold_archive.py` 注释"关键操作不参与冷归档"**正好相反**。

本组守卫：
1) 关键写入路径把列置 True；
2) 通用写入路径（`AuditService.log_action`）在 detail 标了标记时镜像到列；
3) 冷归档的选行条件**同时**看列与 detail（保护修复前的历史行）。
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.observability.audit_chain import CriticalOperationAudit
from app.services.audit_service import AuditService


def _scalar_result(value):
    result = MagicMock()
    result.scalar_one_or_none.return_value = value
    return result


@pytest.mark.asyncio
async def test_critical_audit_sets_column_true():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))

    log = await CriticalOperationAudit(db).log_critical_operation(
        operation="threshold_adjustment", operator="admin"
    )

    assert log.is_critical_operation is True, (
        "关键操作只写了 detail、没写列 —— 冷归档会把它当普通记录搬走（G46）"
    )
    assert log.detail["is_critical_operation"] is True


@pytest.mark.asyncio
async def test_generic_log_action_mirrors_detail_flag():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))
    svc = AuditService(db=db)

    log = await svc.log_action(
        action="kb_update",
        actor="admin",
        detail={"is_critical_operation": True, "reason": "x"},
    )
    assert log.is_critical_operation is True


@pytest.mark.asyncio
async def test_generic_log_action_defaults_false():
    db = AsyncMock()
    db.add = MagicMock()
    db.execute = AsyncMock(return_value=_scalar_result(None))
    svc = AuditService(db=db)

    log = await svc.log_action(action="diagnosis_request", actor="doctor")
    assert log.is_critical_operation is False


def test_cold_archive_guards_historical_rows():
    """历史行：列 False 但 detail 标了 True 时也不能被归档。"""
    src = inspect.getsource(
        __import__("app.services.cold_archive", fromlist=["x"]).ColdArchiveService._find_archivable_sessions
    )
    assert "is_critical_operation.is_(False)" in src
    assert "detail['is_critical_operation']" in src or 'detail["is_critical_operation"]' in src
    assert "!= \"true\"" in src or "!= 'true'" in src
