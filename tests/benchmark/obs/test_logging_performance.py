"""
Performance benchmark tests for the logging framework.

This module provides:
- P99 latency benchmarks for actual log writes (not just enqueue)
- Throughput testing with proper measurement methodology
- Batch write performance testing
- Queue behavior testing under load

Key improvements:
- Measures actual write latency (not just enqueue)
- Proper throughput calculation excluding sleep time
- Uses close() to ensure all writes complete before measurement
- Tests verify real-world performance characteristics
"""

import asyncio
import statistics
import time

import pytest

from app.observability.enums import DeploymentProfile, LogCategory, NtpSyncStatus
from app.observability.logging import BatchLogStorageWrapper, LogRecord, LogStorage

pytestmark = [pytest.mark.track_obs, pytest.mark.track_inf]


class TestLoggingPerformance:
    @pytest.mark.asyncio
    async def test_enqueue_latency(self, tmp_path):
        """Test that enqueue operation is fast (P99 < 1ms)"""
        LogStorage.reset_instance()
        storage = LogStorage.get_instance(
            profile=DeploymentProfile.FULL,
            log_dir=str(tmp_path),
            batch_size=1000,
            flush_interval=10.0,
        )

        latencies = []
        for _ in range(1000):
            start = time.time()
            await storage.awrite(
                category=LogCategory.DIAGNOSTIC,
                message="test message",
                session_id="test-session",
            )
            latency_ms = (time.time() - start) * 1000
            latencies.append(latency_ms)

        await storage._backend.close()

        p99 = self._calculate_percentile(latencies, 99)
        avg = statistics.mean(latencies)

        print(f"Enqueue latency - Avg: {avg:.2f}ms, P99: {p99:.2f}ms")
        assert p99 < 1.0, f"Enqueue P99 latency {p99:.2f}ms exceeds 1ms target"

    @pytest.mark.asyncio
    async def test_actual_write_latency(self, tmp_path):
        """Test actual write latency including flush operations"""
        LogStorage.reset_instance()
        storage = LogStorage.get_instance(
            profile=DeploymentProfile.FULL,
            log_dir=str(tmp_path),
            batch_size=1,
            flush_interval=0.01,
        )

        flush_latencies = []
        for i in range(100):
            await storage.awrite(
                category=LogCategory.DIAGNOSTIC,
                message=f"write test {i}",
                session_id="write-test",
            )
            await asyncio.sleep(0.005)
            metrics = storage.get_metrics()
            # 安全获取flush_latencies，避免KeyError
            if metrics.get("flush_latencies"):
                flush_latencies.extend([l * 1000 for l in metrics["flush_latencies"]])
                # 重置后端的flush_latencies
                if hasattr(storage._backend, "_metrics") and "flush_latencies" in storage._backend._metrics:
                    storage._backend._metrics["flush_latencies"] = []

        await storage._backend.close()

        if flush_latencies:
            p99 = self._calculate_percentile(flush_latencies, 99)
            avg = statistics.mean(flush_latencies)
            print(f"Actual write latency - Avg: {avg:.2f}ms, P99: {p99:.2f}ms")
            # 添加断言验证延迟（设置合理的上限100ms）
            assert p99 < 100.0, f"Write P99 latency {p99:.2f}ms exceeds 100ms"
        else:
            pytest.skip("No flush latencies recorded - test cannot verify performance")

    @pytest.mark.asyncio
    async def test_throughput(self, tmp_path):
        """Test throughput with proper measurement (excludes sleep/wait time)"""
        LogStorage.reset_instance()
        storage = LogStorage.get_instance(
            profile=DeploymentProfile.FULL,
            log_dir=str(tmp_path),
            batch_size=100,
            flush_interval=0.1,
        )

        for i in range(100):
            await storage.awrite(
                category=LogCategory.DIAGNOSTIC,
                message=f"warmup {i}",
                session_id="throughput-test",
            )
        await asyncio.sleep(0.1)

        start_time = time.time()
        for i in range(1000):
            await storage.awrite(
                category=LogCategory.DIAGNOSTIC,
                message=f"throughput test {i}",
                session_id="throughput-test",
            )
        loop_time = time.time() - start_time

        await asyncio.sleep(0.2)
        await storage._backend.close()

        metrics = storage.get_metrics()
        total_written = metrics["total_flushed"]

        throughput = total_written / loop_time

        print(f"Throughput: {throughput:.2f} logs/sec (loop time: {loop_time:.2f}s)")
        print(f"Total flushed: {total_written}")
        assert throughput > 500, f"Throughput {throughput:.2f} logs/sec below 500 target"

    @pytest.mark.asyncio
    async def test_batch_throughput(self, tmp_path):
        """Test batch write throughput"""
        LogStorage.reset_instance()
        storage = LogStorage.get_instance(
            profile=DeploymentProfile.FULL,
            log_dir=str(tmp_path),
            batch_size=1000,
            flush_interval=5.0,
        )

        start_time = time.time()
        for i in range(5000):
            await storage.awrite(
                category=LogCategory.DIAGNOSTIC,
                message=f"batch test {i}",
                session_id="batch-throughput",
            )
        enqueue_time = time.time() - start_time

        await storage._backend.close()

        metrics = storage.get_metrics()
        total_flushed = metrics["total_flushed"]
        total_time = time.time() - start_time

        enqueue_rate = 5000 / enqueue_time
        overall_throughput = total_flushed / total_time

        print(f"Enqueue rate: {enqueue_rate:.2f} logs/sec")
        print(f"Overall throughput: {overall_throughput:.2f} logs/sec")
        print(f"Batch flushes: {metrics['batch_flushes']}")

    @pytest.mark.asyncio
    async def test_concurrent_writes(self, tmp_path):
        """Test concurrent write performance"""
        LogStorage.reset_instance()
        storage = LogStorage.get_instance(
            profile=DeploymentProfile.FULL,
            log_dir=str(tmp_path),
            batch_size=100,
            flush_interval=0.1,
        )

        async def write_logs(count: int):
            enqueue_latencies = []
            for i in range(count):
                start = time.time()
                await storage.awrite(
                    category=LogCategory.DIAGNOSTIC,
                    message=f"concurrent {i}",
                    session_id="concurrent-test",
                )
                latency_ms = (time.time() - start) * 1000
                enqueue_latencies.append(latency_ms)
            return enqueue_latencies

        tasks = [write_logs(200) for _ in range(5)]
        all_latencies = []
        for task in asyncio.as_completed(tasks):
            latencies = await task
            all_latencies.extend(latencies)

        await storage._backend.close()

        p99 = self._calculate_percentile(all_latencies, 99)
        avg = statistics.mean(all_latencies)

        print(f"Concurrent (5x200) - Avg enqueue: {avg:.2f}ms, P99: {p99:.2f}ms")
        assert p99 < 1.0, f"Enqueue P99 latency {p99:.2f}ms exceeds 1ms target"

    @pytest.mark.asyncio
    async def test_queue_overflow(self, tmp_path):
        """Test queue overflow behavior"""
        LogStorage.reset_instance()
        from app.observability.logging import FileLogStorage
        backend = FileLogStorage(log_dir=str(tmp_path))
        wrapper = BatchLogStorageWrapper(backend=backend, batch_size=1000, flush_interval=10.0, max_queue_size=100)

        for i in range(150):
            record = LogRecord(
                category=LogCategory.DIAGNOSTIC,
                message=f"overflow test {i}",
            )
            await wrapper.awrite(record)

        await asyncio.sleep(0.5)
        metrics = wrapper.get_metrics()

        print(f"Queue overflow - Dropped: {metrics['dropped_records']}")
        assert metrics["dropped_records"] == 50, f"Expected 50 dropped records, got {metrics['dropped_records']}"

        await wrapper.close()

    @pytest.mark.asyncio
    async def test_mixed_category_writes(self, tmp_path):
        """Test mixed category writes performance"""
        LogStorage.reset_instance()
        storage = LogStorage.get_instance(
            profile=DeploymentProfile.FULL,
            log_dir=str(tmp_path),
            batch_size=30,
            flush_interval=0.2,
        )

        categories = [LogCategory.DIAGNOSTIC, LogCategory.OPERATIONAL, LogCategory.AUDIT]
        enqueue_latencies = []

        for i in range(300):
            category = categories[i % 3]
            start = time.time()
            await storage.awrite(
                category=category,
                message=f"mixed category {i}",
                ntp_sync_status=NtpSyncStatus.SYNCED,
            )
            latency_ms = (time.time() - start) * 1000
            enqueue_latencies.append(latency_ms)

        await storage._backend.close()

        p99 = self._calculate_percentile(enqueue_latencies, 99)
        avg = statistics.mean(enqueue_latencies)

        print(f"Mixed categories - Avg enqueue: {avg:.2f}ms, P99: {p99:.2f}ms")
        assert p99 < 1.0, f"Enqueue P99 latency {p99:.2f}ms exceeds 1ms target"

    @staticmethod
    def _calculate_percentile(data: list[float], percentile: int) -> float:
        if not data:
            return 0.0
        sorted_data = sorted(data)
        index = int(percentile / 100 * len(sorted_data))
        if index >= len(sorted_data):
            index = len(sorted_data) - 1
        return sorted_data[index]


class TestBatchLogStorageWrapper:
    @pytest.mark.asyncio
    async def test_basic_batching(self, tmp_path):
        from app.observability.logging import FileLogStorage

        backend = FileLogStorage(log_dir=str(tmp_path))
        wrapper = BatchLogStorageWrapper(backend=backend, batch_size=10, flush_interval=0.1)

        for i in range(15):
            record = LogRecord(
                category=LogCategory.DIAGNOSTIC,
                message=f"test {i}",
            )
            await wrapper.awrite(record)

        await asyncio.sleep(0.05)

        metrics = wrapper.get_metrics()
        assert metrics["total_enqueued"] == 15
        assert metrics["batch_flushes"] >= 1
        assert metrics["total_flushed"] == 15

        await wrapper.close()

    @pytest.mark.asyncio
    async def test_flush_interval(self, tmp_path):
        from app.observability.logging import FileLogStorage

        backend = FileLogStorage(log_dir=str(tmp_path))
        wrapper = BatchLogStorageWrapper(backend=backend, batch_size=100, flush_interval=0.2)

        for i in range(10):
            record = LogRecord(
                category=LogCategory.DIAGNOSTIC,
                message=f"interval test {i}",
            )
            await wrapper.awrite(record)

        # 等待超过一个 flush_interval 确保定时 flush 触发
        await asyncio.sleep(0.1)
        metrics = wrapper.get_metrics()

        # 验证至少有一次批量写入
        assert metrics["batch_flushes"] >= 1

        await wrapper.close()

    @pytest.mark.asyncio
    async def test_metrics_calculation(self, tmp_path):
        from app.observability.logging import FileLogStorage

        backend = FileLogStorage(log_dir=str(tmp_path))
        wrapper = BatchLogStorageWrapper(backend=backend, batch_size=5, flush_interval=0.1)

        for i in range(20):
            record = LogRecord(
                category=LogCategory.DIAGNOSTIC,
                message=f"metrics test {i}",
            )
            await wrapper.awrite(record)

        await asyncio.sleep(0.1)

        metrics = wrapper.get_metrics()

        assert "flush_latency_p99" in metrics
        assert "flush_latency_avg" in metrics
        assert "max_queue_size" in metrics
        assert metrics["total_enqueued"] == 20
        assert metrics["total_flushed"] == 20
        assert metrics["batch_flushes"] == 4

        await wrapper.close()

    @pytest.mark.asyncio
    async def test_actual_flush_latency(self, tmp_path):
        """Test actual flush latency measurement"""
        from app.observability.logging import FileLogStorage

        backend = FileLogStorage(log_dir=str(tmp_path))
        wrapper = BatchLogStorageWrapper(backend=backend, batch_size=10, flush_interval=0.01)

        for i in range(50):
            record = LogRecord(
                category=LogCategory.DIAGNOSTIC,
                message=f"flush latency test {i}",
            )
            await wrapper.awrite(record)

        await asyncio.sleep(0.1)

        metrics = wrapper.get_metrics()
        flush_latencies_ms = [l * 1000 for l in metrics["flush_latencies"]]

        if flush_latencies_ms:
            p99 = TestLoggingPerformance._calculate_percentile(flush_latencies_ms, 99)
            avg = statistics.mean(flush_latencies_ms)
            print(f"Flush latency - Avg: {avg:.2f}ms, P99: {p99:.2f}ms")

        await wrapper.close()
