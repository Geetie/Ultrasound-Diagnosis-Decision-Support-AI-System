"""Unit tests for app.agents.monitor (T-GA-AGT-07).

Covers:
- Constructor validation
- R1 (low confidence → needs_review)
- R2 (latency > 5s → performance_degraded)
- R3 (consecutive failures per organ → system_anomaly)
- detect_anomaly() pre-check rules
- Sliding window reset / inspection helpers
- Metrics埋点: graceful degradation when collector unavailable
- Non-terminal status (processing) skips R1/R2 but still updates window
- Failure detection: status="failed" / "timeout" / ai_diagnosis_failed=True

Note on logger capture: the project uses ``structlog`` with ``JSONRenderer``,
which writes to ``sys.stdout`` rather than the standard library ``logging``
handlers. As a result, pytest's ``caplog`` fixture cannot capture structlog
output. We therefore use ``unittest.mock.patch`` on the module-level
``logger`` object to record calls directly. The ``_has_tag`` helper checks
whether a given tag was logged at the expected level.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_agt]

from app.agents.monitor import (
    CONFIDENCE_REVIEW_THRESHOLD,
    CONSECUTIVE_FAILURE_WINDOW,
    LATENCY_DEGRADED_THRESHOLD_MS,
    TAG_NEEDS_REVIEW,
    TAG_PERFORMANCE_DEGRADED,
    TAG_SYSTEM_ANOMALY,
    MonitorAgentImpl,
)
from app.models.schemas import (
    DiagnosisQualityControl,
    DiagnosisRequest,
    DiagnosisResult,
    Organ,
)

# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def _req(organ: Organ = Organ.THYROID, image_ids: list[str] = None) -> DiagnosisRequest:
    return DiagnosisRequest(
        session_id="s1",
        image_ids=image_ids if image_ids is not None else ["img1"],
        target_organ=organ,
    )


def _result(
    status: str = "completed",
    organ: Organ = Organ.THYROID,
    confidence: float = 0.8,
    processing_time_ms: int = 1000,
    ai_diagnosis_failed: bool = False,
    request_id: str = "r1",
) -> DiagnosisResult:
    return DiagnosisResult(
        request_id=request_id,
        status=status,
        desensitization_status="full",
        consent_status_at_output="granted",
        fusion_status="normal",
        diagnosis={},
        quality_control=DiagnosisQualityControl(confidence_score=confidence),
        processing_time_ms=processing_time_ms,
        ai_diagnosis_failed=ai_diagnosis_failed,
    )


def _has_tag(mock_logger, level: str, tag: str) -> bool:
    """Return True if any call to ``mock_logger.<level>(...)`` had ``tag=<tag>`` kwarg."""
    method = getattr(mock_logger, level)
    for call in method.call_args_list:
        if call.kwargs.get("tag") == tag:
            return True
    return False


@pytest.fixture
def monitor_logger():
    """Patch the module-level structlog logger to a MagicMock for call inspection."""
    with patch("app.agents.monitor.logger") as mock_logger:
        yield mock_logger


# ----------------------------------------------------------------------
# Constructor
# ----------------------------------------------------------------------


class TestConstructor:
    def test_default_construction(self):
        m = MonitorAgentImpl()
        assert m is not None

    def test_invalid_window_rejected(self):
        with pytest.raises(ValueError):
            MonitorAgentImpl(consecutive_failure_window=0)
        with pytest.raises(ValueError):
            MonitorAgentImpl(consecutive_failure_window=-1)


# ----------------------------------------------------------------------
# R1 — low confidence
# ----------------------------------------------------------------------


class TestRuleR1:
    @pytest.mark.asyncio
    async def test_low_confidence_triggers_needs_review(self, monitor_logger):
        m = MonitorAgentImpl()
        await m.track_diagnosis(
            _req(),
            _result(confidence=CONFIDENCE_REVIEW_THRESHOLD - 0.01),
        )
        assert _has_tag(monitor_logger, "warning", TAG_NEEDS_REVIEW)

    @pytest.mark.asyncio
    async def test_confidence_at_threshold_does_not_trigger(self, monitor_logger):
        m = MonitorAgentImpl()
        await m.track_diagnosis(
            _req(),
            _result(confidence=CONFIDENCE_REVIEW_THRESHOLD),
        )
        assert not _has_tag(monitor_logger, "warning", TAG_NEEDS_REVIEW)

    @pytest.mark.asyncio
    async def test_high_confidence_no_trigger(self, monitor_logger):
        m = MonitorAgentImpl()
        await m.track_diagnosis(_req(), _result(confidence=0.9))
        assert not _has_tag(monitor_logger, "warning", TAG_NEEDS_REVIEW)

    @pytest.mark.asyncio
    async def test_no_quality_control_means_confidence_zero(self, monitor_logger):
        m = MonitorAgentImpl()
        result = _result()
        result.quality_control = None
        await m.track_diagnosis(_req(), result)
        assert _has_tag(monitor_logger, "warning", TAG_NEEDS_REVIEW)


# ----------------------------------------------------------------------
# R2 — latency degradation
# ----------------------------------------------------------------------


class TestRuleR2:
    @pytest.mark.asyncio
    async def test_above_threshold_triggers_performance_degraded(self, monitor_logger):
        m = MonitorAgentImpl()
        await m.track_diagnosis(
            _req(),
            _result(processing_time_ms=LATENCY_DEGRADED_THRESHOLD_MS + 1),
        )
        assert _has_tag(monitor_logger, "warning", TAG_PERFORMANCE_DEGRADED)

    @pytest.mark.asyncio
    async def test_at_threshold_does_not_trigger(self, monitor_logger):
        m = MonitorAgentImpl()
        await m.track_diagnosis(
            _req(),
            _result(processing_time_ms=LATENCY_DEGRADED_THRESHOLD_MS),
        )
        assert not _has_tag(monitor_logger, "warning", TAG_PERFORMANCE_DEGRADED)

    @pytest.mark.asyncio
    async def test_below_threshold_no_trigger(self, monitor_logger):
        m = MonitorAgentImpl()
        await m.track_diagnosis(_req(), _result(processing_time_ms=100))
        assert not _has_tag(monitor_logger, "warning", TAG_PERFORMANCE_DEGRADED)


# ----------------------------------------------------------------------
# R3 — consecutive failures per organ
# ----------------------------------------------------------------------


class TestRuleR3:
    @pytest.mark.asyncio
    async def test_consecutive_failures_trigger_system_anomaly(self, monitor_logger):
        m = MonitorAgentImpl()
        for i in range(CONSECUTIVE_FAILURE_WINDOW):
            await m.track_diagnosis(
                _req(Organ.LIVER),
                _result(organ=Organ.LIVER, status="failed", request_id=f"r{i}"),
            )
        assert _has_tag(monitor_logger, "error", TAG_SYSTEM_ANOMALY)

    @pytest.mark.asyncio
    async def test_failures_split_by_organ(self, monitor_logger):
        """每个 organ 独立维护窗口，跨 organ 失败不应互计数。"""
        m = MonitorAgentImpl()
        # 5 LIVER failures
        for i in range(5):
            await m.track_diagnosis(
                _req(Organ.LIVER),
                _result(organ=Organ.LIVER, status="failed", request_id=f"l{i}"),
            )
        # 5 THYROID failures
        for i in range(5):
            await m.track_diagnosis(
                _req(Organ.THYROID),
                _result(organ=Organ.THYROID, status="failed", request_id=f"t{i}"),
            )
        # Neither organ hit the threshold (each only has 5, threshold is 10)
        assert not _has_tag(monitor_logger, "error", TAG_SYSTEM_ANOMALY)

    @pytest.mark.asyncio
    async def test_success_resets_window(self, monitor_logger):
        m = MonitorAgentImpl()
        # 9 failures
        for i in range(9):
            await m.track_diagnosis(
                _req(Organ.CARDIAC),
                _result(organ=Organ.CARDIAC, status="failed", request_id=f"c{i}"),
            )
        # 1 success — deque(maxlen=10) keeps 9 failures + 1 success
        # → "all False" condition fails on this call
        await m.track_diagnosis(
            _req(Organ.CARDIAC),
            _result(organ=Organ.CARDIAC, status="completed", request_id="c9"),
        )
        # 1 more failure
        await m.track_diagnosis(
            _req(Organ.CARDIAC),
            _result(organ=Organ.CARDIAC, status="failed", request_id="c10"),
        )
        # No system_anomaly should have fired
        assert not _has_tag(monitor_logger, "error", TAG_SYSTEM_ANOMALY)

    @pytest.mark.asyncio
    async def test_ai_diagnosis_failed_counts_as_failure(self, monitor_logger):
        m = MonitorAgentImpl()
        for i in range(CONSECUTIVE_FAILURE_WINDOW):
            await m.track_diagnosis(
                _req(Organ.THYROID),
                _result(
                    organ=Organ.THYROID,
                    status="completed",
                    ai_diagnosis_failed=True,
                    request_id=f"r{i}",
                ),
            )
        assert _has_tag(monitor_logger, "error", TAG_SYSTEM_ANOMALY)

    @pytest.mark.asyncio
    async def test_timeout_counts_as_failure(self, monitor_logger):
        m = MonitorAgentImpl()
        for i in range(CONSECUTIVE_FAILURE_WINDOW):
            await m.track_diagnosis(
                _req(Organ.LIVER),
                _result(organ=Organ.LIVER, status="timeout", request_id=f"r{i}"),
            )
        assert _has_tag(monitor_logger, "error", TAG_SYSTEM_ANOMALY)


# ----------------------------------------------------------------------
# Non-terminal status
# ----------------------------------------------------------------------


class TestNonTerminal:
    @pytest.mark.asyncio
    async def test_processing_skips_r1_r2(self, monitor_logger):
        m = MonitorAgentImpl()
        # Even with bad confidence + high latency, processing status skips R1/R2
        await m.track_diagnosis(
            _req(),
            _result(
                status="processing",
                confidence=0.05,
                processing_time_ms=99999,
            ),
        )
        # R1 (needs_review) and R2 (performance_degraded) should NOT fire
        assert not _has_tag(monitor_logger, "warning", TAG_NEEDS_REVIEW)
        assert not _has_tag(monitor_logger, "warning", TAG_PERFORMANCE_DEGRADED)


# ----------------------------------------------------------------------
# detect_anomaly
# ----------------------------------------------------------------------


class TestDetectAnomaly:
    @pytest.mark.asyncio
    async def test_missing_image_ids(self):
        m = MonitorAgentImpl()
        result = await m.detect_anomaly(_req(image_ids=[]))
        assert result == "missing_image_ids"

    @pytest.mark.asyncio
    async def test_valid_request(self):
        m = MonitorAgentImpl()
        result = await m.detect_anomaly(_req())
        assert result is None


# ----------------------------------------------------------------------
# Metrics埋点: 优雅降级
# ----------------------------------------------------------------------


class TestMetricsDegradation:
    @pytest.mark.asyncio
    async def test_collector_unavailable_does_not_break(self):
        m = MonitorAgentImpl(
            metrics_collector_factory=lambda: (_ for _ in ()).throw(
                ConnectionError("collector down")
            )
        )
        # Should not raise
        await m.track_diagnosis(_req(), _result())

    @pytest.mark.asyncio
    async def test_collector_called_with_seconds(self):
        collector = MagicMock()
        collector.record_diagnosis_latency = MagicMock()
        m = MonitorAgentImpl(metrics_collector_factory=lambda: collector)

        await m.track_diagnosis(
            _req(),
            _result(processing_time_ms=2500),
        )
        collector.record_diagnosis_latency.assert_called_once()
        args, kwargs = collector.record_diagnosis_latency.call_args
        # First positional arg should be seconds (2.5)
        assert args[0] == pytest.approx(2.5)

    @pytest.mark.asyncio
    async def test_health_rate_updated_on_system_anomaly(self):
        collector = MagicMock()
        collector.record_diagnosis_latency = MagicMock()
        collector.set_service_health_rate = MagicMock()
        m = MonitorAgentImpl(metrics_collector_factory=lambda: collector)

        for i in range(CONSECUTIVE_FAILURE_WINDOW):
            await m.track_diagnosis(
                _req(Organ.THYROID),
                _result(organ=Organ.THYROID, status="failed", request_id=f"r{i}"),
            )
        collector.set_service_health_rate.assert_called_with(0.5)


# ----------------------------------------------------------------------
# Window helpers
# ----------------------------------------------------------------------


class TestWindowHelpers:
    @pytest.mark.asyncio
    async def test_window_populated_after_track(self):
        m = MonitorAgentImpl()
        await m.track_diagnosis(_req(Organ.THYROID), _result())
        window = m.get_failure_window(Organ.THYROID)
        assert len(window) == 1
        assert window[0] is True  # success → True

    @pytest.mark.asyncio
    async def test_window_records_failure(self):
        m = MonitorAgentImpl()
        await m.track_diagnosis(_req(Organ.THYROID), _result(status="failed"))
        window = m.get_failure_window(Organ.THYROID)
        assert window[0] is False

    def test_reset_clears_all_windows(self):
        m = MonitorAgentImpl()
        m._failure_windows[Organ.THYROID].append(True)
        m._failure_windows[Organ.LIVER].append(True)
        m.reset()
        assert len(m.get_failure_window(Organ.THYROID)) == 0
        assert len(m.get_failure_window(Organ.LIVER)) == 0

    @pytest.mark.asyncio
    async def test_window_bounded_by_configured_size(self):
        m = MonitorAgentImpl(consecutive_failure_window=3)
        for i in range(10):
            await m.track_diagnosis(_req(Organ.LIVER), _result(organ=Organ.LIVER))
        window = m.get_failure_window(Organ.LIVER)
        assert len(window) == 3


# ----------------------------------------------------------------------
# health_check
# ----------------------------------------------------------------------


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_returns_true(self):
        m = MonitorAgentImpl()
        assert await m.health_check() is True

    @pytest.mark.asyncio
    async def test_does_not_mutate_state(self):
        m = MonitorAgentImpl()
        await m.track_diagnosis(_req(), _result())
        assert await m.health_check() is True
        # health_check 之后窗口状态保持不变
        assert len(m.get_failure_window(Organ.THYROID)) == 1
