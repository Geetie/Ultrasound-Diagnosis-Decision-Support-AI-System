import pytest
import time
from tests.utils.step_tracker import StepTracker, StepStatus

_tracker = StepTracker()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    start = time.perf_counter()
    ts = time.strftime("%H:%M:%S")
    print(f"\n{'=' * 60}")
    print(f"[{ts}] ▶ START: {item.nodeid}")
    print(f"{'=' * 60}")
    yield
    duration = (time.perf_counter() - start) * 1000
    print(f"{'─' * 60}")
    print(
        f"[{time.strftime('%H:%M:%S')}] ■ END: {item.nodeid} ({duration:.0f}ms)"
    )
    print(f"{'=' * 60}\n")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if call.when == "setup":
        _tracker.start_step(item.nodeid, "SETUP")
    elif call.when == "call":
        status = StepStatus.PASSED if report.passed else StepStatus.FAILED
        _tracker.end_step(item.nodeid, "CALL", status)
    elif call.when == "teardown":
        _tracker.end_step(
            item.nodeid,
            "TEARDOWN",
            StepStatus.PASSED if report.passed else StepStatus.WARNING,
        )
