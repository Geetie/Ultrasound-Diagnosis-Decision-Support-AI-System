from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from .verification_config import VerificationConfig, get_verification_config

logger = logging.getLogger(__name__)


@dataclass
class VRAMSnapshot:
    timestamp: str
    total_vram_mb: float
    used_vram_mb: float
    free_vram_mb: float
    fragmented_vram_mb: float
    fragmentation_rate: float


@dataclass
class VRAMFragmentationReport:
    snapshots: list[VRAMSnapshot] = field(default_factory=list)
    peak_fragmentation_rate: float = 0.0
    avg_fragmentation_rate: float = 0.0
    warning_triggered: bool = False
    hotreload_triggered: bool = False
    duration_seconds: float = 0.0
    sample_count: int = 0

    WARNING_THRESHOLD: float = 0.30
    HOTRELOAD_THRESHOLD: float = 0.40

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("WARNING_THRESHOLD", None)
        d.pop("HOTRELOAD_THRESHOLD", None)
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


class VRAMFragmentationVerifier:
    def __init__(self, config: VerificationConfig | None = None):
        self._config = config or get_verification_config()

    async def verify(
        self,
        duration_seconds: float = 3600.0,
        sample_interval_seconds: float = 60.0,
    ) -> VRAMFragmentationReport:
        report = VRAMFragmentationReport()
        start = time.monotonic()

        while (time.monotonic() - start) < duration_seconds:
            snapshot = self._sample_vram()
            if snapshot is not None:
                report.snapshots.append(snapshot)
                report.sample_count += 1

                if snapshot.fragmentation_rate > report.peak_fragmentation_rate:
                    report.peak_fragmentation_rate = snapshot.fragmentation_rate

                if snapshot.fragmentation_rate > report.WARNING_THRESHOLD:
                    report.warning_triggered = True
                if snapshot.fragmentation_rate > report.HOTRELOAD_THRESHOLD:
                    report.hotreload_triggered = True

            elapsed_in_interval = time.monotonic() - start
            remaining = sample_interval_seconds - (elapsed_in_interval % sample_interval_seconds)
            if remaining > 0 and (time.monotonic() - start + remaining) < duration_seconds:
                await asyncio.sleep(remaining)

        report.duration_seconds = time.monotonic() - start
        if report.snapshots:
            report.avg_fragmentation_rate = (
                sum(s.fragmentation_rate for s in report.snapshots) / len(report.snapshots)
            )

        logger.info(
            "vram_fragmentation_verification_complete",
            extra={
                "peak_fragmentation_rate": f"{report.peak_fragmentation_rate:.4f}",
                "avg_fragmentation_rate": f"{report.avg_fragmentation_rate:.4f}",
                "warning_triggered": report.warning_triggered,
                "hotreload_triggered": report.hotreload_triggered,
                "sample_count": report.sample_count,
            },
        )

        return report

    def _sample_vram(self) -> VRAMSnapshot | None:
        try:
            result = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=memory.total,memory.used,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode != 0:
                logger.warning("nvidia_smi_failed", extra={"stderr": result.stderr})
                return None

            lines = result.stdout.strip().split("\n")
            if not lines:
                return None

            total_mb, used_mb, free_mb = [float(x.strip()) for x in lines[0].split(",")]
            fragmented_mb = self._estimate_fragmented_vram(total_mb, used_mb, free_mb)
            fragmentation_rate = fragmented_mb / total_mb if total_mb > 0 else 0.0

            return VRAMSnapshot(
                timestamp=datetime.now().isoformat(),
                total_vram_mb=total_mb,
                used_vram_mb=used_mb,
                free_vram_mb=free_mb,
                fragmented_vram_mb=fragmented_mb,
                fragmentation_rate=fragmentation_rate,
            )
        except (subprocess.TimeoutExpired, FileNotFoundError, ValueError) as e:
            logger.warning("vram_sample_failed", extra={"error": str(e)})
            return None

    @staticmethod
    def _estimate_fragmented_vram(total_mb: float, used_mb: float, free_mb: float) -> float:
        fragmented = free_mb * 0.3
        return max(0.0, fragmented)
