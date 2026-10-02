import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.observability.vm_backup import VictoriaMetricsBackup


@pytest.fixture
def backup(tmp_path):
    return VictoriaMetricsBackup(
        vm_url="http://vm:8428",
        backup_dir=str(tmp_path / "backups"),
        vm_data_path=str(tmp_path / "vm_data"),
        retention_days=7,
        interval_hours=1,
    )


@pytest.fixture
def mock_snapshot_response():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {"snapshot": "snap-20260517"}
    resp.text = '{"snapshot":"snap-20260517"}'
    return resp


@pytest.fixture
def mock_delete_response():
    resp = MagicMock()
    resp.status_code = 200
    return resp


@pytest.mark.track_obs
class TestBackupSuccessPath:
    @pytest.mark.asyncio
    async def test_perform_backup_success(self, backup, mock_snapshot_response, mock_delete_response):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_snapshot_response, mock_delete_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.observability.vm_backup.httpx.AsyncClient", return_value=mock_client):
            with patch("app.observability.vm_backup.shutil.make_archive", return_value="/fake/archive.tar.gz"):
                with patch("app.observability.vm_backup.asyncio.to_thread", new_callable=AsyncMock, return_value="/fake/archive.tar.gz"):
                    await backup._perform_backup()

        assert backup._backup_count == 1

    @pytest.mark.asyncio
    async def test_perform_backup_deletes_snapshot_after_archive(self, backup, mock_snapshot_response, mock_delete_response):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[mock_snapshot_response, mock_delete_response])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.observability.vm_backup.httpx.AsyncClient", return_value=mock_client):
            with patch("app.observability.vm_backup.asyncio.to_thread", new_callable=AsyncMock, return_value="/fake/archive.tar.gz"):
                with patch.object(backup, "_cleanup_old_backups", new_callable=AsyncMock):
                    await backup._perform_backup()

        assert mock_client.get.call_count == 2
        second_call_url = mock_client.get.call_args_list[1][0][0]
        assert "snapshot/delete" in second_call_url
        assert "snap-20260517" in second_call_url


@pytest.mark.track_obs
class TestSnapshotCreateFailure:
    @pytest.mark.asyncio
    async def test_create_snapshot_returns_none_on_http_error(self, backup):
        mock_client = AsyncMock()
        resp = MagicMock()
        resp.status_code = 500
        resp.text = "internal error"
        mock_client.get = AsyncMock(return_value=resp)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.observability.vm_backup.httpx.AsyncClient", return_value=mock_client):
            result = await backup._create_snapshot()

        assert result is None

    @pytest.mark.asyncio
    async def test_perform_backup_raises_on_none_snapshot(self, backup):
        with patch.object(backup, "_create_snapshot", new_callable=AsyncMock, return_value=None):
            with pytest.raises(RuntimeError, match="Failed to create VictoriaMetrics snapshot"):
                await backup._perform_backup()

    @pytest.mark.asyncio
    async def test_create_snapshot_returns_none_on_exception(self, backup):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("app.observability.vm_backup.httpx.AsyncClient", return_value=mock_client):
            result = await backup._create_snapshot()

        assert result is None


@pytest.mark.track_obs
class TestCleanupOldBackups:
    @pytest.mark.asyncio
    async def test_old_backups_deleted(self, backup, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        old_date = (datetime.now(timezone.utc) - timedelta(days=10)).strftime("%Y%m%dT%H%M%SZ")
        old_file = backup_dir / f"vm_snapshot_{old_date}.tar.gz"
        old_file.write_text("old")

        recent_date = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%dT%H%M%SZ")
        recent_file = backup_dir / f"vm_snapshot_{recent_date}.tar.gz"
        recent_file.write_text("recent")

        await backup._cleanup_old_backups()

        assert not old_file.exists()
        assert recent_file.exists()

    @pytest.mark.asyncio
    async def test_non_snapshot_files_not_deleted(self, backup, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        other_file = backup_dir / "other_data.txt"
        other_file.write_text("keep")

        await backup._cleanup_old_backups()

        assert other_file.exists()

    @pytest.mark.asyncio
    async def test_malformed_filename_skipped(self, backup, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        bad_file = backup_dir / "vm_snapshot_bad_format.tar.gz"
        bad_file.write_text("bad")

        await backup._cleanup_old_backups()

        assert bad_file.exists()


@pytest.mark.track_obs
class TestDefaultFailureCallback:
    def test_callback_calls_alert_manager(self):
        mock_manager = MagicMock()
        with patch(
            "app.observability.alert_escalation.manager.get_alert_escalation_manager",
            return_value=mock_manager,
        ):
            VictoriaMetricsBackup._default_failure_callback("test failure message")
            mock_manager.emit_alert.assert_called_once_with(
                level="warning",
                metric="vm_backup_failure",
                value=1.0,
                threshold=0.0,
                message="test failure message",
            )

    def test_callback_swallows_exception(self):
        with patch(
            "app.observability.alert_escalation.manager.get_alert_escalation_manager",
            side_effect=Exception("import error"),
        ):
            try:
                VictoriaMetricsBackup._default_failure_callback("test")
            except Exception:
                pytest.fail("_default_failure_callback should not raise")


@pytest.mark.track_obs
class TestStartStopLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, backup):
        with patch.object(backup, "_backup_loop", new_callable=AsyncMock):
            await backup.start()
            assert backup._task is not None
            assert not backup._task.done()
            backup._shutdown_event.set()
            await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_start_idempotent(self, backup):
        with patch.object(backup, "_backup_loop", new_callable=AsyncMock):
            await backup.start()
            first_task = backup._task
            await backup.start()
            assert backup._task is first_task
            backup._shutdown_event.set()
            await asyncio.sleep(0.05)

    @pytest.mark.asyncio
    async def test_stop_sets_shutdown_event(self, backup):
        with patch.object(backup, "_backup_loop", new_callable=AsyncMock):
            await backup.start()
            assert not backup._shutdown_event.is_set()
            await backup.stop()
            assert backup._shutdown_event.is_set()

    @pytest.mark.asyncio
    async def test_stop_cancels_on_timeout(self, backup):
        async def infinite_loop():
            while not backup._shutdown_event.is_set():
                await asyncio.sleep(1)

        backup._backup_loop = infinite_loop
        await backup.start()

        with patch("app.observability.vm_backup.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            await backup.stop()

        assert backup._task.done() or backup._shutdown_event.is_set()


@pytest.mark.track_obs
class TestGetStats:
    def test_returns_correct_stats(self, backup, tmp_path):
        backup._backup_count = 5
        backup._fail_count = 2

        stats = backup.get_stats()

        assert stats["backup_count"] == 5
        assert stats["fail_count"] == 2
        assert stats["vm_url"] == "http://vm:8428"
        assert stats["backup_dir"] == str(tmp_path / "backups")
        assert stats["retention_days"] == 7
        assert stats["interval_hours"] == 1

    def test_default_stats(self, tmp_path):
        b = VictoriaMetricsBackup(
            vm_url="http://vm:8428",
            backup_dir=str(tmp_path / "backups"),
        )
        stats = b.get_stats()
        assert stats["backup_count"] == 0
        assert stats["fail_count"] == 0
