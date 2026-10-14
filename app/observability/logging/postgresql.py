import asyncio
import hashlib
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from app.config.settings import settings

from .backend import LogStorageBackend
from .record import LogRecord

_logger = structlog.get_logger("app.observability.logging")


class PostgreSQLLogStorage(LogStorageBackend):
    def __init__(
        self,
        database_url: str,
        max_pool_size: int = None,
        min_pool_size: int = None,
    ):
        self.database_url = database_url
        self.max_pool_size = max_pool_size or settings.log_db_pool_max_size
        self.min_pool_size = min_pool_size or settings.log_db_pool_min_size
        self._pool: Any | None = None
        self._lock = threading.Lock()
        self._async_lock: asyncio.Lock | None = None

    async def _get_pool(self):
        if self._pool is None:
            async with self._get_async_lock():
                if self._pool is None:
                    import asyncpg
                    self._pool = await asyncpg.create_pool(
                        self.database_url,
                        min_size=self.min_pool_size,
                        max_size=self.max_pool_size,
                    )
        return self._pool

    async def _ensure_pool(self) -> None:
        await self._get_pool()

    async def awrite(self, record: LogRecord) -> None:
        try:
            pool = await self._get_pool()
            log_id = str(uuid.uuid4())
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_logs (log_id, session_id, action, actor, target_type, target_id, detail, log_type, hash_self, created_at)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                    """,
                    log_id,
                    record.kwargs.get("session_id"),
                    record.message,
                    record.kwargs.get("operator", "system"),
                    record.kwargs.get("target_type"),
                    record.kwargs.get("target_id"),
                    json.dumps(record.to_dict()),
                    "operation",
                    hashlib.sha256(f"{log_id}:{record.message}".encode()).hexdigest(),
                    datetime.now(timezone.utc),
                )
        except Exception as e:
            _logger.error("Failed to write log to PostgreSQL",
                          error=str(e),
                          category=record.category,
                          message=record.message)
            self._report_degraded_write()

    async def awrite_batch(self, records: list[LogRecord]) -> None:
        if not records:
            return
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for record in records:
                        log_id = str(uuid.uuid4())
                        await conn.execute(
                            """
                            INSERT INTO audit_logs (log_id, session_id, action, actor, target_type, target_id, detail, log_type, hash_self, created_at)
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            """,
                            log_id,
                            record.kwargs.get("session_id"),
                            record.message,
                            record.kwargs.get("operator", "system"),
                            record.kwargs.get("target_type"),
                            record.kwargs.get("target_id"),
                            json.dumps(record.to_dict()),
                            "operation",
                            hashlib.sha256(f"{log_id}:{record.message}".encode()).hexdigest(),
                            datetime.now(timezone.utc),
                        )
            _logger.debug("Batch write to PostgreSQL completed", count=len(records))
        except Exception as e:
            _logger.error("Failed to write batch logs to PostgreSQL",
                          error=str(e),
                          count=len(records))
            await asyncio.sleep(1)
            success_count = 0
            for record in records:
                try:
                    await self.awrite(record)
                    success_count += 1
                except Exception:  # silent-ok: 单条失败由 success_count 计数 + 随后的 error 日志兜底（在此写日志会递归入队）
                    pass
            if success_count < len(records):
                _logger.error(
                    "Batch write partial failure",
                    total=len(records),
                    succeeded=success_count,
                    failed=len(records) - success_count,
                )
                self._report_degraded_write()

    @staticmethod
    def _report_degraded_write() -> None:
        try:
            from app.observability.metrics_collector import get_metrics_collector
            get_metrics_collector().inc_audit_log_degraded_writes()
        except Exception:  # silent-ok: 降级统计的上报失败，本身属「计数失败」，再写日志会递归入队
            pass

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
