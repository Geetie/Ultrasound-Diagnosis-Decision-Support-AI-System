"""GPU显存碎片化验证集成测试

Gate 0A必须验证项: FunASR+vLLM+TensorRT混合运行显存碎片化率<30%

运行方式:
    set LLM_TEST_MODE=local
    pytest tests/integration/llm/test_gpu_memory_fragmentation.py -v -m phase0a

    # 快速验证（5分钟）
    pytest tests/integration/llm/test_gpu_memory_fragmentation.py -v -m "phase0a and not slow"

    # 完整4小时验证
    pytest tests/integration/llm/test_gpu_memory_fragmentation.py -v -m "phase0a and slow"
"""


import pytest

pytestmark = [pytest.mark.track_llm, pytest.mark.track_int]

from app.inference.gpu_memory_monitor import (
    FragmentationReport,
    GPUMemoryMonitor,
    GPUMemorySnapshot,
    _estimate_fragmentation,
    _query_nvidia_smi,
)
from app.inference.verification_config import get_verification_config


def _gpu_available() -> bool:
    return _query_nvidia_smi(0) is not None


skip_no_gpu = pytest.mark.skipif(
    not _gpu_available(),
    reason="NVIDIA GPU not available or nvidia-smi not found",
)


class TestGPUMemorySnapshot:
    def test_snapshot_to_dict(self):
        snapshot = GPUMemorySnapshot(
            timestamp="2026-01-01T00:00:00",
            gpu_id=0,
            total_memory_mb=12288.0,
            used_memory_mb=8192.0,
            free_memory_mb=4096.0,
            fragmented_memory_mb=614.4,
            fragmentation_rate=0.05,
        )
        d = snapshot.to_dict()
        assert d["gpu_id"] == 0
        assert d["total_memory_mb"] == 12288.0
        assert d["fragmentation_rate"] == 0.05


class TestFragmentationEstimation:
    def test_zero_free_memory(self):
        rate = _estimate_fragmentation(0.0, 12288.0)
        assert rate == 0.0

    def test_zero_total_memory(self):
        rate = _estimate_fragmentation(4096.0, 0.0)
        assert rate == 0.0

    def test_normal_fragmentation(self):
        rate = _estimate_fragmentation(4096.0, 12288.0)
        assert 0.0 < rate < 1.0

    def test_high_free_low_fragmentation(self):
        rate = _estimate_fragmentation(10000.0, 12288.0)
        assert rate < 0.2


class TestFragmentationReport:
    def test_report_to_dict(self):
        report = FragmentationReport(
            gpu_id=0,
            duration_seconds=300.0,
            snapshot_count=5,
            initial_fragmentation_rate=0.05,
            final_fragmentation_rate=0.08,
            peak_fragmentation_rate=0.10,
            avg_fragmentation_rate=0.07,
            passed=True,
        )
        d = report.to_dict()
        assert d["gpu_id"] == 0
        assert d["passed"] is True
        assert d["snapshot_count"] == 5

    def test_report_to_json(self):
        report = FragmentationReport(
            gpu_id=0,
            duration_seconds=300.0,
            passed=True,
        )
        json_str = report.to_json()
        assert "gpu_id" in json_str
        assert "passed" in json_str


class TestGPUMemoryMonitor:
    def test_monitor_initialization(self):
        monitor = GPUMemoryMonitor(gpu_id=0)
        assert monitor._gpu_id == 0
        assert monitor._warning_threshold == 0.30
        assert monitor._critical_threshold == 0.40

    @skip_no_gpu
    def test_take_snapshot(self):
        monitor = GPUMemoryMonitor(gpu_id=0)
        snapshot = monitor.take_snapshot()
        assert snapshot is not None
        assert snapshot.total_memory_mb > 0
        assert snapshot.used_memory_mb >= 0
        assert snapshot.free_memory_mb >= 0
        assert 0.0 <= snapshot.fragmentation_rate <= 1.0

    @skip_no_gpu
    def test_is_gpu_available(self):
        monitor = GPUMemoryMonitor(gpu_id=0)
        assert monitor.is_gpu_available() is True

    def test_is_gpu_available_no_gpu(self):
        monitor = GPUMemoryMonitor(gpu_id=99)
        result = _query_nvidia_smi(99)
        if result is None:
            assert monitor.is_gpu_available() is False


@pytest.mark.phase0a
@pytest.mark.requires_llm
@skip_no_gpu
class TestGPUFragmentationQuick:
    @pytest.mark.asyncio
    async def test_quick_fragmentation_check(self):
        config = get_verification_config()
        thresholds = config.consistency_thresholds
        monitor = GPUMemoryMonitor(
            gpu_id=0,
            snapshot_interval_seconds=1.0,
            warning_threshold=thresholds.gpu_fragmentation_warning_threshold,
            critical_threshold=thresholds.gpu_fragmentation_critical_threshold,
        )
        report = await monitor.monitor_during_load_test(
            duration_seconds=5.0,
            request_interval_seconds=3600.0,
        )
        assert report.snapshot_count >= 1
        assert 0.0 <= report.final_fragmentation_rate <= 1.0
        print(f"\n[quick-fragmentation] Final rate: {report.final_fragmentation_rate:.2%}")


@pytest.mark.phase0a
@pytest.mark.requires_llm
@pytest.mark.slow
@skip_no_gpu
class TestGPUFragmentationFull:
    @pytest.mark.asyncio
    async def test_4hour_fragmentation_check(self):
        config = get_verification_config()
        thresholds = config.consistency_thresholds
        monitor = GPUMemoryMonitor(
            gpu_id=0,
            snapshot_interval_seconds=60.0,
            warning_threshold=thresholds.gpu_fragmentation_warning_threshold,
            critical_threshold=thresholds.gpu_fragmentation_critical_threshold,
        )
        report = await monitor.monitor_during_load_test(
            duration_seconds=thresholds.gpu_memory_monitor_duration_seconds,
            request_interval_seconds=360.0,
        )
        assert report.final_fragmentation_rate < thresholds.gpu_fragmentation_warning_threshold, (
            f"Gate FAILED: GPU fragmentation rate {report.final_fragmentation_rate:.2%} "
            f">= {thresholds.gpu_fragmentation_warning_threshold:.0%}"
        )
        print(f"\n[4h-fragmentation] Final: {report.final_fragmentation_rate:.2%}, "
              f"Peak: {report.peak_fragmentation_rate:.2%}")
