from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ..protocols import InferenceEngine
from ..hallucination_detector import HallucinationDetector


def _percentile(values: List[float], p: int) -> float:
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    idx = max(0, min(len(sorted_vals) - 1, int(len(sorted_vals) * p / 100)))
    return sorted_vals[idx]


@dataclass
class TimeWindowStats:
    window_start: float
    window_end: float
    request_count: int = 0
    error_count: int = 0
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    hallucination_count: int = 0
    hallucination_rate: float = 0.0


@dataclass
class SustainedLoadTestReport:
    duration_seconds: float
    total_requests: int = 0
    total_errors: int = 0
    total_hallucinations: int = 0
    windows: List[TimeWindowStats] = field(default_factory=list)
    latency_degradation_pct: float = 0.0
    hallucination_drift: float = 0.0
    passed: bool = False


class SustainedLoadTest:
    def __init__(
        self,
        engine: InferenceEngine,
        hallucination_detector: Optional[HallucinationDetector] = None,
        window_seconds: float = 300.0,
        max_latency_degradation_pct: float = 20.0,
        max_hallucination_drift: float = 0.005,
    ):
        self._engine = engine
        self._detector = hallucination_detector
        self._window_seconds = window_seconds
        self._max_latency_degradation_pct = max_latency_degradation_pct
        self._max_hallucination_drift = max_hallucination_drift

    async def run(
        self,
        cases: List[Dict[str, Any]],
        duration_seconds: float = 14400.0,
        interval_seconds: float = 1.0,
    ) -> SustainedLoadTestReport:
        start_time = time.monotonic()
        report = SustainedLoadTestReport(duration_seconds=duration_seconds)

        window_start = start_time
        window_latencies: List[float] = []
        window_errors = 0
        window_requests = 0
        window_hallucinations = 0

        case_idx = 0
        while time.monotonic() - start_time < duration_seconds:
            case = cases[case_idx % len(cases)]
            case_idx += 1

            try:
                t0 = time.monotonic()
                resp = await asyncio.wait_for(
                    self._engine.generate(case.get("prompt", "")),
                    timeout=30.0,
                )
                latency_ms = (time.monotonic() - t0) * 1000.0
                window_latencies.append(latency_ms)
                window_requests += 1

                if self._detector and resp.text:
                    is_hall = self._detector.is_hallucination(resp.text, case.get("organ", ""))
                    if is_hall:
                        window_hallucinations += 1

            except Exception:
                window_errors += 1
                window_requests += 1

            elapsed_in_window = time.monotonic() - window_start
            if elapsed_in_window >= self._window_seconds:
                ws = TimeWindowStats(
                    window_start=window_start - start_time,
                    window_end=time.monotonic() - start_time,
                    request_count=window_requests,
                    error_count=window_errors,
                    latency_p50_ms=_percentile(window_latencies, 50),
                    latency_p99_ms=_percentile(window_latencies, 99),
                    hallucination_count=window_hallucinations,
                    hallucination_rate=window_hallucinations / window_requests if window_requests > 0 else 0.0,
                )
                report.windows.append(ws)
                report.total_requests += window_requests
                report.total_errors += window_errors
                report.total_hallucinations += window_hallucinations

                window_start = time.monotonic()
                window_latencies = []
                window_errors = 0
                window_requests = 0
                window_hallucinations = 0

            if interval_seconds > 0:
                await asyncio.sleep(interval_seconds)

        if window_requests > 0:
            ws = TimeWindowStats(
                window_start=window_start - start_time,
                window_end=time.monotonic() - start_time,
                request_count=window_requests,
                error_count=window_errors,
                latency_p50_ms=_percentile(window_latencies, 50),
                latency_p99_ms=_percentile(window_latencies, 99),
                hallucination_count=window_hallucinations,
                hallucination_rate=window_hallucinations / window_requests if window_requests > 0 else 0.0,
            )
            report.windows.append(ws)
            report.total_requests += window_requests
            report.total_errors += window_errors
            report.total_hallucinations += window_hallucinations

        if len(report.windows) >= 2:
            first_p99 = report.windows[0].latency_p99_ms
            last_p99 = report.windows[-1].latency_p99_ms
            if first_p99 > 0:
                report.latency_degradation_pct = (last_p99 - first_p99) / first_p99 * 100.0

            first_hall_rate = report.windows[0].hallucination_rate
            last_hall_rate = report.windows[-1].hallucination_rate
            report.hallucination_drift = abs(last_hall_rate - first_hall_rate)

        report.passed = (
            report.latency_degradation_pct <= self._max_latency_degradation_pct
            and report.hallucination_drift <= self._max_hallucination_drift
        )

        return report
