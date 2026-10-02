import time
from dataclasses import dataclass
from enum import Enum


class StepStatus(Enum):
    STARTED = "▶ STARTED"
    PASSED = "✓ PASSED"
    FAILED = "✗ FAILED"
    SKIPPED = "⊘ SKIPPED"
    WARNING = "⚠ WARNING"
    INFO = "ℹ INFO"


@dataclass
class StepRecord:
    timestamp: float
    test_name: str
    step_name: str
    status: StepStatus
    detail: str | None = None
    duration_ms: float | None = None


class StepTracker:
    def __init__(self):
        self._steps: list[StepRecord] = []
        self._start_times: dict[str, float] = {}

    def start_step(self, test_name: str, step_name: str, detail: str = None):
        now = time.perf_counter()
        self._start_times[f"{test_name}.{step_name}"] = now
        record = StepRecord(
            timestamp=time.time(),
            test_name=test_name,
            step_name=step_name,
            status=StepStatus.STARTED,
            detail=detail,
        )
        self._steps.append(record)
        self._print_step(record)

    def end_step(
        self,
        test_name: str,
        step_name: str,
        status: StepStatus = StepStatus.PASSED,
        detail: str = None,
    ):
        key = f"{test_name}.{step_name}"
        start = self._start_times.pop(key, None)
        duration = (time.perf_counter() - start) * 1000 if start else None
        record = StepRecord(
            timestamp=time.time(),
            test_name=test_name,
            step_name=step_name,
            status=status,
            detail=detail,
            duration_ms=duration,
        )
        self._steps.append(record)
        self._print_step(record)

    def _print_step(self, record: StepRecord):
        ts = time.strftime("%H:%M:%S", time.localtime(record.timestamp))
        ms = f" ({record.duration_ms:.1f}ms)" if record.duration_ms else ""
        detail = f" | {record.detail}" if record.detail else ""
        print(f"  [{ts}] {record.status.value} {record.step_name}{ms}{detail}")

    def get_summary(self) -> dict:
        total = len(self._steps)
        passed = sum(1 for s in self._steps if s.status == StepStatus.PASSED)
        failed = sum(1 for s in self._steps if s.status == StepStatus.FAILED)
        started = sum(1 for s in self._steps if s.status == StepStatus.STARTED)
        total_duration = sum(
            s.duration_ms for s in self._steps if s.duration_ms is not None
        )
        return {
            "total_steps": total,
            "passed": passed,
            "failed": failed,
            "started": started,
            "total_duration_ms": total_duration,
        }
