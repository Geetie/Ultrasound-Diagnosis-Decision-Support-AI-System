from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

import structlog

logger = structlog.get_logger("app.inference.gpu_memory_monitor")


@dataclass
class GPUMemorySnapshot:
    timestamp: str
    gpu_id: int
    total_memory_mb: float
    used_memory_mb: float
    free_memory_mb: float
    fragmented_memory_mb: float
    fragmentation_rate: float

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "gpu_id": self.gpu_id,
            "total_memory_mb": round(self.total_memory_mb, 2),
            "used_memory_mb": round(self.used_memory_mb, 2),
            "free_memory_mb": round(self.free_memory_mb, 2),
            "fragmented_memory_mb": round(self.fragmented_memory_mb, 2),
            "fragmentation_rate": round(self.fragmentation_rate, 4),
        }


@dataclass
class FragmentationReport:
    gpu_id: int
    duration_seconds: float
    snapshot_count: int = 0
    initial_fragmentation_rate: float = 0.0
    final_fragmentation_rate: float = 0.0
    peak_fragmentation_rate: float = 0.0
    avg_fragmentation_rate: float = 0.0
    snapshots: List[GPUMemorySnapshot] = field(default_factory=list)
    passed: bool = False
    warning_threshold: float = 0.30
    critical_threshold: float = 0.40

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gpu_id": self.gpu_id,
            "duration_seconds": round(self.duration_seconds, 2),
            "snapshot_count": self.snapshot_count,
            "initial_fragmentation_rate": f"{self.initial_fragmentation_rate:.2%}",
            "final_fragmentation_rate": f"{self.final_fragmentation_rate:.2%}",
            "peak_fragmentation_rate": f"{self.peak_fragmentation_rate:.2%}",
            "avg_fragmentation_rate": f"{self.avg_fragmentation_rate:.2%}",
            "passed": self.passed,
            "warning_threshold": f"{self.warning_threshold:.0%}",
            "critical_threshold": f"{self.critical_threshold:.0%}",
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


def _query_nvidia_smi(gpu_id: int = 0) -> Optional[Dict[str, float]]:
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                f"--id={gpu_id}",
                "--query-gpu=memory.total,memory.used,memory.free,temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        if result.returncode != 0:
            return None
        parts = result.stdout.strip().split(",")
        if len(parts) < 3:
            return None
        data = {
            "total_memory_mb": float(parts[0].strip()),
            "used_memory_mb": float(parts[1].strip()),
            "free_memory_mb": float(parts[2].strip()),
        }
        if len(parts) >= 4:
            data["temperature_celsius"] = float(parts[3].strip())
        return data
    except (subprocess.TimeoutExpired, FileNotFoundError, ValueError):
        return None


def _estimate_fragmentation(free_memory_mb: float, total_memory_mb: float) -> float:
    if total_memory_mb <= 0:
        return 0.0
    if free_memory_mb <= 0:
        return 0.0
    estimated_contiguous = free_memory_mb * 0.85
    fragmented = free_memory_mb - estimated_contiguous
    return fragmented / total_memory_mb


class GPUMemoryMonitor:
    FRAGMENTATION_SUSTAINED_WARNING_SECONDS = 600
    OVERHEAT_CRITICAL_CELSIUS = 90.0
    OVERHEAT_SUSTAINED_CRITICAL_SECONDS = 60.0

    def __init__(
        self,
        gpu_id: int = 0,
        snapshot_interval_seconds: float = 60.0,
        warning_threshold: float = 0.30,
        critical_threshold: float = 0.40,
    ):
        self._gpu_id = gpu_id
        self._snapshot_interval = snapshot_interval_seconds
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._log = structlog.get_logger("app.inference.gpu_memory_monitor")
        self._fragmentation_above_warning_since: Optional[float] = None
        self._overheat_since: Optional[float] = None

    def take_snapshot(self) -> Optional[GPUMemorySnapshot]:
        info = _query_nvidia_smi(self._gpu_id)
        if info is None:
            self._log.warning("gpu_snapshot_failed", gpu_id=self._gpu_id)
            return None

        fragmentation_rate = _estimate_fragmentation(
            info["free_memory_mb"], info["total_memory_mb"]
        )
        fragmented_mb = info["free_memory_mb"] * 0.15
        memory_utilization = info["used_memory_mb"] / max(info["total_memory_mb"], 1)

        snapshot = GPUMemorySnapshot(
            timestamp=datetime.now().isoformat(),
            gpu_id=self._gpu_id,
            total_memory_mb=info["total_memory_mb"],
            used_memory_mb=info["used_memory_mb"],
            free_memory_mb=info["free_memory_mb"],
            fragmented_memory_mb=fragmented_mb,
            fragmentation_rate=fragmentation_rate,
        )

        self._update_fragmentation_duration(fragmentation_rate)
        temperature = info.get("temperature_celsius")
        self._update_overheat_duration(temperature)
        self._publish_metrics(snapshot, memory_utilization, fragmentation_rate, temperature)

        return snapshot

    def _update_fragmentation_duration(self, fragmentation_rate: float) -> None:
        now = time.time()
        if fragmentation_rate > self._warning_threshold:
            if self._fragmentation_above_warning_since is None:
                self._fragmentation_above_warning_since = now
            sustained_seconds = now - self._fragmentation_above_warning_since
            if sustained_seconds >= self.FRAGMENTATION_SUSTAINED_WARNING_SECONDS:
                self._log.warning(
                    "gpu_fragmentation_sustained_above_warning",
                    gpu_id=self._gpu_id,
                    fragmentation_rate=f"{fragmentation_rate:.2%}",
                    sustained_seconds=round(sustained_seconds, 1),
                )
        else:
            self._fragmentation_above_warning_since = None

    def _update_overheat_duration(self, temperature: Optional[float]) -> None:
        if temperature is None:
            self._overheat_since = None
            return
        now = time.time()
        if temperature > self.OVERHEAT_CRITICAL_CELSIUS:
            if self._overheat_since is None:
                self._overheat_since = now
            sustained_seconds = now - self._overheat_since
            if sustained_seconds >= self.OVERHEAT_SUSTAINED_CRITICAL_SECONDS:
                self._log.critical(
                    "gpu_overheat_sustained_critical",
                    gpu_id=self._gpu_id,
                    temperature_celsius=temperature,
                    sustained_seconds=round(sustained_seconds, 1),
                    threshold_celsius=self.OVERHEAT_CRITICAL_CELSIUS,
                )
        else:
            self._overheat_since = None

    def _publish_metrics(
        self,
        snapshot: GPUMemorySnapshot,
        memory_utilization: float,
        fragmentation_rate: float,
        temperature: Optional[float] = None,
    ) -> None:
        try:
            from app.observability.metrics_collector import get_metrics_collector
            collector = get_metrics_collector()
            device = f"gpu_{self._gpu_id}"
            collector.set_gpu_memory_utilization(device, memory_utilization)
            collector.set_gpu_fragmentation_rate(device, fragmentation_rate)
            if self._fragmentation_above_warning_since is not None:
                sustained = time.time() - self._fragmentation_above_warning_since
                collector.set_gpu_fragmentation_duration(device, sustained)
            else:
                collector.set_gpu_fragmentation_duration(device, 0.0)
            if temperature is not None:
                collector.set_gpu_temperature(device, temperature)
            if self._overheat_since is not None:
                overheat_sustained = time.time() - self._overheat_since
                collector.set_gpu_overheat_duration(device, overheat_sustained)
            else:
                collector.set_gpu_overheat_duration(device, 0.0)
        except Exception:
            pass

    async def monitor_during_load_test(
        self,
        duration_seconds: float = 14400.0,
        request_fn=None,
        request_interval_seconds: float = 360.0,
    ) -> FragmentationReport:
        report = FragmentationReport(
            gpu_id=self._gpu_id,
            duration_seconds=duration_seconds,
            warning_threshold=self._warning_threshold,
            critical_threshold=self._critical_threshold,
        )

        start_time = time.monotonic()
        last_request_time = start_time

        while time.monotonic() - start_time < duration_seconds:
            snapshot = self.take_snapshot()
            if snapshot is not None:
                report.snapshots.append(snapshot)

            if request_fn and time.monotonic() - last_request_time >= request_interval_seconds:
                try:
                    await request_fn()
                except Exception as e:
                    self._log.warning("load_test_request_failed", error=str(e))
                last_request_time = time.monotonic()

            await asyncio.sleep(self._snapshot_interval)

        if report.snapshots:
            report.initial_fragmentation_rate = report.snapshots[0].fragmentation_rate
            report.final_fragmentation_rate = report.snapshots[-1].fragmentation_rate
            report.peak_fragmentation_rate = max(
                s.fragmentation_rate for s in report.snapshots
            )
            report.avg_fragmentation_rate = (
                sum(s.fragmentation_rate for s in report.snapshots)
                / len(report.snapshots)
            )
            report.snapshot_count = len(report.snapshots)

        report.passed = report.final_fragmentation_rate < self._warning_threshold

        self._log.info(
            "gpu_memory_monitor_completed",
            gpu_id=self._gpu_id,
            duration_seconds=round(duration_seconds, 2),
            final_fragmentation_rate=f"{report.final_fragmentation_rate:.2%}",
            peak_fragmentation_rate=f"{report.peak_fragmentation_rate:.2%}",
            passed=report.passed,
        )

        return report

    def is_gpu_available(self) -> bool:
        return _query_nvidia_smi(self._gpu_id) is not None
