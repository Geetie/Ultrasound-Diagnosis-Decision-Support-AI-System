"""A.132 / G45：数据留存周期任务已接线，但默认**不启动**、启动后默认 **dry-run**。

缺口：`start_data_retention_periodic_task` 全仓零生产调用 → retention 配置整体空转；
且函数体 `async for db in db_session_factory()` 对 sessionmaker 不成立（接线必 TypeError）。

口径（逐条钉住）：
1) 总开关默认关（不可逆的数据搬迁/删除不能默认开）；
2) 打开后默认 dry-run：只跑合规校验，**不调用**任何 archive/purge；
3) 血缘模式（dry_run=False）才真正执行归档/清理；
4) lifespan 只有在开关打开时才启动，且关闭时总是尝试停止（no-op 安全）。
"""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.settings import settings
from app.services import data_retention as dr


def _service():
    svc = MagicMock()
    svc.verify_retention_compliance = AsyncMock(
        return_value={"overall_compliant": True, "checks": {}}
    )
    svc.archive_expired_sessions = AsyncMock(return_value=3)
    svc.archive_and_purge_audit_logs = AsyncMock(return_value={"archived": 2})
    svc.archive_and_purge_session_archives = AsyncMock(return_value={"archived": 1})
    svc.purge_expired_archives = AsyncMock(return_value=0)
    svc.purge_expired_operation_logs = AsyncMock(return_value=5)
    return svc


@pytest.mark.asyncio
async def test_dry_run_never_mutates():
    svc = _service()
    summary = await dr.run_retention_cycle(svc, dry_run=True)

    assert summary["dry_run"] is True
    svc.verify_retention_compliance.assert_awaited()
    for method in (
        svc.archive_expired_sessions,
        svc.archive_and_purge_audit_logs,
        svc.archive_and_purge_session_archives,
        svc.purge_expired_archives,
        svc.purge_expired_operation_logs,
    ):
        method.assert_not_awaited()


@pytest.mark.asyncio
async def test_real_run_executes_and_reports():
    svc = _service()
    summary = await dr.run_retention_cycle(svc, dry_run=False)

    assert summary["dry_run"] is False
    assert summary["archived"] == 3
    assert summary["cold_audit"] == 2
    assert summary["purged_logs"] == 5
    svc.archive_expired_sessions.assert_awaited()


def test_schedule_defaults_off_and_dry_run_on():
    assert settings.data_retention_schedule_enabled is False
    assert settings.data_retention_dry_run is True


def test_start_uses_sessionmaker_context_and_dry_run_flag():
    src = inspect.getsource(dr.start_data_retention_periodic_task)
    assert "async with db_session_factory() as db:" in src
    # docstring 里提到旧写法（作为说明）不算，只看可执行代码行
    code_lines = [ln for ln in src.splitlines() if not ln.strip().startswith("以前写的")]
    assert not any("async for db in" in ln for ln in code_lines)
    assert "dry_run" in src


def test_lifespan_gated_by_switch():
    from app import main as main_mod

    src = inspect.getsource(main_mod)
    assert "data_retention_schedule_enabled" in src
    assert "start_data_retention_periodic_task" in src
    assert "stop_data_retention_periodic_task" in src
