from __future__ import annotations

import asyncio
import json
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

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

    def to_dict(self) -> dict[str, Any]:
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
    snapshots: list[GPUMemorySnapshot] = field(default_factory=list)
    passed: bool = False
    warning_threshold: float = 0.30
    critical_threshold: float = 0.40

    def to_dict(self) -> dict[str, Any]:
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


def _query_nvidia_smi(gpu_id: int = 0) -> dict[str, float] | None:
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


# T-GB-LLM-01：``hot_reload_controller`` 未显式传入时的哨兵——表示"用进程级
# 控制器"，与显式传 ``None``（关闭自动触发）区分开。
_AUTO_HOT_RELOAD = object()


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
        hot_reload_controller: Any = _AUTO_HOT_RELOAD,
    ):
        self._gpu_id = gpu_id
        self._snapshot_interval = snapshot_interval_seconds
        self._warning_threshold = warning_threshold
        self._critical_threshold = critical_threshold
        self._log = structlog.get_logger("app.inference.gpu_memory_monitor")
        self._fragmentation_above_warning_since: float | None = None
        self._overheat_since: float | None = None
        # T-GB-LLM-01：碎片化超临界阈值时触发模型热重载。未传参时用进程级
        # 控制器（延迟解析，避免构造期就要求适配器就绪）；显式传 None 关闭。
        self._hot_reload_controller = hot_reload_controller
        self._hot_reload_tasks: set = set()

    def take_snapshot(self) -> GPUMemorySnapshot | None:
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

    def _resolve_hot_reload_controller(self) -> Any:
        """解析热重载控制器（哨兵 → 进程级单例；``None`` → 关闭）。"""
        if self._hot_reload_controller is _AUTO_HOT_RELOAD:
            try:
                from app.inference.hot_reload import get_hot_reload_controller

                return get_hot_reload_controller()
            except Exception as exc:
                self._log.warning(
                    "hot_reload_controller_unavailable",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                return None
        return self._hot_reload_controller

    def _maybe_trigger_hot_reload(self, fragmentation_rate: float) -> None:
        """碎片化超临界阈值时触发模型热重载（T-GB-LLM-01）。

        本方法位于**同步**采样路径，因此把异步编排交给当前事件循环执行；
        没有运行中的事件循环（如 CLI 单次采样）时记录日志并跳过——真正的
        触发由 ``monitor_during_load_test`` 等异步循环承担。
        """
        if fragmentation_rate <= self._critical_threshold:
            return
        controller = self._resolve_hot_reload_controller()
        if controller is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._log.warning(
                "hot_reload_skipped_no_event_loop",
                fragmentation_rate=round(fragmentation_rate, 4),
            )
            return
        task = loop.create_task(controller.on_fragmentation_sample(fragmentation_rate))
        # 持有引用避免任务被 GC；完成后自动移除。
        self._hot_reload_tasks.add(task)
        task.add_done_callback(self._hot_reload_tasks.discard)

    async def trigger_hot_reload(self, reason: str = "manual") -> Any:
        """显式触发一次热重载（运维/测试用），返回编排结果。"""
        controller = self._resolve_hot_reload_controller()
        if controller is None:
            return None
        return await controller.request_reload(reason)

    def _update_fragmentation_duration(self, fragmentation_rate: float) -> None:
        """更新碎片化持续时长并处理阈值动作。

        T-GB-LLM-01：除原有的"持续超 Warning 阈值告警"外，还负责在碎片化
        超临界阈值时触发模型热重载（见 :meth:`_maybe_trigger_hot_reload`）。
        """
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

        # T-GB-LLM-01：碎片化超临界阈值（默认 >40%）触发模型热重载
        self._maybe_trigger_hot_reload(fragmentation_rate)

    def _update_overheat_duration(self, temperature: float | None) -> None:
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
        temperature: float | None = None,
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
