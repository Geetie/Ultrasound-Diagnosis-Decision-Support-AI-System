import asyncio
import json
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import aiofiles
import structlog

from app.observability.enums import LogCategory

from .backend import LogStorageBackend
from .record import LogRecord

_logger = structlog.get_logger("app.observability.logging")


class FileLogStorage(LogStorageBackend):
    DEFAULT_LOG_DIR = "/var/log/ultrasound/"
    FILE_PERMISSIONS = 0o640
    MAX_FILE_SIZE = 100 * 1024 * 1024
    ROTATION_CHECK_INTERVAL = 100

    def __init__(
        self,
        log_dir: Optional[str] = None,
        max_file_size: int = MAX_FILE_SIZE,
        retention_days: int = 7,
    ):
        self.log_dir = Path(log_dir or self.DEFAULT_LOG_DIR)
        self.max_file_size = max_file_size
        self.retention_days = retention_days
        self._write_count = 0
        self._current_files: Dict[str, Path] = {}
        self._lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            self.log_dir = Path.home() / ".ultrasound" / "logs"
            self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_log_file_path(self, category) -> Path:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        category_value = category.value if hasattr(category, 'value') else category
        filename = f"{category_value}_{today}.jsonl"
        if filename not in self._current_files:
            self._current_files[filename] = self.log_dir / filename
        return self._current_files[filename]

    def _should_rotate(self, file_path: Path) -> bool:
        try:
            if file_path.exists():
                return file_path.stat().st_size >= self.max_file_size
        except OSError:
            pass
        return False

    def _rotate_file(self, file_path: Path) -> None:
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rotated_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
            rotated_path = file_path.parent / rotated_name
            file_path.rename(rotated_path)
        except OSError:
            pass

    async def awrite(self, record: LogRecord) -> None:
        file_path = self._get_log_file_path(record.category)

        async with self._get_async_lock():
            if self._should_rotate(file_path):
                self._rotate_file(file_path)

            try:
                async with aiofiles.open(file_path, mode="a") as f:
                    await f.write(json.dumps(record.to_dict(), default=str) + "\n")

                self._write_count += 1
                if self._write_count >= self.ROTATION_CHECK_INTERVAL:
                    self._write_count = 0
                    await self._cleanup_old_files()
            except OSError as e:
                _logger.warning("Primary log directory failed, using fallback",
                                error=str(e),
                                primary_dir=str(self.log_dir))
                fallback_dir = Path.home() / ".ultrasound" / "logs"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                fallback_path = fallback_dir / file_path.name
                try:
                    async with aiofiles.open(fallback_path, mode="a") as f:
                        await f.write(json.dumps(record.to_dict(), default=str) + "\n")
                except Exception as fallback_e:
                    _logger.error("Failed to write to fallback log directory",
                                  error=str(fallback_e))

    async def awrite_batch(self, records: List[LogRecord]) -> None:
        if not records:
            return

        records_by_category = {}
        for record in records:
            category = LogCategory(record.category)
            if category not in records_by_category:
                records_by_category[category] = []
            records_by_category[category].append(record)

        async with self._get_async_lock():
            for category, category_records in records_by_category.items():
                file_path = self._get_log_file_path(category)

                if self._should_rotate(file_path):
                    self._rotate_file(file_path)

                try:
                    lines = [json.dumps(r.to_dict(), default=str) + "\n" for r in category_records]
                    async with aiofiles.open(file_path, mode="a") as f:
                        await f.writelines(lines)

                    self._write_count += len(category_records)
                    if self._write_count >= self.ROTATION_CHECK_INTERVAL:
                        self._write_count = 0
                        await self._cleanup_old_files()

                    _logger.debug("Batch write to file completed",
                                  category=category.value,
                                  count=len(category_records))
                except OSError as e:
                    _logger.warning("Primary log directory failed for batch, using fallback",
                                    error=str(e),
                                    primary_dir=str(self.log_dir))
                    fallback_dir = Path.home() / ".ultrasound" / "logs"
                    fallback_dir.mkdir(parents=True, exist_ok=True)
                    fallback_path = fallback_dir / file_path.name
                    try:
                        lines = [json.dumps(r.to_dict(), default=str) + "\n" for r in category_records]
                        async with aiofiles.open(fallback_path, mode="a") as f:
                            await f.writelines(lines)
                    except Exception as fallback_e:
                        _logger.error("Failed to write batch to fallback log directory",
                                      error=str(fallback_e))
                        for record in category_records:
                            await self.awrite(record)

    async def _cleanup_old_files(self) -> None:
        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)
            deleted_count = 0
            for file_path in self.log_dir.glob("*.jsonl"):
                try:
                    mtime = datetime.fromtimestamp(file_path.stat().st_mtime, tz=timezone.utc)
                    if mtime < cutoff:
                        file_path.unlink()
                        deleted_count += 1
                except OSError:
                    pass
            if deleted_count > 0:
                _logger.info("Cleaned up old log files",
                             count=deleted_count,
                             cutoff=cutoff.isoformat())
        except Exception as e:
            _logger.error("Error cleaning up old files", error=str(e))

    async def close(self) -> None:
        pass
