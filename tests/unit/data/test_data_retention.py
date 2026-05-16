import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone, timedelta

from app.services.data_retention import DataRetentionService
pytestmark = [pytest.mark.track_data, pytest.mark.track_sec]


class TestArchiveExpiredSessions:
    """测试过期会话归档"""

    @pytest.mark.asyncio
    async def test_archive_completed_sessions(self):
        """超过7天的已完成会话应被归档"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        mock_session = MagicMock()
        mock_session.session_id = "S001"
        mock_session.patient_id = "P001"
        mock_session.device_id = "D001"
        mock_session.kb_version = "1.0"
        mock_session.status = "completed"
        mock_session.created_at = datetime.now(timezone.utc) - timedelta(days=10)
        mock_session.completed_at = datetime.now(timezone.utc) - timedelta(days=10)
        mock_session.expired_at = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_session]
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.delete = AsyncMock()
        mock_db.flush = AsyncMock()

        with patch.object(service.audit_service, 'log_action', new_callable=AsyncMock):
            count = await service.archive_expired_sessions()

        assert count == 1
        mock_db.add.assert_called_once()
        mock_db.delete.assert_called_once_with(mock_session)

    @pytest.mark.asyncio
    async def test_no_expired_sessions(self):
        """无过期会话时不执行归档"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        count = await service.archive_expired_sessions()
        assert count == 0

    @pytest.mark.asyncio
    async def test_active_sessions_not_archived(self):
        """活跃会话不应被归档"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        count = await service.archive_expired_sessions()
        assert count == 0


class TestPurgeExpiredArchives:
    """测试过期归档清理"""

    @pytest.mark.asyncio
    async def test_purge_outpatient_archives(self):
        """门诊数据超过15年应被清理"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        mock_result = MagicMock()
        mock_result.rowcount = 5
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()

        with patch.object(service.audit_service, 'log_action', new_callable=AsyncMock):
            count = await service.purge_expired_archives("outpatient")

        assert count == 5

    @pytest.mark.asyncio
    async def test_purge_inpatient_archives(self):
        """住院数据超过30年应被清理"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        mock_result = MagicMock()
        mock_result.rowcount = 2
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.flush = AsyncMock()

        with patch.object(service.audit_service, 'log_action', new_callable=AsyncMock):
            count = await service.purge_expired_archives("inpatient")

        assert count == 2


class TestVerifyRetentionCompliance:
    """测试保留合规性验证"""

    @pytest.mark.asyncio
    async def test_compliant_when_no_issues(self):
        """无违规时合规检查通过"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 0
        mock_db.execute = AsyncMock(return_value=mock_count_result)

        report = await service.verify_retention_compliance()

        assert report["overall_compliant"] is True
        assert report["checks"]["unarchived_expired_sessions"]["compliant"] is True
        assert report["checks"]["overdue_archive_purge"]["compliant"] is True

    @pytest.mark.asyncio
    async def test_non_compliant_when_unarchived_sessions_exist(self):
        """存在未归档过期会话时合规检查失败"""
        mock_db = AsyncMock()
        service = DataRetentionService(mock_db)

        call_count = 0

        async def mock_execute(query):
            nonlocal call_count
            call_count += 1
            result = MagicMock()
            if call_count == 1:
                result.scalar.return_value = 3
            else:
                result.scalar.return_value = 0
            return result

        mock_db.execute = mock_execute
        mock_db.flush = AsyncMock()

        with patch.object(service.audit_service, 'log_action', new_callable=AsyncMock):
            report = await service.verify_retention_compliance()

        assert report["overall_compliant"] is False
        assert report["checks"]["unarchived_expired_sessions"]["compliant"] is False
        assert report["checks"]["unarchived_expired_sessions"]["count"] == 3


class TestRetentionConfig:
    """测试保留策略配置"""

    def test_default_retention_values(self):
        """默认保留期配置应符合医疗法规"""
        from app.config.settings import Settings

        s = Settings()
        assert s.data_retention_outpatient_years == 15
        assert s.data_retention_inpatient_years == 30
        assert s.data_retention_audit_years == 30
        assert s.data_retention_operation_years == 5
        assert s.session_archive_after_days == 7
