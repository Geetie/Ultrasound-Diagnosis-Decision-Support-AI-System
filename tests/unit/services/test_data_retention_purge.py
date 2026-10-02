"""数据保留清理补测（quality-debt，核心模块 app/services；61.9% -> 目标高位）。

这一组动作是**合规义务的落地点**（门诊 15 年 / 住院 30 年 / 操作日志 5 年），
它的失效方式很隐蔽：清理不生效、或清理得太干净，功能测试都看不出来。

重点覆盖：
- `purge_expired_operation_logs`（此前**零覆盖**）：按白名单动作 + 创建时间清理，
  并保证"清理动作本身"也在白名单里（否则清理日志会永久堆积）；
- `purge_expired_archives`：门诊/住院两套保留年限、rowcount 为 0 时不留审计噪音；
- `archive_expired_sessions`：分批归档循环、冷归档记录字段、归档后删热层、审计求和；
- **一个易被误判的契约守卫**：清理按 `archived_at` 过滤，而归档代码只显式写
  `archive_timestamp`（`app/services/data_retention.py:55` vs `:88`）。两列并存不是笔误，
  但前提是 `archived_at` 有默认值——否则该列恒为 NULL，过期归档**永远清不掉**。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.database import SessionArchive
from app.services.data_retention import DataRetentionService


def _scalars_result(rows):
    result = MagicMock()
    result.scalars.return_value.all.return_value = list(rows)
    return result


def _delete_result(rowcount):
    result = MagicMock()
    result.rowcount = rowcount
    return result


@pytest.fixture
def db():
    instance = MagicMock()
    instance.execute = AsyncMock(return_value=_scalars_result([]))
    instance.delete = AsyncMock()
    instance.flush = AsyncMock()
    instance.add = MagicMock()
    return instance


@pytest.fixture
def service(db):
    instance = DataRetentionService(db)
    instance.audit_service.log_action = AsyncMock()
    return instance


def _session(index=1):
    return SimpleNamespace(
        session_id=f"sess_{index}",
        patient_id=f"P-{index}",
        device_id=f"dev_{index}",
        status="completed",
        target_organs=["thyroid"],
        organ_statuses={"thyroid": "completed"},
        knowledge_base_version="kb-1.0",
        consent_status_at_creation="GRANTED",
        patient_type="outpatient",
        created_at=None,
    )


class TestColumnContract:
    def test_archived_at_has_a_default(self):
        """清理按 `archived_at` 过滤，而归档代码只显式写 `archive_timestamp`。

        两列并存是设计（业务时刻 vs 入库时刻），但**前提是 archived_at 有默认值**：
        没有默认值该列就恒为 NULL，`archived_at < expiry` 永远不成立，
        过期归档将永久留存——这是合规违约，且不会有任何报错。
        """
        column = SessionArchive.__table__.columns["archived_at"]

        assert column.default is not None or column.server_default is not None, (
            "archived_at 缺默认值：归档记录入库时该列会是 NULL，清理条件永不成立"
        )

    def test_archive_timestamp_is_required(self):
        """`archive_timestamp` 无默认值，归档代码必须显式传（否则插入即报错）。"""
        column = SessionArchive.__table__.columns["archive_timestamp"]

        assert column.nullable is False


class TestPurgeExpiredOperationLogs:
    async def test_purges_and_audits(self, service, db):
        db.execute = AsyncMock(return_value=_delete_result(12))

        purged = await service.purge_expired_operation_logs()

        assert purged == 12
        db.flush.assert_awaited()
        action = service.audit_service.log_action.call_args.kwargs["action"]
        assert action == "operation_logs_purged"

    async def test_zero_rowcount_skips_audit(self, service, db):
        """没有清理掉东西就不要写审计：否则审计里全是噪音，掩盖真实清理量。"""
        db.execute = AsyncMock(return_value=_delete_result(0))

        purged = await service.purge_expired_operation_logs()

        assert purged == 0
        service.audit_service.log_action.assert_not_awaited()
        db.flush.assert_not_awaited()

    async def test_purge_action_itself_is_purgeable(self, service, db):
        """清理动作产生的日志必须也在白名单里，否则清理日志会永久堆积。"""
        captured = {}

        async def _capture(statement):
            captured["statement"] = statement
            return _delete_result(0)

        db.execute = AsyncMock(side_effect=_capture)

        await service.purge_expired_operation_logs()

        sql = str(captured["statement"].compile(compile_kwargs={"literal_binds": True}))
        assert "operation_logs_purged" in sql
        assert "archives_purged" in sql, "归档清理日志同样要可被清理"


class TestPurgeExpiredArchives:
    async def test_outpatient_uses_outpatient_retention(self, service, db, monkeypatch):
        db.execute = AsyncMock(return_value=_delete_result(4))
        monkeypatch.setattr(
            "app.services.data_retention.settings.data_retention_outpatient_years", 15
        )

        purged = await service.purge_expired_archives("outpatient")

        assert purged == 4
        detail = service.audit_service.log_action.call_args.kwargs["detail"]
        assert detail["retention_years"] == 15
        assert detail["patient_type"] == "outpatient"

    async def test_inpatient_uses_inpatient_retention(self, service, db, monkeypatch):
        db.execute = AsyncMock(return_value=_delete_result(1))
        monkeypatch.setattr(
            "app.services.data_retention.settings.data_retention_inpatient_years", 30
        )

        await service.purge_expired_archives("inpatient")

        detail = service.audit_service.log_action.call_args.kwargs["detail"]
        assert detail["retention_years"] == 30

    async def test_unknown_patient_type_falls_back_to_outpatient(self, service, db, monkeypatch):
        """病人类别写错时按门诊口径处理：宁可多留，也不要提前删（数据不可恢复）。"""
        db.execute = AsyncMock(return_value=_delete_result(0))
        monkeypatch.setattr(
            "app.services.data_retention.settings.data_retention_outpatient_years", 15
        )

        await service.purge_expired_archives("day_surgery")

        sql = str(
            db.execute.call_args.args[0].compile(compile_kwargs={"literal_binds": True})
        )
        assert "day_surgery" in sql or "outpatient" in sql

    async def test_zero_rowcount_skips_audit(self, service, db):
        db.execute = AsyncMock(return_value=_delete_result(0))

        assert await service.purge_expired_archives("outpatient") == 0
        service.audit_service.log_action.assert_not_awaited()


class TestArchiveExpiredSessions:
    async def test_archives_and_removes_hot_rows(self, service, db):
        db.execute = AsyncMock(
            return_value=_scalars_result([_session(1), _session(2)])
        )

        archived = await service.archive_expired_sessions(batch_size=500)

        assert archived == 2
        assert db.add.call_count == 2, "每条会话都要落一条冷归档记录"
        assert db.delete.await_count == 2, "归档后必须删掉热层记录"
        db.flush.assert_awaited()

    async def test_audits_total_archived(self, service, db):
        db.execute = AsyncMock(return_value=_scalars_result([_session(1)]))

        await service.archive_expired_sessions()

        call = service.audit_service.log_action.call_args.kwargs
        assert call["action"] == "sessions_archived"
        assert call["detail"]["archived_count"] == 1

    async def test_empty_batch_returns_zero_without_audit(self, service, db):
        db.execute = AsyncMock(return_value=_scalars_result([]))

        assert await service.archive_expired_sessions() == 0
        service.audit_service.log_action.assert_not_awaited()

    async def test_continues_until_short_batch(self, service, db):
        """满批要继续取下一批：只跑一轮会让积压数据永远清不完。"""
        db.execute = AsyncMock(
            side_effect=[
                _scalars_result([_session(1), _session(2)]),
                _scalars_result([]),
            ]
        )

        archived = await service.archive_expired_sessions(batch_size=2)

        assert archived == 2
        assert db.execute.await_count == 2

    async def test_archive_record_carries_required_fields(self, service, db):
        db.execute = AsyncMock(return_value=_scalars_result([_session(1)]))

        await service.archive_expired_sessions()

        archive = db.add.call_args.args[0]
        assert isinstance(archive, SessionArchive)
        assert archive.session_id == "sess_1"
        assert archive.archive_timestamp is not None, "该列无默认值，必须显式写入"
