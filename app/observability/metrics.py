"""
VictoriaMetrics内存监控模块

实现内存监控、超限检测和自动降频功能，满足以下验收标准：
- 内存占用<200MB
- 超150MB持续5分钟 Warning
- 超180MB持续5分钟 Critical
- 自动降采集频率到60s
"""

__all__ = ["MemoryMetrics", "MemoryMonitorConfig", "VictoriaMetricsMemoryMonitor"]

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

import psutil
import structlog

from app.observability.constants import VMConstants
from app.observability.enums import AlertLevel

_logger = structlog.get_logger("app.observability.metrics")


class MemoryThreshold(Enum):
    WARNING = 150
    CRITICAL = 180
    MAX = 200


@dataclass
class MemoryMetrics:
    rss_mb: float
    vms_mb: float
    percent: float
    timestamp: float
    alert_level: AlertLevel


@dataclass
class MemoryMonitorConfig:
    warning_threshold_mb: float = VMConstants.WARNING_THRESHOLD_MB
    critical_threshold_mb: float = VMConstants.CRITICAL_THRESHOLD_MB
    max_allowed_mb: float = VMConstants.MAX_ALLOWED_MB
    reduced_scrape_interval: float = VMConstants.REDUCED_SCRAPE_INTERVAL
    default_scrape_interval: float = VMConstants.DEFAULT_SCRAPE_INTERVAL
    check_interval: float = VMConstants.CHECK_INTERVAL
    sustained_duration_seconds: float = VMConstants.SUSTAINED_DURATION_SECONDS


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
    warning_since: float | None = None
    critical_since: float | None = None


class VictoriaMetricsMemoryMonitor:
    DEFAULT_SCRAPE_INTERVAL = VMConstants.DEFAULT_SCRAPE_INTERVAL
    REDUCED_SCRAPE_INTERVAL = VMConstants.REDUCED_SCRAPE_INTERVAL
    WARNING_THRESHOLD_MB = VMConstants.WARNING_THRESHOLD_MB
    CRITICAL_THRESHOLD_MB = VMConstants.CRITICAL_THRESHOLD_MB
    MAX_ALLOWED_MB = VMConstants.MAX_ALLOWED_MB

    def __init__(
        self,
        process_id: int | None = None,
        config: MemoryMonitorConfig | None = None,
    ):
        self._config = config or MemoryMonitorConfig()
        try:
            self._process = psutil.Process(process_id)
        except psutil.NoSuchProcess:
            _logger.error("target_process_not_found", process_id=process_id)
            raise ValueError(f"Process {process_id} not found")
        self._metrics = MemoryMonitorMetrics(
            scrape_interval=self._config.default_scrape_interval
        )
        self._lock = threading.Lock()
        self._shutdown_event = asyncio.Event()
        self._monitor_task: asyncio.Task | None = None
        self._alert_callbacks: list[Callable[[AlertLevel, MemoryMetrics], Awaitable[None]]] = []
        self._frequency_change_callbacks: list[Callable[[float], Awaitable[None]]] = []
        self._last_alert_level = AlertLevel.NORMAL

    async def start(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            self._shutdown_event.clear()
            self._monitor_task = asyncio.create_task(self._monitor_loop())
            _logger.info("memory_monitor_started",
                                  scrape_interval=self._metrics.scrape_interval)

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._monitor_task and not self._monitor_task.done():
            await asyncio.gather(self._monitor_task, return_exceptions=True)
        _logger.info("memory_monitor_stopped")

    async def _monitor_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._check_memory()
            except Exception as e:
                _logger.error("memory_check_failed", error=str(e))
            await asyncio.sleep(self._config.check_interval)

    async def _check_memory(self) -> None:
        try:
            memory_info = self._process.memory_info()
            rss_mb = memory_info.rss / (1024 * 1024)
            vms_mb = memory_info.vms / (1024 * 1024)
            percent = self._process.memory_percent()
        except Exception as e:
            _logger.error("memory_info_unavailable", error=str(e))
            return

        raw_alert_level = self._calculate_alert_level(rss_mb)
        now = time.time()
        sustained_seconds = self._config.sustained_duration_seconds

        with self._lock:
            self._metrics.current_rss_mb = rss_mb
            if rss_mb > self._metrics.peak_rss_mb:
                self._metrics.peak_rss_mb = rss_mb
            self._metrics.total_checks += 1

            effective_level = AlertLevel.NORMAL

            if raw_alert_level == AlertLevel.CRITICAL:
                if self._metrics.critical_since is None:
                    self._metrics.critical_since = now
                    _logger.info("memory_critical_threshold_crossed_awaiting_sustained",
                                current_mb=rss_mb,
                                threshold_mb=self._config.critical_threshold_mb,
                                sustained_required_seconds=sustained_seconds)
                if now - self._metrics.critical_since >= sustained_seconds:
                    effective_level = AlertLevel.CRITICAL
            elif raw_alert_level == AlertLevel.WARNING:
                if self._metrics.warning_since is None:
                    self._metrics.warning_since = now
                    _logger.info("memory_warning_threshold_crossed_awaiting_sustained",
                                current_mb=rss_mb,
                                threshold_mb=self._config.warning_threshold_mb,
                                sustained_required_seconds=sustained_seconds)
                if now - self._metrics.warning_since >= sustained_seconds:
                    effective_level = AlertLevel.WARNING

            if raw_alert_level == AlertLevel.NORMAL:
                self._metrics.warning_since = None
                self._metrics.critical_since = None

            old_level = self._metrics.alert_level
            self._metrics.alert_level = effective_level

            if effective_level == AlertLevel.WARNING and old_level != AlertLevel.WARNING:
                self._metrics.warning_count += 1
            elif effective_level == AlertLevel.CRITICAL and old_level != AlertLevel.CRITICAL:
                self._metrics.critical_count += 1

            if effective_level in (AlertLevel.WARNING, AlertLevel.CRITICAL):
                self._handle_high_memory(effective_level, rss_mb)
            elif effective_level == AlertLevel.NORMAL and self._metrics.is_reduced_frequency:
                self._restore_normal_frequency(rss_mb)

        metrics = MemoryMetrics(
            rss_mb=rss_mb,
            vms_mb=vms_mb,
            percent=percent,
            timestamp=time.time(),
            alert_level=effective_level
        )

        if effective_level != self._last_alert_level:
            await self._trigger_alerts(effective_level, metrics)
            self._last_alert_level = effective_level

        if rss_mb > self._config.max_allowed_mb:
            _logger.error("memory_exceeded_max",
                         current_mb=rss_mb,
                         max_mb=self._config.max_allowed_mb)

        try:
            from app.observability.metrics_collector import get_metrics_collector
            collector = get_metrics_collector()
            collector.set_victoriametrics_rss_bytes(memory_info.rss)
        except Exception as exc:
            _logger.debug("vm_rss_metric_update_failed", error=str(exc))

    def _calculate_alert_level(self, rss_mb: float) -> AlertLevel:
        if rss_mb >= self._config.critical_threshold_mb:
            return AlertLevel.CRITICAL
        elif rss_mb >= self._config.warning_threshold_mb:
            return AlertLevel.WARNING
        return AlertLevel.NORMAL

    def _handle_high_memory(self, alert_level: AlertLevel, rss_mb: float) -> None:
        if not self._metrics.is_reduced_frequency:
            if alert_level == AlertLevel.CRITICAL:
                _logger.warning("memory_critical_reducing_interval",
                               current_mb=rss_mb,
                               threshold_mb=self._config.critical_threshold_mb,
                               new_interval=self._config.reduced_scrape_interval)
                self._metrics.scrape_interval = self._config.reduced_scrape_interval
                self._metrics.is_reduced_frequency = True
                self._notify_frequency_change(self._config.reduced_scrape_interval)
            elif alert_level == AlertLevel.WARNING:
                _logger.warning("memory_warning",
                               current_mb=rss_mb,
                               threshold_mb=self._config.warning_threshold_mb)

    def _restore_normal_frequency(self, rss_mb: float) -> None:
        _logger.info("memory_normalized_restoring_interval",
                    current_mb=rss_mb,
                    restored_interval=self._config.default_scrape_interval)
        self._metrics.scrape_interval = self._config.default_scrape_interval
        self._metrics.is_reduced_frequency = False
        self._notify_frequency_change(self._config.default_scrape_interval)

    def _notify_frequency_change(self, new_interval: float) -> None:
        for callback in self._frequency_change_callbacks:
            try:
                callback(new_interval)
            except Exception as e:
                _logger.error("frequency_change_callback_failed", error=str(e))

    def register_frequency_change_callback(
        self,
        callback: Callable[[float], None]
    ) -> None:
        self._frequency_change_callbacks.append(callback)

    async def _trigger_alerts(self, level: AlertLevel, metrics: MemoryMetrics) -> None:
        for callback in self._alert_callbacks:
            try:
                await callback(level, metrics)
            except Exception as e:
                _logger.error("alert_callback_failed", error=str(e))

    def register_alert_callback(
        self,
        callback: Callable[[AlertLevel, MemoryMetrics], Awaitable[None]]
    ) -> None:
        self._alert_callbacks.append(callback)

    def _check_memory_sync(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(self._check_memory(), loop).result(timeout=10)
        except RuntimeError:  # silent-ok: 无运行事件循环属同步调用场景，改走 asyncio.run
            asyncio.run(self._check_memory())

    def get_metrics(self) -> dict[str, Any]:
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


_memory_monitor: VictoriaMetricsMemoryMonitor | None = None
_monitor_lock = threading.Lock()


async def get_memory_monitor(
    config: MemoryMonitorConfig | None = None
) -> VictoriaMetricsMemoryMonitor:
    global _memory_monitor
    if _memory_monitor is None:
        with _monitor_lock:
            if _memory_monitor is None:
                _memory_monitor = VictoriaMetricsMemoryMonitor(config=config)
                await _memory_monitor.start()
    return _memory_monitor


async def shutdown_memory_monitor() -> None:
    global _memory_monitor
    with _monitor_lock:
        if _memory_monitor is not None:
            await _memory_monitor.stop()
            _memory_monitor = None
