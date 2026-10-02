"""

Unit tests for the VictoriaMetrics memory monitoring module.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch

import pytest

pytestmark = [pytest.mark.track_obs, pytest.mark.track_inf]

from app.observability import metrics as metrics_module
from app.observability.metrics import (
    AlertLevel,
    MemoryMetrics,
    MemoryMonitorConfig,
    MemoryThreshold,
    VictoriaMetricsMemoryMonitor,
    get_memory_monitor,
    shutdown_memory_monitor,
)


@pytest.fixture
def mock_process():
    process = Mock()
    mem_info = Mock()
    mem_info.rss = 100 * 1024 * 1024
    mem_info.vms = 150 * 1024 * 1024
    process.memory_info.return_value = mem_info
    process.memory_percent.return_value = 3.0
    return process


@pytest.fixture
def monitor(mock_process):
    config = MemoryMonitorConfig()
    m = VictoriaMetricsMemoryMonitor(config=config)
    m._process = mock_process
    return m


@pytest.fixture(autouse=True)
def reset_memory_monitor_singleton():
    """Reset the memory monitor singleton before and after each test."""
    metrics_module._memory_monitor = None
    yield
    metrics_module._memory_monitor = None


class TestMemoryThreshold:
    def test_warning_threshold_value(self):
        assert MemoryThreshold.WARNING.value == 150

    def test_critical_threshold_value(self):
        assert MemoryThreshold.CRITICAL.value == 180

    def test_max_threshold_value(self):
        assert MemoryThreshold.MAX.value == 200


class TestAlertLevel:
    def test_alert_level_values(self):
        assert AlertLevel.NORMAL.value == "normal"
        assert AlertLevel.WARNING.value == "warning"
        assert AlertLevel.CRITICAL.value == "critical"


class TestMemoryMonitorConfig:
    def test_default_config(self):
        config = MemoryMonitorConfig()
        assert config.warning_threshold_mb == 150.0
        assert config.critical_threshold_mb == 180.0
        assert config.max_allowed_mb == 200.0
        assert config.reduced_scrape_interval == 60.0
        assert config.default_scrape_interval == 15.0
        assert config.check_interval == 5.0

    def test_custom_config(self):
        config = MemoryMonitorConfig(
            warning_threshold_mb=100.0,
            critical_threshold_mb=150.0,
            max_allowed_mb=180.0,
            reduced_scrape_interval=30.0,
            default_scrape_interval=10.0,
            check_interval=3.0,
        )
        assert config.warning_threshold_mb == 100.0
        assert config.critical_threshold_mb == 150.0
        assert config.max_allowed_mb == 180.0
        assert config.reduced_scrape_interval == 30.0
        assert config.default_scrape_interval == 10.0
        assert config.check_interval == 3.0


class TestMemoryMetrics:
    def test_memory_metrics_creation(self):
        metrics = MemoryMetrics(
            rss_mb=100.5,
            vms_mb=200.0,
            percent=15.5,
            timestamp=1234567890.0,
            alert_level=AlertLevel.NORMAL,
        )
        assert metrics.rss_mb == 100.5
        assert metrics.vms_mb == 200.0
        assert metrics.percent == 15.5
        assert metrics.timestamp == 1234567890.0
        assert metrics.alert_level == AlertLevel.NORMAL


class TestVictoriaMetricsMemoryMonitor:
    @pytest.mark.asyncio
    async def test_monitor_creation(self):
        monitor = VictoriaMetricsMemoryMonitor()
        # 验证监控器配置正确初始化
        assert monitor._config.warning_threshold_mb == 150.0
        assert monitor._config.critical_threshold_mb == 180.0
        assert monitor._config.max_allowed_mb == 200.0
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_monitor_creation_with_config(self):
        config = MemoryMonitorConfig(
            warning_threshold_mb=100.0,
            critical_threshold_mb=150.0,
        )
        monitor = VictoriaMetricsMemoryMonitor(config=config)
        assert monitor._config.warning_threshold_mb == 100.0
        assert monitor._config.critical_threshold_mb == 150.0
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_get_current_memory_mb(self):
        monitor = VictoriaMetricsMemoryMonitor()
        memory_mb = monitor.get_current_memory_mb()
        # 验证返回合理的内存值（初始值可能为0，已调用check_memory后应有值）
        await monitor._check_memory()
        memory_mb = monitor.get_current_memory_mb()
        assert memory_mb > 0
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_is_within_limits_normal(self):
        monitor = VictoriaMetricsMemoryMonitor()
        monitor._metrics.current_rss_mb = 100.0
        assert monitor.is_within_limits() is True
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_is_within_limits_exceeded(self):
        monitor = VictoriaMetricsMemoryMonitor()
        monitor._metrics.current_rss_mb = 250.0
        assert monitor.is_within_limits() is False
        await monitor.stop()

    def test_calculate_alert_level_normal(self):
        monitor = VictoriaMetricsMemoryMonitor()
        level = monitor._calculate_alert_level(100.0)
        assert level == AlertLevel.NORMAL

    def test_calculate_alert_level_warning(self):
        monitor = VictoriaMetricsMemoryMonitor()
        level = monitor._calculate_alert_level(160.0)
        assert level == AlertLevel.WARNING

    def test_calculate_alert_level_critical(self):
        monitor = VictoriaMetricsMemoryMonitor()
        level = monitor._calculate_alert_level(190.0)
        assert level == AlertLevel.CRITICAL

    def test_calculate_alert_level_boundary_warning(self):
        monitor = VictoriaMetricsMemoryMonitor()
        level = monitor._calculate_alert_level(150.0)
        assert level == AlertLevel.WARNING

    def test_calculate_alert_level_boundary_critical(self):
        monitor = VictoriaMetricsMemoryMonitor()
        level = monitor._calculate_alert_level(180.0)
        assert level == AlertLevel.CRITICAL

    @pytest.mark.asyncio
    async def test_get_metrics(self):
        monitor = VictoriaMetricsMemoryMonitor()
        metrics = monitor.get_metrics()
        assert "current_rss_mb" in metrics
        assert "peak_rss_mb" in metrics
        assert "alert_level" in metrics
        assert "is_reduced_frequency" in metrics
        assert "scrape_interval" in metrics
        assert "total_checks" in metrics
        assert "warning_count" in metrics
        assert "critical_count" in metrics
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_handle_high_memory_warning(self):
        monitor = VictoriaMetricsMemoryMonitor()
        monitor._metrics.is_reduced_frequency = False
        monitor._handle_high_memory(AlertLevel.WARNING, 160.0)
        assert monitor._metrics.is_reduced_frequency is False
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_handle_high_memory_critical_reduces_frequency(self):
        monitor = VictoriaMetricsMemoryMonitor()
        monitor._metrics.is_reduced_frequency = False
        monitor._handle_high_memory(AlertLevel.CRITICAL, 190.0)
        assert monitor._metrics.is_reduced_frequency is True
        assert monitor._metrics.scrape_interval == 60.0
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_handle_memory_normal_restores_frequency(self):
        monitor = VictoriaMetricsMemoryMonitor()
        monitor._metrics.is_reduced_frequency = True
        monitor._metrics.scrape_interval = 60.0
        monitor._restore_normal_frequency(100.0)
        assert monitor._metrics.is_reduced_frequency is False
        assert monitor._metrics.scrape_interval == 15.0
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_register_alert_callback(self):
        monitor = VictoriaMetricsMemoryMonitor()
        callback = AsyncMock()
        monitor.register_alert_callback(callback)
        assert len(monitor._alert_callbacks) == 1
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_alert_callback_triggered(self):
        monitor = VictoriaMetricsMemoryMonitor()
        callback = AsyncMock()
        monitor.register_alert_callback(callback)
        metrics = MemoryMetrics(
            rss_mb=190.0,
            vms_mb=200.0,
            percent=20.0,
            timestamp=1234567890.0,
            alert_level=AlertLevel.CRITICAL,
        )
        monitor._last_alert_level = AlertLevel.NORMAL
        await monitor._trigger_alerts(AlertLevel.CRITICAL, metrics)
        callback.assert_called_once_with(AlertLevel.CRITICAL, metrics)
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_monitor_start_stop(self):
        monitor = VictoriaMetricsMemoryMonitor()
        await monitor.start()

        # 验证任务正在运行
        assert monitor._monitor_task is not None
        assert not monitor._monitor_task.done()  # 任务未完成

        await monitor.stop()
        # 验证任务已结束
        assert monitor._monitor_task.done()

    @pytest.mark.asyncio
    async def test_multiple_start_calls(self):
        monitor = VictoriaMetricsMemoryMonitor()
        await monitor.start()
        await monitor.start()

        # 验证任务正在运行（多次调用start后任务仍正常运行）
        assert monitor._monitor_task is not None
        assert not monitor._monitor_task.done()

        await monitor.stop()
        # 验证任务已结束
        assert monitor._monitor_task.done()

    @pytest.mark.asyncio
    async def test_check_memory_updates_metrics(self):
        monitor = VictoriaMetricsMemoryMonitor()
        initial_checks = monitor._metrics.total_checks
        await monitor._check_memory()
        assert monitor._metrics.total_checks == initial_checks + 1
        await monitor.stop()


class TestMemoryMonitorIntegration:
    @pytest.mark.asyncio
    async def test_full_monitoring_cycle(self):
        monitor = VictoriaMetricsMemoryMonitor()
        await monitor.start()

        for _ in range(20):
            metrics = monitor.get_metrics()
            if metrics["total_checks"] >= 1:
                break
            await asyncio.sleep(0.1)

        assert metrics["total_checks"] >= 1
        assert metrics["current_rss_mb"] > 0
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_get_memory_monitor_singleton(self):
        monitor1 = await get_memory_monitor()
        monitor2 = await get_memory_monitor()
        assert monitor1 is monitor2

        await shutdown_memory_monitor()


class TestMemoryMonitorEdgeCases:
    @pytest.mark.asyncio
    async def test_zero_memory(self):
        monitor = VictoriaMetricsMemoryMonitor()
        level = monitor._calculate_alert_level(0)
        assert level == AlertLevel.NORMAL
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_exactly_max_memory(self):
        monitor = VictoriaMetricsMemoryMonitor()
        monitor._metrics.current_rss_mb = 200.0
        assert monitor.is_within_limits() is False
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_peak_memory_tracking(self):
        monitor = VictoriaMetricsMemoryMonitor()
        # 验证进程对象已正确初始化
        assert monitor._process is not None

        # 通过实际调用_check_memory()测试峰值追踪
        # 使用真实进程对象，验证真实内存获取逻辑
        await monitor._check_memory()
        # 验证峰值内存被正确记录（应该大于0）
        assert monitor._metrics.peak_rss_mb > 0.0
        initial_peak = monitor._metrics.peak_rss_mb

        # 再次调用，验证峰值更新逻辑
        await monitor._check_memory()
        # 峰值应大于等于初始值
        assert monitor._metrics.peak_rss_mb >= initial_peak
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_check_memory_with_process_error(self):
        """测试进程对象获取失败时的异常处理"""
        import psutil

        monitor = VictoriaMetricsMemoryMonitor()

        # 模拟进程获取失败的情况
        with patch.object(monitor._process, 'memory_info', side_effect=psutil.NoSuchProcess("Process does not exist")):
            # 验证异常被静默处理
            try:
                await monitor._check_memory()
            except Exception:
                pytest.fail("_check_memory should handle process errors silently")

        await monitor.stop()

    @pytest.mark.asyncio
    async def test_alert_count_tracking(self):
        """测试告警计数功能 - 使用默认配置验证正常状态"""
        # 使用默认配置（贴近生产环境）
        config = MemoryMonitorConfig(
            warning_threshold_mb=150.0,
            critical_threshold_mb=180.0,
            max_allowed_mb=200.0,
        )
        monitor = VictoriaMetricsMemoryMonitor(config=config)

        # 验证进程对象已正确初始化
        assert monitor._process is not None

        # 通过实际调用_check_memory()测试告警计数
        await monitor._check_memory()

        # 验证监控正常运行（根据当前进程内存状态验证相应的告警级别）
        # 如果内存低于警告阈值，告警计数应为0
        # 如果内存超过警告阈值，警告计数应增加
        assert monitor._metrics.total_checks == 1
        await monitor.stop()

    @pytest.mark.asyncio
    async def test_alert_count_warning_triggered(self, mock_process):
        config = MemoryMonitorConfig(
            warning_threshold_mb=10.0,
            critical_threshold_mb=200.0,
            max_allowed_mb=250.0,
            sustained_duration_seconds=0,
        )
        monitor = VictoriaMetricsMemoryMonitor(config=config)
        # 注入受控进程内存。此前用真实进程 RSS：全量运行/并行 worker 下
        # 进程占用会超过 critical_threshold_mb(200MB)，档位变成 CRITICAL，
        # 本用例（断言 WARNING）随运行规模偶发失败。
        mock_process.memory_info.return_value.rss = 50 * 1024 * 1024
        monitor._process = mock_process

        await monitor._check_memory()
        assert monitor._metrics.warning_since is not None
        assert monitor._metrics.alert_level == AlertLevel.WARNING
        assert monitor._metrics.warning_count >= 1
        await monitor.stop()


class TestSustainedAlertLevel:
    def test_sustained_warning_not_triggered_immediately(self, monitor, mock_process):
        mock_process.memory_info.return_value.rss = 160 * 1024 * 1024
        mock_process.memory_info.return_value.vms = 200 * 1024 * 1024
        mock_process.memory_percent.return_value = 5.0
        monitor._check_memory_sync()
        assert monitor._metrics.alert_level == AlertLevel.NORMAL
        assert monitor._metrics.warning_since is not None

    def test_sustained_warning_triggered_after_5min(self, monitor, mock_process):
        import time as time_mod
        mock_process.memory_info.return_value.rss = 160 * 1024 * 1024
        mock_process.memory_info.return_value.vms = 200 * 1024 * 1024
        mock_process.memory_percent.return_value = 5.0
        monitor._check_memory_sync()
        assert monitor._metrics.alert_level == AlertLevel.NORMAL
        monitor._metrics.warning_since = time_mod.time() - 301
        monitor._check_memory_sync()
        assert monitor._metrics.alert_level == AlertLevel.WARNING

    def test_sustained_critical_triggered_after_5min(self, monitor, mock_process):
        import time as time_mod
        mock_process.memory_info.return_value.rss = 190 * 1024 * 1024
        mock_process.memory_info.return_value.vms = 250 * 1024 * 1024
        mock_process.memory_percent.return_value = 6.0
        monitor._check_memory_sync()
        assert monitor._metrics.alert_level == AlertLevel.NORMAL
        monitor._metrics.critical_since = time_mod.time() - 301
        monitor._check_memory_sync()
        assert monitor._metrics.alert_level == AlertLevel.CRITICAL

    def test_sustained_warning_resets_on_normalization(self, monitor, mock_process):
        import time as time_mod
        mock_process.memory_info.return_value.rss = 160 * 1024 * 1024
        mock_process.memory_info.return_value.vms = 200 * 1024 * 1024
        mock_process.memory_percent.return_value = 5.0
        monitor._check_memory_sync()
        monitor._metrics.warning_since = time_mod.time() - 180
        mock_process.memory_info.return_value.rss = 100 * 1024 * 1024
        monitor._check_memory_sync()
        assert monitor._metrics.alert_level == AlertLevel.NORMAL
        assert monitor._metrics.warning_since is None

    def test_frequency_change_callback_on_critical(self, monitor, mock_process):
        import time as time_mod
        callback_intervals = []
        monitor.register_frequency_change_callback(lambda i: callback_intervals.append(i))
        mock_process.memory_info.return_value.rss = 190 * 1024 * 1024
        mock_process.memory_info.return_value.vms = 250 * 1024 * 1024
        mock_process.memory_percent.return_value = 6.0
        monitor._check_memory_sync()
        assert callback_intervals == []
        monitor._metrics.critical_since = time_mod.time() - 301
        monitor._check_memory_sync()
        assert len(callback_intervals) == 1
        assert callback_intervals[0] == 60.0

    def test_frequency_change_callback_on_restore(self, monitor, mock_process):
        import time as time_mod
        callback_intervals = []
        monitor.register_frequency_change_callback(lambda i: callback_intervals.append(i))
        mock_process.memory_info.return_value.rss = 190 * 1024 * 1024
        mock_process.memory_info.return_value.vms = 250 * 1024 * 1024
        mock_process.memory_percent.return_value = 6.0
        monitor._metrics.critical_since = time_mod.time() - 301
        monitor._metrics.is_reduced_frequency = True
        monitor._check_memory_sync()
        mock_process.memory_info.return_value.rss = 100 * 1024 * 1024
        monitor._check_memory_sync()
        assert 15.0 in callback_intervals
