import asyncio
from typing import Any

import structlog

from app.config.settings import settings
from app.observability.constants import VMPusherConstants

from .collector import MetricsCollector

_logger = structlog.get_logger("app.observability.metrics_collector")


class VictoriaMetricsPusher:
    MAX_CONSECUTIVE_FAILURES = VMPusherConstants.MAX_CONSECUTIVE_FAILURES
    BASE_RETRY_DELAY = VMPusherConstants.BASE_RETRY_DELAY
    MAX_RETRY_DELAY = VMPusherConstants.MAX_RETRY_DELAY

    def __init__(
        self,
        vm_url: str | None = None,
        interval: float = 15.0,
        collector: MetricsCollector | None = None,
    ):
        self._vm_url = vm_url or settings.obs_victoriametrics_url
        self._interval = interval
        self._collector = collector or MetricsCollector.get_instance()
        self._task: asyncio.Task | None = None
        self._shutdown_event = asyncio.Event()
        self._push_count = 0
        self._fail_count = 0
        self._consecutive_failures = 0

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._shutdown_event.clear()
        self._task = asyncio.create_task(self._push_loop())
        _logger.info("VictoriaMetrics pusher started", url=self._vm_url, interval=self._interval)

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):  # silent-ok: 停机路径：先取消推送任务再等待其结束
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:  # silent-ok: 停机时推送任务被取消属正常控制流
                    pass
        _logger.info("VictoriaMetrics pusher stopped", push_count=self._push_count, fail_count=self._fail_count)

    async def _push_loop(self) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._push_metrics()
                self._consecutive_failures = 0
            except Exception as e:
                _logger.error("VictoriaMetrics push failed", error=str(e))
                self._fail_count += 1
                self._consecutive_failures += 1
            delay = self._calculate_delay()
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
            except asyncio.TimeoutError:  # silent-ok: 推送周期等待超时属正常轮询节奏
                pass

    def _calculate_delay(self) -> float:
        if self._consecutive_failures == 0:
            return self._interval
        if self._consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            _logger.critical(
                "VictoriaMetrics pusher circuit_open",
                consecutive_failures=self._consecutive_failures,
            )
            return self.MAX_RETRY_DELAY
        backoff = min(
            self.BASE_RETRY_DELAY * (2 ** (self._consecutive_failures - 1)),
            self.MAX_RETRY_DELAY,
        )
        return backoff

    async def _push_metrics(self) -> None:
        import httpx

        metrics_text = self._collector.get_metrics_text()
        url = f"{self._vm_url}/api/v1/import/prometheus"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(url, content=metrics_text.encode("utf-8"))
            if resp.status_code == 204:
                self._push_count += 1
            else:
                _logger.warning(
                    "VictoriaMetrics push returned non-204",
                    status_code=resp.status_code,
                )
                self._fail_count += 1

    def get_stats(self) -> dict[str, Any]:
        return {
            "push_count": self._push_count,
            "fail_count": self._fail_count,
            "vm_url": self._vm_url,
            "interval": self._interval,
        }

    def set_interval(self, new_interval: float) -> None:
        self._interval = new_interval
        _logger.info("VictoriaMetrics pusher interval updated", new_interval=new_interval)
