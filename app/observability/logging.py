"""
Structured logging framework with LogStorage abstraction layer.

This module provides:
- LogStorage abstraction supporting multiple backends (PostgreSQL, File, Elasticsearch)
- Log classification (diagnostic/operational/audit) per deployment profile
- NTP sync status marking for IEC 62304 compliance
- Thread-safe singleton pattern
- Batch async write queue for P99 < 1ms performance target

Architecture:
    app code → structlog processors → LogStorage singleton → BatchLogStorageWrapper → backend
"""

import asyncio
import json
import os
import statistics
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from datetime import datetime, timezone, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import structlog

from app.config.settings import settings
from app.observability.enums import (
    DeploymentProfile,
    LogCategory,
    NtpSyncStatus,
)


def _json_serializer(obj: Any) -> str:
    if isinstance(obj, Enum):
        return obj.value
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        structlog.processors.JSONRenderer(serializer=_json_serializer)
        if settings.env == "production"
        else structlog.dev.ConsoleRenderer(),
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)


# Module internal logger for logging framework errors
_internal_logger = structlog.get_logger("app.observability.logging")


class LogRecord:
    def __init__(
        self,
        category: LogCategory,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        ntp_sync_status: Optional[NtpSyncStatus] = None,
        **kwargs,
    ):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.category = category.value
        self.message = message
        self.extra = extra or {}
        self.ntp_sync_status = ntp_sync_status.value if ntp_sync_status else None
        self.kwargs = kwargs
        self._enqueue_time = time.time()

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "timestamp": self.timestamp,
            "category": self.category,
            "message": self.message,
            "ntp_sync_status": self.ntp_sync_status,
        }
        for k, v in self.extra.items():
            result[f"extra_{k}"] = v
        for k, v in self.kwargs.items():
            result[f"param_{k}"] = v
        return result


class LogStorageBackend(ABC):
    @abstractmethod
    def write(self, record: LogRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    async def awrite(self, record: LogRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    async def awrite_batch(self, records: List[LogRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError


class PostgreSQLLogStorage(LogStorageBackend):
    def __init__(
        self,
        database_url: str,
        max_pool_size: int = 10,
        min_pool_size: int = 2,
    ):
        self.database_url = database_url
        self.max_pool_size = max_pool_size
        self.min_pool_size = min_pool_size
        self._pool: Optional[Any] = None
        self._lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None

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

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    async def _ensure_pool(self) -> None:
        await self._get_pool()

    def write(self, record: LogRecord) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.awrite(record))
            else:
                loop.run_until_complete(self.awrite(record))
        except RuntimeError:
            asyncio.run(self.awrite(record))

    async def awrite(self, record: LogRecord) -> None:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO audit_logs (session_id, operation, operator, details, created_at)
                    VALUES ($1, $2, $3, $4, $5)
                    """,
                    record.kwargs.get("session_id"),
                    record.message,
                    record.kwargs.get("operator"),
                    json.dumps(record.to_dict()),
                    datetime.now(timezone.utc),
                )
        except Exception as e:
            _internal_logger.error("Failed to write log to PostgreSQL",
                                  error=str(e),
                                  category=record.category,
                                  message=record.message)

    async def awrite_batch(self, records: List[LogRecord]) -> None:
        if not records:
            return
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for record in records:
                        await conn.execute(
                            """
                            INSERT INTO audit_logs (session_id, operation, operator, details, created_at)
                            VALUES ($1, $2, $3, $4, $5)
                            """,
                            record.kwargs.get("session_id"),
                            record.message,
                            record.kwargs.get("operator"),
                            json.dumps(record.to_dict()),
                            datetime.now(timezone.utc),
                        )
            _internal_logger.debug("Batch write to PostgreSQL completed", count=len(records))
        except Exception as e:
            _internal_logger.error("Failed to write batch logs to PostgreSQL",
                                  error=str(e),
                                  count=len(records))
            for record in records:
                await self.awrite(record)

    async def close(self) -> None:
        if self._pool:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None


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

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

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

    def write(self, record: LogRecord) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.awrite(record))
            else:
                loop.run_until_complete(self.awrite(record))
        except RuntimeError:
            asyncio.run(self.awrite(record))

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
                _internal_logger.warning("Primary log directory failed, using fallback",
                                       error=str(e),
                                       primary_dir=str(self.log_dir))
                fallback_dir = Path.home() / ".ultrasound" / "logs"
                fallback_dir.mkdir(parents=True, exist_ok=True)
                fallback_path = fallback_dir / file_path.name
                try:
                    async with aiofiles.open(fallback_path, mode="a") as f:
                        await f.write(json.dumps(record.to_dict(), default=str) + "\n")
                except Exception as fallback_e:
                    _internal_logger.error("Failed to write to fallback log directory",
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

                    _internal_logger.debug("Batch write to file completed",
                                          category=category.value,
                                          count=len(category_records))
                except OSError as e:
                    _internal_logger.warning("Primary log directory failed for batch, using fallback",
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
                        _internal_logger.error("Failed to write batch to fallback log directory",
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
                _internal_logger.info("Cleaned up old log files",
                                     count=deleted_count,
                                     cutoff=cutoff.isoformat())
        except Exception as e:
            _internal_logger.error("Error cleaning up old files", error=str(e))

    async def close(self) -> None:
        pass


class ElasticsearchLogStorage(LogStorageBackend):
    def __init__(
        self,
        es_url: Optional[str] = None,
        index_prefix: str = "ultrasound-logs",
    ):
        self.es_url = es_url or os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
        self.index_prefix = index_prefix
        self._client: Optional[Any] = None
        self._async_lock: Optional[asyncio.Lock] = None

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    async def _get_client(self):
        if self._client is None:
            try:
                from elasticsearch import AsyncElasticsearch
                self._client = AsyncElasticsearch([self.es_url])
            except ImportError:
                self._client = None
        return self._client

    def write(self, record: LogRecord) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(self.awrite(record))
            else:
                loop.run_until_complete(self.awrite(record))
        except RuntimeError:
            asyncio.run(self.awrite(record))

    async def awrite(self, record: LogRecord) -> None:
        client = await self._get_client()
        if client is None:
            _internal_logger.debug("Elasticsearch client not available (import error)")
            return

        index_name = f"{self.index_prefix}-{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"

        try:
            await client.index(index=index_name, document=record.to_dict())
        except Exception as e:
            _internal_logger.error("Failed to write log to Elasticsearch",
                                  error=str(e),
                                  es_url=self.es_url,
                                  index=index_name)

    async def awrite_batch(self, records: List[LogRecord]) -> None:
        if not records:
            return

        client = await self._get_client()
        if client is None:
            _internal_logger.debug("Elasticsearch client not available (import error)")
            for record in records:
                await self.awrite(record)
            return

        index_name = f"{self.index_prefix}-{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"

        try:
            from elasticsearch.helpers import async_bulk

            actions = [
                {
                    "_index": index_name,
                    "_source": record.to_dict(),
                }
                for record in records
            ]

            await async_bulk(client, actions)
            _internal_logger.debug("Batch write to Elasticsearch completed", count=len(records))
        except Exception as e:
            _internal_logger.error("Failed to write batch logs to Elasticsearch",
                                  error=str(e),
                                  count=len(records))
            for record in records:
                await self.awrite(record)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None


class BatchLogStorageWrapper(LogStorageBackend):
    DEFAULT_BATCH_SIZE = 50
    DEFAULT_FLUSH_INTERVAL = 1.0
    DEFAULT_MAX_QUEUE_SIZE = 10000

    def __init__(
        self,
        backend: LogStorageBackend,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL,
        max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE,
    ):
        self._backend = backend
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._max_queue_size = max_queue_size
        self._queue: deque = deque()
        self._flush_task: Optional[asyncio.Task] = None
        self._lock = threading.Lock()
        self._async_lock: Optional[asyncio.Lock] = None
        self._shutdown_event = asyncio.Event()

        self._metrics = {
            "total_enqueued": 0,
            "total_flushed": 0,
            "batch_flushes": 0,
            "flush_latencies": [],
            "queue_sizes": [],
            "dropped_records": 0,
        }

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    def _start_flush_task(self):
        if self._flush_task is None or self._flush_task.done():
            self._flush_task = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self):
        while not self._shutdown_event.is_set():
            await asyncio.sleep(self._flush_interval)
            await self._flush_batch()

    async def _flush_batch(self):
        batch = []
        with self._lock:
            while self._queue and len(batch) < self._batch_size:
                batch.append(self._queue.popleft())

        if batch:
            start_time = time.time()
            try:
                await self._backend.awrite_batch(batch)
                self._metrics["total_flushed"] += len(batch)
                self._metrics["batch_flushes"] += 1
                flush_time = time.time() - start_time
                self._metrics["flush_latencies"].append(flush_time)
                _internal_logger.debug("Batch flush completed",
                                      count=len(batch),
                                      duration_ms=flush_time * 1000)
            except Exception as e:
                _internal_logger.error("Batch flush failed", error=str(e))
                for record in batch:
                    await self._backend.awrite(record)
                self._metrics["total_flushed"] += len(batch)

    def write(self, record: LogRecord) -> None:
        self._enqueue(record)
        try:
            asyncio.get_running_loop()
            self._start_flush_task()
        except RuntimeError:
            pass

    async def awrite(self, record: LogRecord) -> None:
        self._enqueue(record)
        self._start_flush_task()

    def _enqueue(self, record: LogRecord) -> None:
        with self._lock:
            if len(self._queue) >= self._max_queue_size:
                self._queue.popleft()
                self._metrics["dropped_records"] += 1
                _internal_logger.warning("Log queue full, dropped oldest record",
                                        queue_size=len(self._queue))

            self._queue.append(record)
            self._metrics["total_enqueued"] += 1
            self._metrics["queue_sizes"].append(len(self._queue))

            if len(self._queue) >= self._batch_size:
                try:
                    asyncio.get_running_loop()
                    asyncio.create_task(self._flush_batch())
                except RuntimeError:
                    pass

    async def awrite_batch(self, records: List[LogRecord]) -> None:
        for record in records:
            self._enqueue(record)
        self._start_flush_task()

    async def close(self) -> None:
        self._shutdown_event.set()
        if self._flush_task:
            await self._flush_task

        await self._flush_batch()
        await self._backend.close()

    def get_metrics(self) -> Dict[str, Any]:
        result = self._metrics.copy()
        if self._metrics["flush_latencies"]:
            result["flush_latency_p99"] = self._calculate_percentile(self._metrics["flush_latencies"], 99) * 1000
            result["flush_latency_avg"] = statistics.mean(self._metrics["flush_latencies"]) * 1000
        else:
            result["flush_latency_p99"] = 0
            result["flush_latency_avg"] = 0

        if self._metrics["queue_sizes"]:
            result["max_queue_size"] = max(self._metrics["queue_sizes"])
        else:
            result["max_queue_size"] = 0

        return result

    @staticmethod
    def _calculate_percentile(data: List[float], percentile: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(percentile / 100 * len(sorted_data))
        if index >= len(sorted_data):
            index = len(sorted_data) - 1
        return sorted_data[index]


class LogStorage:
    _instance: Optional["LogStorage"] = None
    _lock = threading.Lock()
    _backend: Optional[LogStorageBackend] = None
    _profile: DeploymentProfile = DeploymentProfile.COMPACT

    def __init__(
        self,
        profile: DeploymentProfile = DeploymentProfile.COMPACT,
        database_url: Optional[str] = None,
        log_dir: Optional[str] = None,
        es_url: Optional[str] = None,
        enable_batching: bool = True,
        batch_size: int = BatchLogStorageWrapper.DEFAULT_BATCH_SIZE,
        flush_interval: float = BatchLogStorageWrapper.DEFAULT_FLUSH_INTERVAL,
    ):
        if not isinstance(profile, DeploymentProfile):
            raise ValueError(f"Invalid profile type: {type(profile).__name__}. Must be DeploymentProfile enum.")
        
        self._profile = profile
        self._log_dir = log_dir
        self._es_url = es_url
        self._enable_batching = enable_batching

        raw_backend: LogStorageBackend
        if profile == DeploymentProfile.COMPACT:
            raw_backend = PostgreSQLLogStorage(
                database_url=database_url or settings.database_url,
            )
        elif profile == DeploymentProfile.FULL:
            raw_backend = FileLogStorage(log_dir=log_dir)
        else:
            raw_backend = PostgreSQLLogStorage(
                database_url=database_url or settings.database_url,
            )

        if enable_batching:
            self._backend = BatchLogStorageWrapper(
                backend=raw_backend,
                batch_size=batch_size,
                flush_interval=flush_interval,
            )
        else:
            self._backend = raw_backend

    @classmethod
    def get_instance(
        cls,
        profile: Optional[DeploymentProfile] = None,
        database_url: Optional[str] = None,
        log_dir: Optional[str] = None,
        es_url: Optional[str] = None,
        enable_batching: bool = True,
        batch_size: int = BatchLogStorageWrapper.DEFAULT_BATCH_SIZE,
        flush_interval: float = BatchLogStorageWrapper.DEFAULT_FLUSH_INTERVAL,
    ) -> "LogStorage":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    profile = profile or cls._detect_profile()
                    cls._instance = cls(
                        profile=profile,
                        database_url=database_url,
                        log_dir=log_dir,
                        es_url=es_url,
                        enable_batching=enable_batching,
                        batch_size=batch_size,
                        flush_interval=flush_interval,
                    )
        return cls._instance

    @classmethod
    def _detect_profile(cls) -> DeploymentProfile:
        env = getattr(settings, "deployment_profile", None)
        if env:
            try:
                return DeploymentProfile(env)
            except ValueError:
                pass
        return DeploymentProfile.COMPACT

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            if cls._instance and cls._instance._backend:
                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(cls._instance._backend.close())
                except RuntimeError:
                    asyncio.run(cls._instance._backend.close())
            cls._instance = None

    def get_profile(self) -> DeploymentProfile:
        return self._profile

    def get_metrics(self) -> Dict[str, Any]:
        if isinstance(self._backend, BatchLogStorageWrapper):
            return self._backend.get_metrics()
        return {}

    def write(
        self,
        category: LogCategory,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        ntp_sync_status: Optional[NtpSyncStatus] = None,
        **kwargs,
    ) -> None:
        if self._backend:
            record = LogRecord(
                category=category,
                message=message,
                extra=extra,
                ntp_sync_status=ntp_sync_status,
                **kwargs,
            )
            self._backend.write(record)

    async def awrite(
        self,
        category: LogCategory,
        message: str,
        extra: Optional[Dict[str, Any]] = None,
        ntp_sync_status: Optional[NtpSyncStatus] = None,
        **kwargs,
    ) -> None:
        if self._backend:
            record = LogRecord(
                category=category,
                message=message,
                extra=extra,
                ntp_sync_status=ntp_sync_status,
                **kwargs,
            )
            await self._backend.awrite(record)


def get_log_storage() -> LogStorage:
    return LogStorage.get_instance()


def add_ntp_context(
    logger: structlog.stdlib.BoundLogger,
    ntp_sync_status: NtpSyncStatus,
) -> structlog.stdlib.BoundLogger:
    return logger.bind(ntp_sync_status=ntp_sync_status.value)


def classify_and_bind_logger(
    category: LogCategory,
    logger_name: Optional[str] = None,
    ntp_sync_status: Optional[NtpSyncStatus] = None,
) -> structlog.stdlib.BoundLogger:
    logger = structlog.get_logger(name=logger_name)

    logger = logger.bind(
        log_category=category.value,
        deployment_profile=get_log_storage().get_profile().value,
    )

    if ntp_sync_status:
        logger = logger.bind(ntp_sync_status=ntp_sync_status.value)

    return logger


def create_diagnostic_logger(
    logger_name: Optional[str] = None,
    ntp_sync_status: Optional[NtpSyncStatus] = None,
) -> structlog.stdlib.BoundLogger:
    return classify_and_bind_logger(
        category=LogCategory.DIAGNOSTIC,
        logger_name=logger_name,
        ntp_sync_status=ntp_sync_status,
    )


def create_operational_logger(
    logger_name: Optional[str] = None,
    ntp_sync_status: Optional[NtpSyncStatus] = None,
) -> structlog.stdlib.BoundLogger:
    return classify_and_bind_logger(
        category=LogCategory.OPERATIONAL,
        logger_name=logger_name,
        ntp_sync_status=ntp_sync_status,
    )


def create_audit_logger(
    logger_name: Optional[str] = None,
    ntp_sync_status: Optional[NtpSyncStatus] = None,
) -> structlog.stdlib.BoundLogger:
    return classify_and_bind_logger(
        category=LogCategory.AUDIT,
        logger_name=logger_name,
        ntp_sync_status=ntp_sync_status,
    )
