import asyncio
import statistics
import threading
import time
from collections import deque
from typing import Any, Dict, List, Optional

import structlog

from .backend import LogStorageBackend
from .file import FileLogStorage
from .record import LogRecord
from app.observability.utils import calculate_percentile

_logger = structlog.get_logger("app.observability.logging")


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
                _logger.debug("Batch flush completed",
                              count=len(batch),
                              duration_ms=flush_time * 1000)
            except Exception as e:
                _logger.error("Batch flush failed, attempting single writes", error=str(e))
                single_success = 0
                for record in batch:
                    try:
                        await self._backend.awrite(record)
                        single_success += 1
                    except Exception:
                        pass
                if single_success < len(batch):
                    failed_count = len(batch) - single_success
                    _logger.error("Single writes also failed, degrading to file",
                                  failed_count=failed_count)
                    await self._degrade_to_file(batch[single_success:])
                self._metrics["total_flushed"] += single_success

    async def _degrade_to_file(self, records: List[LogRecord]) -> None:
        try:
            fallback = FileLogStorage(
                log_dir="logs/fallback",
                max_file_size=50 * 1024 * 1024,
                retention_days=30,
            )
            for record in records:
                try:
                    await fallback.awrite(record)
                except Exception:
                    pass
            await fallback.close()
            self._metrics["degraded_writes"] = self._metrics.get("degraded_writes", 0) + len(records)
            _logger.warning("Degraded log writes to file",
                            count=len(records),
                            path="logs/fallback")
        except Exception as fallback_err:
            _logger.critical("All log write paths failed",
                             error=str(fallback_err),
                             lost_records=len(records))

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
                _logger.warning("Log queue full, dropped oldest record",
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
            result["flush_latency_p99"] = calculate_percentile(self._metrics["flush_latencies"], 99.0) * 1000
            result["flush_latency_avg"] = statistics.mean(self._metrics["flush_latencies"]) * 1000
        else:
            result["flush_latency_p99"] = 0
            result["flush_latency_avg"] = 0

        if self._metrics["queue_sizes"]:
            result["max_queue_size"] = max(self._metrics["queue_sizes"])
        else:
            result["max_queue_size"] = 0

        return result
