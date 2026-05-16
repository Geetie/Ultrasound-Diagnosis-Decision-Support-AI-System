from app.inference.vram_fragmentation import (
    VRAMFragmentationReport,
    VRAMSnapshot,
    VRAMFragmentationVerifier,
)



import pytest
pytestmark = [pytest.mark.track_inf, pytest.mark.track_obs]
class TestVRAMSnapshot:
    def test_snapshot_fields(self):
        s = VRAMSnapshot(
            timestamp="2026-05-11T10:00:00",
            total_vram_mb=12288.0,
            used_vram_mb=8192.0,
            free_vram_mb=4096.0,
            fragmented_vram_mb=1228.8,
            fragmentation_rate=0.1,
        )
        assert s.total_vram_mb == 12288.0
        assert s.fragmentation_rate == 0.1


class TestVRAMFragmentationReport:
    def test_warning_threshold(self):
        report = VRAMFragmentationReport()
        report.peak_fragmentation_rate = 0.35
        assert report.peak_fragmentation_rate > report.WARNING_THRESHOLD

    def test_hotreload_threshold(self):
        report = VRAMFragmentationReport()
        report.peak_fragmentation_rate = 0.45
        assert report.peak_fragmentation_rate > report.HOTRELOAD_THRESHOLD

    def test_no_warning_below_threshold(self):
        report = VRAMFragmentationReport()
        report.peak_fragmentation_rate = 0.25
        assert report.peak_fragmentation_rate < report.WARNING_THRESHOLD

    def test_report_serialization(self):
        report = VRAMFragmentationReport(
            peak_fragmentation_rate=0.35,
            avg_fragmentation_rate=0.28,
            warning_triggered=True,
            hotreload_triggered=False,
            duration_seconds=60.0,
            sample_count=10,
        )
        d = report.to_dict()
        assert d["peak_fragmentation_rate"] == 0.35
        assert d["warning_triggered"] is True
        assert "WARNING_THRESHOLD" not in d

        json_str = report.to_json()
        assert "peak_fragmentation_rate" in json_str

    def test_avg_fragmentation_rate_calculation(self):
        snapshots = [
            VRAMSnapshot("t1", 12288, 8192, 4096, 1228.8, 0.10),
            VRAMSnapshot("t2", 12288, 9216, 3072, 921.6, 0.075),
        ]
        report = VRAMFragmentationReport(snapshots=snapshots, sample_count=2)
        report.avg_fragmentation_rate = sum(s.fragmentation_rate for s in snapshots) / len(snapshots)
        assert abs(report.avg_fragmentation_rate - 0.0875) < 0.001


class TestFragmentationRateCalculation:
    def test_estimate_fragmented_vram(self):
        fragmented = VRAMFragmentationVerifier._estimate_fragmented_vram(
            total_mb=12288.0, used_mb=8192.0, free_mb=4096.0,
        )
        assert fragmented == pytest.approx(1228.8, abs=0.1)

    def test_zero_free_vram(self):
        fragmented = VRAMFragmentationVerifier._estimate_fragmented_vram(
            total_mb=12288.0, used_mb=12288.0, free_mb=0.0,
        )
        assert fragmented == 0.0
