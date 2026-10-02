"""数据保留的冷热分层与合规校验补测（quality-debt，核心模块 app/services）。

两条不可妥协的语义：
1. **冷层不可用时不删任何热数据**（T-GB-SEC-02）——"清理"绝不能变成"把本应
   入冷层的数据直接删掉"；
2. **合规违约必须留痕**——`verify_retention_compliance` 发现超标时要写审计并 flush，
   否则违规只存在于返回值里，没人会去看。

冷层包装通过 patch `ColdArchiveService` 断言委托关系与 storage 透传；
本模块的周期任务体由 lifespan 驱动，这里只测启停纪律（理由同审计侧）。
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import data_retention as dr_module
from app.services.data_retention import (
    DataRetentionService,
    start_data_retention_periodic_task,
    stop_data_retention_periodic_task,
)


class _ColdResult:
    def __init__(self, payload=None):
        self._payload = payload or {"archived": 3, "failed": 0, "pending": 0}

    def as_dict(self):
        return dict(self._payload)


@pytest.fixture(autouse=True)
def _reset_periodic(monkeypatch):
    monkeypatch.setattr(dr_module, "_retention_running", False)
    monkeypatch.setattr(dr_module, "_retention_task", None)


@pytest.fixture
def db():
    instance = MagicMock()
    instance.flush = AsyncMock()
    return instance


@pytest.fixture
def service(db):
    instance = DataRetentionService(db)
    instance.audit_service.log_action = AsyncMock()
    return instance


class TestColdTierWrappers:
    async def test_audit_logs_delegate_to_cold_service(self, service, monkeypatch):
        cold = MagicMock()
        cold.archive_audit_logs = AsyncMock(return_value=_ColdResult())
        monkeypatch.setattr(dr_module, "ColdArchiveService", MagicMock(return_value=cold))

        result = await service.archive_and_purge_audit_logs(before="2026-01-01")

        assert result == {"archived": 3, "failed": 0, "pending": 0}
        cold.archive_audit_logs.assert_awaited_once_with(before="2026-01-01")

    async def test_storage_is_passed_through(self, service, monkeypatch):
        """storage 必须透传：测试与运维注入假存储靠它，写死会让注入点失效。"""
        cold = MagicMock()
        cold.archive_session_archives = AsyncMock(return_value=_ColdResult())
        factory = MagicMock(return_value=cold)
        monkeypatch.setattr(dr_module, "ColdArchiveService", factory)
        storage = object()

        await service.archive_and_purge_session_archives("outpatient", storage=storage)

        assert factory.call_args.kwargs["storage"] is storage
        cold.archive_session_archives.assert_awaited_once_with(
            before=None, patient_type="outpatient"
        )

    async def test_cold_failure_does_not_delete_hot_data(self, service, monkeypatch):
        """冷层不可用时不删任何热数据：结果里的 failed/pending 要如实透传。"""
        cold = MagicMock()
        cold.archive_audit_logs = AsyncMock(
            return_value=_ColdResult({"archived": 0, "failed": 5, "pending": 7})
        )
        monkeypatch.setattr(dr_module, "ColdArchiveService", MagicMock(return_value=cold))

        result = await service.archive_and_purge_audit_logs()

        assert result["failed"] == 5
        assert result["pending"] == 7
        service.db.delete.assert_not_called(), "冷层失败路径下不得有任何热层删除"

    async def test_stats_delegates(self, service, monkeypatch):
        cold = MagicMock()
        cold.stats = AsyncMock(return_value={"manifests": 2})
        monkeypatch.setattr(dr_module, "ColdArchiveService", MagicMock(return_value=cold))

        assert await service.archive_audit_manifest_stats() == {"manifests": 2}


class TestVerifyRetentionCompliance:
    async def test_all_compliant_when_counts_are_zero(self, service, db, monkeypatch):
        monkeypatch.setattr(
            dr_module.settings, "data_retention_outpatient_years", 15
        )
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute = AsyncMock(return_value=count_result)

        report = await service.verify_retention_compliance()

        assert report["overall_compliant"] is True
        assert set(report["checks"]) == {
            "unarchived_expired_sessions",
            "overdue_archive_purge",
            "overdue_inpatient_archive_purge",
        }
        assert all(
            check["compliant"] for check in report["checks"].values()
        )
        service.audit_service.log_action.assert_not_awaited()

    async def test_config_block_exposes_effective_settings(self, service, db, monkeypatch):
        """报告里的配置必须是**实际生效值**：否则合规报告无法对着配置追责。"""
        monkeypatch.setattr(
            dr_module.settings, "session_archive_after_days", 90
        )
        count_result = MagicMock()
        count_result.scalar.return_value = 0
        db.execute = AsyncMock(return_value=count_result)

        report = await service.verify_retention_compliance()

        assert report["config"]["session_archive_after_days"] == 90

    async def test_violation_is_audited(self, service, db):
        """发现超标必须写审计并 flush：违规只存在于返回值里等于没人知道。"""
        first = MagicMock()
        first.scalar.return_value = 2   # 有 2 条过期会话未归档
        second = MagicMock()
        second.scalar.return_value = 0
        third = MagicMock()
        third.scalar.return_value = 0
        db.execute = AsyncMock(side_effect=[first, second, third])

        report = await service.verify_retention_compliance()

        assert report["overall_compliant"] is False
        assert report["checks"]["unarchived_expired_sessions"]["count"] == 2
        service.audit_service.log_action.assert_awaited_once()
        assert (
            service.audit_service.log_action.call_args.kwargs["action"]
            == "retention_compliance_violation"
        )
        db.flush.assert_awaited()


class TestPeriodicTaskLifecycle:
    async def test_start_creates_task(self, db):
        await start_data_retention_periodic_task(MagicMock(return_value=db), interval_hours=1)
        try:
            assert dr_module._retention_running is True
            assert dr_module._retention_task is not None
        finally:
            await stop_data_retention_periodic_task()

    async def test_double_start_does_not_stack_tasks(self, db):
        await start_data_retention_periodic_task(MagicMock(return_value=db), interval_hours=1)
        first = dr_module._retention_task
        try:
            await start_data_retention_periodic_task(MagicMock(return_value=db), interval_hours=1)
            assert dr_module._retention_task is first
        finally:
            await stop_data_retention_periodic_task()

    async def test_stop_cancels_and_resets(self, db):
        await start_data_retention_periodic_task(MagicMock(return_value=db), interval_hours=1)

        await stop_data_retention_periodic_task()

        assert dr_module._retention_running is False
        assert dr_module._retention_task is None

    async def test_stop_without_start_is_safe(self):
        await stop_data_retention_periodic_task()
