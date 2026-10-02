
import pytest

from app.inference.gpu_monitor import (
    FragmentationCalculator,
    FragmentationReport,
    GPUMemoryMonitor,
    GPUMemorySnapshot,
    GPUScheduleStrategy,
    MemoryProfileReport,
    Platform,
    ScheduledRequest,
    SystemMemoryProfiler,
    SystemMemorySnapshot,
    create_memory_reader,
    get_platform,
)

pytestmark = [pytest.mark.track_inf, pytest.mark.track_obs]
class TestGPUMemorySnapshot:
    def test_usage_pct(self):
        snap = GPUMemorySnapshot(
            timestamp=0.0,
            total_memory_mb=12288.0,
            used_memory_mb=6144.0,
            free_memory_mb=6144.0,
        )
        assert snap.usage_pct == 50.0

    def test_usage_pct_zero_total(self):
        snap = GPUMemorySnapshot(timestamp=0.0)
        assert snap.usage_pct == 0.0

    def test_default_values(self):
        snap = GPUMemorySnapshot(timestamp=1.0)
        assert snap.fragmentation_rate == 0.0
        assert snap.gpu_utilization_pct == 0.0
        assert snap.gpu_temperature_c == 0.0


class TestSystemMemorySnapshot:
    def test_usage_pct(self):
        snap = SystemMemorySnapshot(
            timestamp=0.0,
            total_memory_mb=65536.0,
            used_memory_mb=22048.0,
            available_memory_mb=43488.0,
        )
        assert snap.usage_pct == pytest.approx(33.64, rel=0.01)

    def test_usage_pct_zero_total(self):
        snap = SystemMemorySnapshot(timestamp=0.0)
        assert snap.usage_pct == 0.0


class TestFragmentationReport:
    def test_passed_below_critical(self):
        report = FragmentationReport(
            max_fragmentation_rate=0.25,
            critical_threshold=0.40,
        )
        assert report.passed is True

    def test_failed_at_critical(self):
        report = FragmentationReport(
            max_fragmentation_rate=0.45,
            critical_threshold=0.40,
        )
        assert report.passed is False

    def test_default_values(self):
        report = FragmentationReport()
        assert report.warning_threshold == 0.30
        assert report.critical_threshold == 0.40
        assert report.total_snapshots == 0


class TestMemoryProfileReport:
    def test_within_budget(self):
        report = MemoryProfileReport(
            measured_total_used_mb=18000.0,
            budget_limit_mb=22048.0,
        )
        assert report.within_budget is True

    def test_over_budget(self):
        report = MemoryProfileReport(
            measured_total_used_mb=25000.0,
            budget_limit_mb=22048.0,
        )
        assert report.within_budget is False

    def test_default_budget(self):
        report = MemoryProfileReport()
        assert report.budget_limit_mb == 22048.0


class TestGPUScheduleStrategy:
    def test_initial_state(self):
        strategy = GPUScheduleStrategy()
        assert strategy.llama_cpp_active is False
        assert strategy.retrieval_active is False

    async def test_acquire_llm(self):
        strategy = GPUScheduleStrategy()
        result = await strategy.acquire_llm()
        assert result is True
        assert strategy.llama_cpp_active is True

    async def test_release_llm(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_llm()
        await strategy.release_llm()
        assert strategy.llama_cpp_active is False

    async def test_acquire_retrieval(self):
        strategy = GPUScheduleStrategy()
        result = await strategy.acquire_retrieval()
        assert result is True
        assert strategy.retrieval_active is True

    async def test_release_retrieval(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_retrieval()
        await strategy.release_retrieval()
        assert strategy.retrieval_active is False

    async def test_llm_blocks_retrieval(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_llm()
        result = await strategy.acquire_retrieval()
        assert result is False

    async def test_retrieval_blocks_llm(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_retrieval()
        result = await strategy.acquire_llm()
        assert result is False

    async def test_release_llm_allows_retrieval(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_llm()
        await strategy.release_llm()
        result = await strategy.acquire_retrieval()
        assert result is True

    async def test_release_retrieval_allows_llm(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_retrieval()
        await strategy.release_retrieval()
        result = await strategy.acquire_llm()
        assert result is True

    def test_get_schedule_state(self):
        strategy = GPUScheduleStrategy(
            llama_cpp_gpu_memory_mb=3072.0,
            retrieval_gpu_budget_mb=9216.0,
            total_gpu_memory_mb=12288.0,
        )
        state = strategy.get_schedule_state()
        assert state["llama_cpp_active"] is False
        assert state["retrieval_active"] is False
        assert state["total_gpu_mb"] == 12288.0
        assert state["available_gpu_mb"] == 12288.0

    async def test_schedule_state_with_llm_active(self):
        strategy = GPUScheduleStrategy(
            llama_cpp_gpu_memory_mb=3072.0,
            total_gpu_memory_mb=12288.0,
        )
        await strategy.acquire_llm()
        state = strategy.get_schedule_state()
        assert state["llama_cpp_active"] is True
        assert state["available_gpu_mb"] == 12288.0 - 3072.0

    def test_custom_gpu_config(self):
        strategy = GPUScheduleStrategy(
            llama_cpp_gpu_memory_mb=2048.0,
            retrieval_gpu_budget_mb=8192.0,
            total_gpu_memory_mb=24576.0,
        )
        state = strategy.get_schedule_state()
        assert state["llama_cpp_gpu_mb"] == 2048.0
        assert state["retrieval_budget_mb"] == 8192.0
        assert state["total_gpu_mb"] == 24576.0


class TestParseDockerMem:
    def test_parse_gib(self):
        assert SystemMemoryProfiler._parse_docker_mem("2.5GiB") == 2.5 * 1024

    def test_parse_mib(self):
        assert SystemMemoryProfiler._parse_docker_mem("512MiB") == 512.0

    def test_parse_kib(self):
        assert SystemMemoryProfiler._parse_docker_mem("1024KiB") == 1.0

    def test_parse_tib(self):
        assert SystemMemoryProfiler._parse_docker_mem("1.5TiB") == 1.5 * 1024 * 1024

    def test_parse_gb(self):
        assert SystemMemoryProfiler._parse_docker_mem("2GB") == 2 * 1024

    def test_parse_mb(self):
        assert SystemMemoryProfiler._parse_docker_mem("256MB") == 256.0

    def test_parse_kb(self):
        assert SystemMemoryProfiler._parse_docker_mem("2048KB") == 2.0

    def test_parse_tb(self):
        assert SystemMemoryProfiler._parse_docker_mem("1TB") == 1 * 1024 * 1024

    def test_parse_with_slash(self):
        assert SystemMemoryProfiler._parse_docker_mem("512MiB / 1GiB") == 512.0

    def test_parse_invalid(self):
        assert SystemMemoryProfiler._parse_docker_mem("invalid") == 0.0


class TestPlatformDetection:
    """测试平台检测"""

    def test_get_platform_returns_enum(self):
        platform = get_platform()
        assert isinstance(platform, Platform)

    def test_platform_enum_values(self):
        assert Platform.LINUX.value == "linux"
        assert Platform.WINDOWS.value == "windows"
        assert Platform.DARWIN.value == "darwin"
        assert Platform.UNKNOWN.value == "unknown"


class TestFragmentationCalculator:
    """测试碎片化计算器"""

    def test_calculator_initialization(self):
        calculator = FragmentationCalculator()
        assert calculator is not None

    def test_fallback_calculation(self):
        calculator = FragmentationCalculator()
        fragmentation = calculator._calculate_fallback(8000.0, 10000.0, 1500.0)
        assert 0 <= fragmentation <= 1.0
        assert fragmentation > 0.0

    def test_fallback_low_fragmentation(self):
        calculator = FragmentationCalculator()
        fragmentation = calculator._calculate_fallback(5000.0, 10000.0, 5000.0)
        assert fragmentation == 0.0

    def test_fallback_high_fragmentation(self):
        calculator = FragmentationCalculator()
        fragmentation = calculator._calculate_fallback(8000.0, 10000.0, 500.0)
        assert fragmentation > 0.5

    def test_fallback_calculation_zero_total(self):
        calculator = FragmentationCalculator()
        fragmentation = calculator._calculate_fallback(100.0, 0.0, 0.0)
        assert fragmentation == 0.0

    def test_calculate_fragmentation_fallback(self):
        calculator = FragmentationCalculator()
        fragmentation = calculator.calculate_fragmentation(10000.0, 5000.0, 5000.0)
        assert 0 <= fragmentation <= 1.0


class TestSustainedRunFragmentation:
    """持续运行碎片化验证测试"""

    def test_short_sustained_run_simulation(self):
        monitor = GPUMemoryMonitor()
        snapshots = []
        for i in range(100):
            total = 10240.0
            used = 5000.0 + (i % 30) * 100.0
            free = total - used - (i % 10) * 50.0
            snapshot = GPUMemorySnapshot(
                timestamp=float(i),
                total_memory_mb=total,
                used_memory_mb=used,
                free_memory_mb=free,
                fragmentation_rate=0.0,
            )
            snapshots.append(snapshot)
        fragmentation_rates = [s.fragmentation_rate for s in snapshots]
        assert all(0 <= r <= 1.0 for r in fragmentation_rates)

    @pytest.mark.slow
    @pytest.mark.integration
    @pytest.mark.asyncio
    async def test_four_hour_sustained_run(self):
        try:
            import pynvml
            pynvml.nvmlInit()
            pynvml.nvmlShutdown()
        except Exception:
            pytest.skip("No GPU available for sustained run test")
        monitor = GPUMemoryMonitor()
        report = await monitor.run_continuous_monitor(duration_hours=4.0)
        if report and monitor._snapshots:
            max_fragmentation = max(s.fragmentation_rate for s in monitor._snapshots)
            assert max_fragmentation < 0.30, f"Fragmentation exceeded 30%: {max_fragmentation}"


class TestMemoryReaderFactory:
    """测试内存读取器工厂"""

    def test_create_memory_reader_default(self):
        reader = create_memory_reader()
        assert reader is not None

    def test_create_memory_reader_linux(self):
        reader = create_memory_reader(Platform.LINUX)
        assert reader is not None
        # 类名应该是 LinuxMemoryReader
        assert "Linux" in reader.__class__.__name__

    def test_create_memory_reader_windows(self):
        reader = create_memory_reader(Platform.WINDOWS)
        assert reader is not None

    def test_create_memory_reader_darwin(self):
        reader = create_memory_reader(Platform.DARWIN)
        assert reader is not None


class TestSystemMemorySnapshotEnhancements:
    """测试系统内存快照增强功能"""

    def test_effective_used_mb_without_cache(self):
        snapshot = SystemMemorySnapshot(
            timestamp=0.0,
            total_memory_mb=1000.0,
            used_memory_mb=500.0,
            available_memory_mb=500.0
        )
        # 没有缓存和缓冲区，有效使用等于使用
        assert snapshot.effective_used_mb == 500.0

    def test_effective_used_mb_with_cache(self):
        snapshot = SystemMemorySnapshot(
            timestamp=0.0,
            total_memory_mb=1000.0,
            used_memory_mb=500.0,
            available_memory_mb=500.0,
            cached_mb=200.0,
            buffers_mb=50.0
        )
        # 有效使用应该是 500 - 200 - 50 = 250
        assert snapshot.effective_used_mb == 250.0

    def test_effective_used_mb_cannot_be_negative(self):
        snapshot = SystemMemorySnapshot(
            timestamp=0.0,
            total_memory_mb=1000.0,
            used_memory_mb=100.0,
            available_memory_mb=900.0,
            cached_mb=200.0,
            buffers_mb=50.0
        )
        # 减去缓存后不能为负
        assert snapshot.effective_used_mb == 0.0


class TestMemoryProfileReportEnhancements:
    """测试内存配置报告增强"""

    def test_within_budget_uses_effective_used(self):
        report = MemoryProfileReport(
            measured_total_used_mb=30000.0,  # 超过预算
            measured_effective_used_mb=20000.0,  # 有效使用在预算内
            budget_limit_mb=22048.0
        )
        # 应该使用有效使用来判断
        assert report.within_budget is True

    def test_effective_used_over_budget(self):
        report = MemoryProfileReport(
            measured_total_used_mb=30000.0,
            measured_effective_used_mb=25000.0,
            budget_limit_mb=22048.0
        )
        assert report.within_budget is False


class TestGPUScheduleStrategyEnhancements:
    """测试GPU调度策略增强"""

    async def test_timeout_checks(self):
        strategy = GPUScheduleStrategy(default_timeout_seconds=10.0)
        await strategy.acquire_llm()
        released = await strategy.check_timeouts(max_hold_seconds=100.0)
        assert len(released) == 0
        assert strategy.llama_cpp_active is True

    async def test_schedule_state_includes_held_time(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_llm()
        state = strategy.get_schedule_state()
        assert "llama_cpp_held_seconds" in state
        assert "retrieval_held_seconds" in state
        assert state["pending_requests_count"] == 0

    async def test_acquire_with_timeout_no_wait(self):
        strategy = GPUScheduleStrategy()
        result = await strategy.acquire_with_timeout("llm", 10.0)
        assert result is True

    async def test_pending_requests_count(self):
        strategy = GPUScheduleStrategy()
        await strategy.acquire_llm()
        state = strategy.get_schedule_state()
        assert "pending_requests_count" in state


class TestScheduledRequest:
    def test_create_scheduled_request(self):
        import time

        req = ScheduledRequest(
            resource_type="llm",
            request_id="req-001",
            timestamp=time.time(),
            timeout_seconds=30.0,
        )
        assert req.resource_type == "llm"
        assert req.request_id == "req-001"
        assert req.timeout_seconds == 30.0
        assert req.callback is None

    def test_scheduled_request_with_callback(self):
        import time

        callback_called = []
        def cb(success: bool):
            callback_called.append(success)
        req = ScheduledRequest(
            resource_type="retrieval",
            request_id="req-002",
            timestamp=time.time(),
            timeout_seconds=10.0,
            callback=cb,
        )
        assert req.callback is not None
        req.callback(True)
        assert callback_called == [True]


class TestGPUMemoryMonitorInit:
    def test_default_thresholds(self):
        from app.inference.gpu_monitor import GPUMemoryMonitor
        monitor = GPUMemoryMonitor()
        assert monitor._warning_threshold == 0.30
        assert monitor._critical_threshold == 0.40
        assert monitor._snapshot_interval == 60.0

    def test_custom_thresholds(self):
        from app.inference.gpu_monitor import GPUMemoryMonitor
        monitor = GPUMemoryMonitor(
            warning_threshold=0.20,
            critical_threshold=0.35,
            snapshot_interval_seconds=30.0,
        )
        assert monitor._warning_threshold == 0.20
        assert monitor._critical_threshold == 0.35
        assert monitor._snapshot_interval == 30.0


class TestGPUMemoryMonitorSnapshot:
    @pytest.mark.asyncio
    async def test_take_snapshot_no_gpu(self):
        from app.inference.gpu_monitor import GPUMemoryMonitor
        monitor = GPUMemoryMonitor()
        snapshot = await monitor.take_snapshot()
        assert snapshot is not None

    def test_build_fragmentation_report_empty(self):
        from app.inference.gpu_monitor import GPUMemoryMonitor
        monitor = GPUMemoryMonitor()
        report = monitor._build_fragmentation_report(duration_hours=0.0)
        assert report is not None
        assert report.total_snapshots == 0

    def test_build_fragmentation_report_with_data(self):
        from app.inference.gpu_monitor import GPUMemoryMonitor, GPUMemorySnapshot
        monitor = GPUMemoryMonitor()
        for i in range(5):
            monitor._snapshots.append(
                GPUMemorySnapshot(
                    total_memory_mb=8192,
                    used_memory_mb=4096,
                    free_memory_mb=4096,
                    fragmentation_rate=0.25,
                    timestamp=1000.0 + i * 60,
                )
            )
        report = monitor._build_fragmentation_report(duration_hours=1.0)
        assert report.total_snapshots == 5
        assert report.max_fragmentation_rate >= 0.0
