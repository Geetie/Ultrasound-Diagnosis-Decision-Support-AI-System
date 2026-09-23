import asyncio
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

import httpx
import structlog

from app.config.settings import settings

_logger = structlog.get_logger("app.observability.vm_backup")


class VictoriaMetricsBackup:
    def __init__(
        self,
        vm_url: Optional[str] = None,
        backup_dir: Optional[str] = None,
        vm_data_path: Optional[str] = None,
        retention_days: int = 7,
        interval_hours: int = 1,
        on_failure: Optional[Callable[[str], None]] = None,
    ):
        self._vm_url = vm_url or settings.obs_victoriametrics_url
        self._backup_dir = Path(backup_dir or "./data/vm_backups")
        self._vm_data_path = Path(vm_data_path or "/victoria-metrics-data")
        self._retention_days = retention_days
        self._interval_hours = interval_hours
        self._on_failure = on_failure or self._default_failure_callback
        self._task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._backup_count = 0
        self._fail_count = 0

    @staticmethod
    def _default_failure_callback(message: str) -> None:
        try:
            from app.observability.alert_escalation.manager import get_alert_escalation_manager
            manager = get_alert_escalation_manager()
            manager.emit_alert(
                level="warning",
                metric="vm_backup_failure",
                value=1.0,
                threshold=0.0,
                message=message,
            )
        except Exception:
            _logger.error("vm_backup_alert_emit_failed")

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown_event.clear()
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(self._backup_loop())
        _logger.info(
            "vm_backup_started",
            vm_url=self._vm_url,
            backup_dir=str(self._backup_dir),
            interval_hours=self._interval_hours,
            retention_days=self._retention_days,
        )

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=30.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
        _logger.info(
            "vm_backup_stopped",
            backup_count=self._backup_count,
            fail_count=self._fail_count,
        )

    async def _backup_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._perform_backup()
            except Exception as e:
                _logger.error("vm_backup_failed", error=str(e))
                self._fail_count += 1
                if self._on_failure:
                    try:
                        self._on_failure(f"VictoriaMetrics备份失败: {e}")
                    except Exception as cb_err:
                        _logger.error("vm_backup_failure_callback_error", error=str(cb_err))
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=self._interval_hours * 3600,
                )
            except asyncio.TimeoutError:
                pass

    async def _perform_backup(self) -> None:
        snapshot_name = await self._create_snapshot()
        if not snapshot_name:
            raise RuntimeError("Failed to create VictoriaMetrics snapshot")
        try:
            snapshot_path = self._vm_data_path / "snapshots" / snapshot_name
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            archive_name = f"vm_snapshot_{timestamp}"
            archive_path = self._backup_dir / archive_name
            await asyncio.to_thread(
                shutil.make_archive,
                str(archive_path),
                "gztar",
                str(snapshot_path),
            )
            self._backup_count += 1
            _logger.info(
                "vm_backup_completed",
                snapshot=snapshot_name,
                archive=f"{archive_name}.tar.gz",
            )
        finally:
            await self._delete_snapshot(snapshot_name)
            await self._cleanup_old_backups()

    async def _create_snapshot(self) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                url = f"{self._vm_url}/snapshot/create"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    snapshot_name = data.get("snapshot")
                    _logger.info("vm_snapshot_created", snapshot=snapshot_name)
                    return snapshot_name
                _logger.error(
                    "vm_snapshot_create_failed",
                    status_code=resp.status_code,
                    body=resp.text[:200],
                )
        except Exception as e:
            _logger.error("vm_snapshot_create_error", error=str(e))
        return None

    async def _delete_snapshot(self, snapshot_name: str) -> None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                url = f"{self._vm_url}/snapshot/delete?snapshot={snapshot_name}"
                resp = await client.get(url)
                if resp.status_code == 200:
                    _logger.info("vm_snapshot_deleted", snapshot=snapshot_name)
                else:
                    _logger.warning(
                        "vm_snapshot_delete_failed",
                        snapshot=snapshot_name,
                        status_code=resp.status_code,
                    )
        except Exception as e:
            _logger.warning("vm_snapshot_delete_error", snapshot=snapshot_name, error=str(e))

    async def _cleanup_old_backups(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self._retention_days)
        try:
            for f in self._backup_dir.iterdir():
                if not f.is_file() or not f.name.startswith("vm_snapshot_"):
                    continue
                if not f.name.endswith(".tar.gz"):
                    continue
                try:
                    date_str = f.name.replace("vm_snapshot_", "").replace(".tar.gz", "")
                    file_time = datetime.strptime(date_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                    if file_time < cutoff:
                        f.unlink()
                        _logger.info("vm_backup_cleaned", file=f.name)
                except ValueError:
                    continue
        except Exception as e:
            _logger.warning("vm_backup_cleanup_error", error=str(e))

    def get_stats(self) -> Dict[str, object]:
        return {
            "backup_count": self._backup_count,
            "fail_count": self._fail_count,
            "vm_url": self._vm_url,
            "backup_dir": str(self._backup_dir),
            "retention_days": self._retention_days,
            "interval_hours": self._interval_hours,
        }
