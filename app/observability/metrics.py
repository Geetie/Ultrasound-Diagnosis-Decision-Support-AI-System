"""
VictoriaMetrics内存监控模块

实现内存监控、超限检测和自动降频功能，满足以下验收标准：
- 内存占用<200MB
- 超150MB Warning
- 超180MB Critical
- 自动降采集频率到60s
"""

import asyncio
import psutil
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable, Dict, Any

import structlog


_internal_logger = structlog.get_logger("app.observability.metrics")


class MemoryThreshold(Enum):
    WARNING = 150
    CRITICAL = 180
    MAX = 200


class AlertLevel(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class MemoryMetrics:
    rss_mb: float
    vms_mb: float
    percent: float
    timestamp: float
    alert_level: AlertLevel


@dataclass
class MemoryMonitorConfig:
    warning_threshold_mb: float = 150.0
    critical_threshold_mb: float = 180.0
    max_allowed_mb: float = 200.0
    reduced_scrape_interval: float = 60.0
    default_scrape_interval: float = 15.0
    check_interval: float = 5.0


@dataclass
class MemoryMonitorMetrics:
    current_rss_mb: float = 0.0
    peak_rss_mb: float = 0.0
    alert_level: AlertLevel = AlertLevel.NORMAL
    is_reduced_frequency: bool = False
    scrape_interval: float = 15.0
    total_checks: int = 0
    warning_count: int = 0
    critical_count: int = 0


class VictoriaMetricsMemoryMonitor:
    DEFAULT_SCRAPE_INTERVAL = 15.0
    REDUCED_SCRAPE_INTERVAL = 60.0
    WARNING_THRESHOLD_MB = 150.0
    CRITICAL_THRESHOLD_MB = 180.0
    MAX_ALLOWED_MB = 200.0

    def __init__(
        self,
        process_id: Optional[int] = None,
        config: Optional[MemoryMonitorConfig] = None,
    ):
        self._config = config or MemoryMonitorConfig()
        self._process = psutil.Process(process_id)
        self._metrics = MemoryMonitorMetrics(
            scrape_interval=self._config.default_scrape_interval
        )
        self._lock = asyncio.Lock()
        self._shutdown_event = asyncio.Event()
        self._monitor_task: Optional[asyncio.Task] = None
        self._alert_callbacks: list[Callable[[AlertLevel, MemoryMetrics], Awaitable[None]]] = []
        self._last_alert_level = AlertLevel.NORMAL

    async def start(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._shutdown_event.clear()
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            _internal_logger.info("Memory monitor started",
                                  scrape_interval=self._metrics.scrape_interval)

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._monitor_task and not self._monitor_task.done():
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        _internal_logger.info("Memory monitor stopped")

    async def _monitor_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._check_memory()
            except Exception as e:
                _internal_logger.error("Memory check failed", error=str(e))
            await asyncio.sleep(self._config.check_interval)

    async def _check_memory(self) -> None:
        try:
            memory_info = self._process.memory_info()
            rss_mb = memory_info.rss / (1024 * 1024)
            vms_mb = memory_info.vms / (1024 * 1024)
            percent = self._process.memory_percent()
        except Exception as e:
            _internal_logger.error("Failed to get memory info", error=str(e))
            return

        alert_level = self._calculate_alert_level(rss_mb)

        async with self._lock:
            self._metrics.current_rss_mb = rss_mb
            if rss_mb > self._metrics.peak_rss_mb:
                self._metrics.peak_rss_mb = rss_mb
            self._metrics.total_checks += 1

            old_level = self._metrics.alert_level
            self._metrics.alert_level = alert_level

            if alert_level == AlertLevel.WARNING and old_level != AlertLevel.WARNING:
                self._metrics.warning_count += 1
            elif alert_level == AlertLevel.CRITICAL and old_level != AlertLevel.CRITICAL:
                self._metrics.critical_count += 1

            if alert_level in (AlertLevel.WARNING, AlertLevel.CRITICAL):
                self._handle_high_memory(alert_level, rss_mb)

        metrics = MemoryMetrics(
            rss_mb=rss_mb,
            vms_mb=vms_mb,
            percent=percent,
            timestamp=time.time(),
            alert_level=alert_level
        )

        if alert_level != self._last_alert_level:
            await self._trigger_alerts(alert_level, metrics)
            self._last_alert_level = alert_level

        if rss_mb > self._config.max_allowed_mb:
            _internal_logger.error("Memory exceeded maximum allowed",
                                 current_mb=rss_mb,
                                 max_mb=self._config.max_allowed_mb)

    def _calculate_alert_level(self, rss_mb: float) -> AlertLevel:
        if rss_mb >= self._config.critical_threshold_mb:
            return AlertLevel.CRITICAL
        elif rss_mb >= self._config.warning_threshold_mb:
            return AlertLevel.WARNING
        return AlertLevel.NORMAL

    def _handle_high_memory(self, alert_level: AlertLevel, rss_mb: float) -> None:
        if not self._metrics.is_reduced_frequency:
            if alert_level == AlertLevel.CRITICAL:
                _internal_logger.warning("Memory critical - reducing scrape interval",
                                       current_mb=rss_mb,
                                       threshold_mb=self._config.critical_threshold_mb,
                                       new_interval=self._config.reduced_scrape_interval)
                self._metrics.scrape_interval = self._config.reduced_scrape_interval
                self._metrics.is_reduced_frequency = True
            elif alert_level == AlertLevel.WARNING:
                _internal_logger.warning("Memory warning",
                                       current_mb=rss_mb,
                                       threshold_mb=self._config.warning_threshold_mb)
        elif alert_level == AlertLevel.NORMAL and self._metrics.is_reduced_frequency:
            _internal_logger.info("Memory normalized - restoring scrape interval",
                                current_mb=rss_mb,
                                restored_interval=self._config.default_scrape_interval)
            self._metrics.scrape_interval = self._config.default_scrape_interval
            self._metrics.is_reduced_frequency = False

    async def _trigger_alerts(self, level: AlertLevel, metrics: MemoryMetrics) -> None:
        for callback in self._alert_callbacks:
            try:
                await callback(level, metrics)
            except Exception as e:
                _internal_logger.error("Alert callback failed", error=str(e))

    def register_alert_callback(
        self,
        callback: Callable[[AlertLevel, MemoryMetrics], Awaitable[None]]
    ) -> None:
        self._alert_callbacks.append(callback)

    def get_metrics(self) -> Dict[str, Any]:
        return {
            "current_rss_mb": self._metrics.current_rss_mb,
            "peak_rss_mb": self._metrics.peak_rss_mb,
            "alert_level": self._metrics.alert_level.value,
            "is_reduced_frequency": self._metrics.is_reduced_frequency,
            "scrape_interval": self._metrics.scrape_interval,
            "total_checks": self._metrics.total_checks,
            "warning_count": self._metrics.warning_count,
            "critical_count": self._metrics.critical_count,
        }

    def get_current_memory_mb(self) -> float:
        return self._metrics.current_rss_mb

    def is_within_limits(self) -> bool:
        return self._metrics.current_rss_mb < self._config.max_allowed_mb


_memory_monitor: Optional[VictoriaMetricsMemoryMonitor] = None
_monitor_lock = asyncio.Lock()


async def get_memory_monitor(
    config: Optional[MemoryMonitorConfig] = None
) -> VictoriaMetricsMemoryMonitor:
    global _memory_monitor
    async with _monitor_lock:
        if _memory_monitor is None:
            _memory_monitor = VictoriaMetricsMemoryMonitor(config=config)
            await _memory_monitor.start()
        return _memory_monitor


async def shutdown_memory_monitor() -> None:
    global _memory_monitor
    async with _monitor_lock:
        if _memory_monitor is not None:
            await _memory_monitor.stop()
            _memory_monitor = None
