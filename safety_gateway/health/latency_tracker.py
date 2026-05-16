import asyncio
from collections import deque
from typing import Any, ClassVar

import structlog

from safety_gateway.sg_config import sg_settings as settings

logger = structlog.get_logger()


class LatencyTracker:
    """延迟采样与百分位统计组件

    独立于HealthChecker，可复用于其他模块。
    """

    def __init__(self) -> None:
        self._samples: deque = deque(maxlen=settings.sg_latency_tracker_max_samples)
        self._e2e_samples: deque = deque(maxlen=settings.sg_latency_tracker_max_samples)
        self._lock = asyncio.Lock()
        self._critical_count: int = 0
        self._throttling: bool = False
        self._recovery_count: int = 0

    async def record(self, latency_ms: float) -> None:
        async with self._lock:
            self._samples.append(latency_ms)

    async def record_e2e(self, latency_ms: float) -> None:
        async with self._lock:
            self._e2e_samples.append(latency_ms)

    async def compute_stats(self) -> dict[str, Any]:
        async with self._lock:
            samples = list(self._samples)

            if not samples:
                return {
                    "samples": 0,
                    "p50_ms": None,
                    "p95_ms": None,
                    "p99_ms": None,
                    "avg_ms": None,
                    "max_ms": None,
                    "min_ms": None,
                    "p99_status": "unknown",
                    "p99_threshold_warning_ms": settings.sg_p99_latency_warning_threshold_ms,
                    "p99_threshold_critical_ms": settings.sg_p99_latency_critical_threshold_ms,
                }

            sorted_samples = sorted(samples)
            n = len(sorted_samples)

            p99_value = self._percentile(sorted_samples, 99)
            p99_status = self._get_p99_status(
                p99_value,
                settings.sg_p99_latency_warning_threshold_ms,
                settings.sg_p99_latency_critical_threshold_ms,
            )

            if p99_status == "critical":
                self._recovery_count = 0
                self._critical_count += 1
                if self._critical_count >= 3 and not self._throttling:
                    self._throttling = True
                    logger.critical(
                        "sg_p99_latency_sustained_critical_throttling_enabled",
                        p99_ms=round(p99_value, 2),
                        consecutive_critical=self._critical_count,
                    )
                else:
                    logger.warning(
                        "sg_p99_latency_exceeded",
                        p99_ms=round(p99_value, 2),
                        status=p99_status,
                        consecutive_critical=self._critical_count,
                    )
            elif p99_status == "ok":
                if self._throttling:
                    self._recovery_count += 1
                    if self._recovery_count >= 2:
                        self._throttling = False
                        self._recovery_count = 0
                        logger.info("sg_p99_latency_recovered_throttling_disabled")
                self._critical_count = 0
            else:
                self._critical_count = 0
                self._recovery_count = 0
                logger.warning(
                    "sg_p99_latency_warning",
                    p99_ms=round(p99_value, 2),
                    status=p99_status,
                )

            return {
                "samples": n,
                "p50_ms": round(self._percentile(sorted_samples, 50), 2),
                "p95_ms": round(self._percentile(sorted_samples, 95), 2),
                "p99_ms": round(p99_value, 2),
                "avg_ms": round(sum(sorted_samples) / n, 2),
                "max_ms": round(max(sorted_samples), 2),
                "min_ms": round(min(sorted_samples), 2),
                "p99_status": p99_status,
                "p99_threshold_warning_ms": settings.sg_p99_latency_warning_threshold_ms,
                "p99_threshold_critical_ms": settings.sg_p99_latency_critical_threshold_ms,
            }

    async def compute_e2e_stats(self) -> dict[str, Any]:
        async with self._lock:
            samples = list(self._e2e_samples)
        if not samples:
            return {
                "samples": 0,
                "p99_ms": None,
                "p99_status": "unknown",
                "p99_threshold_warning_ms": settings.sg_e2e_p99_latency_warning_threshold_ms,
                "p99_threshold_critical_ms": settings.sg_e2e_p99_latency_critical_threshold_ms,
            }

        sorted_samples = sorted(samples)
        n = len(sorted_samples)
        p99_value = self._percentile(sorted_samples, 99)

        p99_status = self._get_p99_status(
            p99_value,
            settings.sg_e2e_p99_latency_warning_threshold_ms,
            settings.sg_e2e_p99_latency_critical_threshold_ms,
        )

        if p99_status == "critical":
            logger.critical(
                "sg_e2e_p99_latency_critical",
                p99_ms=round(p99_value, 2),
                status=p99_status,
            )
        elif p99_status == "warning":
            logger.warning(
                "sg_e2e_p99_latency_warning",
                p99_ms=round(p99_value, 2),
                status=p99_status,
            )

        return {
            "samples": n,
            "p50_ms": round(self._percentile(sorted_samples, 50), 2),
            "p95_ms": round(self._percentile(sorted_samples, 95), 2),
            "p99_ms": round(p99_value, 2),
            "avg_ms": round(sum(sorted_samples) / n, 2),
            "max_ms": round(max(sorted_samples), 2),
            "min_ms": round(min(sorted_samples), 2),
            "p99_status": p99_status,
            "p99_threshold_warning_ms": settings.sg_e2e_p99_latency_warning_threshold_ms,
            "p99_threshold_critical_ms": settings.sg_e2e_p99_latency_critical_threshold_ms,
        }

    @staticmethod
    def _percentile(data: list[float], p: int) -> float:
        n = len(data)
        if n == 0:
            return 0.0
        index = (p / 100) * (n - 1)
        if index.is_integer():
            return data[int(index)]
        lower = data[int(index)]
        upper = data[min(int(index) + 1, n - 1)]
        return lower + (upper - lower) * (index - int(index))

    @property
    def is_throttling(self) -> bool:
        return self._throttling

    @staticmethod
    def _get_p99_status(p99_ms: float, warning: float, critical: float) -> str:
        if p99_ms >= critical:
            return "critical"
        elif p99_ms >= warning:
            return "warning"
        else:
            return "ok"
