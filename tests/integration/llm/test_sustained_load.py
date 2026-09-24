import os

import pytest

from app.inference.sustained_load_test import SustainedLoadTest, SustainedLoadTestReport

SUSTAINED_LOAD_ENABLED = os.environ.get("SUSTAINED_LOAD_ENABLED", "0") == "1"


@pytest.mark.skipif(not SUSTAINED_LOAD_ENABLED, reason="Sustained load test disabled (set SUSTAINED_LOAD_ENABLED=1)")
class TestSustainedLoad:
    @pytest.mark.asyncio
    async def test_quick_5min_sustained_load(self, mock_inference_engine):
        cases = [
            {"prompt": "甲状腺结节", "organ": "thyroid"},
            {"prompt": "肝脏占位", "organ": "liver"},
            {"prompt": "LVEF 55%", "organ": "cardiac"},
        ]
        test = SustainedLoadTest(
            engine=mock_inference_engine,
            window_seconds=30.0,
            max_latency_degradation_pct=20.0,
            max_hallucination_drift=0.005,
        )
        report = await test.run(
            cases=cases,
            duration_seconds=60.0,
            interval_seconds=0.1,
        )
        assert isinstance(report, SustainedLoadTestReport)
        assert report.total_requests > 0
        assert report.duration_seconds > 0

    @pytest.mark.asyncio
    async def test_4hour_sustained_load(self, mock_inference_engine):
        cases = [
            {"prompt": "甲状腺结节", "organ": "thyroid"},
            {"prompt": "肝脏占位", "organ": "liver"},
            {"prompt": "LVEF 55%", "organ": "cardiac"},
        ]
        test = SustainedLoadTest(
            engine=mock_inference_engine,
            window_seconds=300.0,
            max_latency_degradation_pct=20.0,
            max_hallucination_drift=0.005,
        )
        report = await test.run(
            cases=cases,
            duration_seconds=14400.0,
            interval_seconds=1.0,
        )
        assert isinstance(report, SustainedLoadTestReport)
        assert report.total_requests > 0


class TestSustainedLoadReport:
    def test_report_defaults(self):
        report = SustainedLoadTestReport(duration_seconds=60.0)
        assert report.total_requests == 0
        assert report.total_errors == 0
        assert report.passed is False
        assert report.latency_degradation_pct == 0.0
        assert report.hallucination_drift == 0.0
