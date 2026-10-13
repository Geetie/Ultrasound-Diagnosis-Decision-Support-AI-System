import asyncio
import json
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
        log_dir: str | None = None,
        max_file_size: int = MAX_FILE_SIZE,
        retention_days: int = 7,
    ):
        self.log_dir = Path(log_dir or self.DEFAULT_LOG_DIR)
        self.max_file_size = max_file_size
        self.retention_days = retention_days
        self._write_count = 0
        self._current_files: dict[str, Path] = {}
        self._lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None
        self._ensure_log_dir()

    def _ensure_log_dir(self) -> None:
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError as exc:
            # A.60：目录无权限时静默改到 home —— 日志**写到了别处**却毫无记录，
            # 运维按原路径找不到日志（"日志怎么没了"）。这是一次降级，必须留痕。
            _logger.warning(
                "log_dir_fallback_to_home",
                wanted=str(self.log_dir),
                fallback=str(Path.home() / ".ultrasound" / "logs"),
                error=str(exc),
            )
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
        except OSError:  # silent-ok: 探测式 stat（读不到即按"不需要轮转"，下次写入再看）
            pass
        return False

    def _rotate_file(self, file_path: Path) -> None:
        try:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            rotated_name = f"{file_path.stem}_{timestamp}{file_path.suffix}"
            rotated_path = file_path.parent / rotated_name
            file_path.rename(rotated_path)
        except OSError as exc:
            # A.60：轮转失败原先静默 —— 后果是同一文件**无限增长**（磁盘告警时才被发现），
            # 而且没有线索说明轮转没成功。
            _logger.warning(
                "log_rotate_failed",
                path=str(file_path),
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def awrite(self, record: LogRecord) -> None:
        async with self._get_async_lock():
            await self._write_record_unlocked(record)

    async def _write_record_unlocked(self, record: LogRecord) -> None:
        """写一条记录。**调用方必须已持有 ``_async_lock``**。

        为什么要把写逻辑抽出来：`awrite_batch` 的二次降级路径（主目录与降级目录都写
        失败时逐条补写）已经在锁内，若那里直接调用 ``awrite``，就会重入同一把
        **不可重入的 asyncio.Lock** —— 永久挂起并一直占着锁，此后所有日志写入都会
        卡死。这是实测触发到的真实死锁（主目录只读 + home 也不可写时必然发生）。
        锁只在一个地方获取，这里只负责写。
        """
        file_path = self._get_log_file_path(record.category)

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

    async def awrite_batch(self, records: list[LogRecord]) -> None:
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
                        # 走 _write_record_unlocked 而不是 awrite：此处已在锁内，
                        # 调 awrite 会重入同一把不可重入锁并永久挂起。
                        for record in category_records:
                            await self._write_record_unlocked(record)

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
                except OSError as exc:
                    # A.60：清理失败原先静默（残留旧日志会占盘；但清理是尽力而为，不阻断主流程）
                    _logger.warning(
                        "log_cleanup_file_failed",
                        path=str(file_path),
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
            if deleted_count > 0:
                _logger.info("Cleaned up old log files",
                             count=deleted_count,
                             cutoff=cutoff.isoformat())
        except Exception as e:
            _logger.error("Error cleaning up old files", error=str(e))

    async def close(self) -> None:
        pass
