"""T-GA-EDG-02 边缘端导航推理服务（帧率分级 + 本地安全 + 埋点）单测。"""
import numpy as np
import pytest

from edge.gpu_thermal import GPUThermalPolicy
from edge.navigation import NavigationInferenceService
from edge.protocols import Frame, NavLabel
from edge.safety import LocalSafetyChecker

pytestmark = pytest.mark.track_edg


def _frame(index: int = 0, session_id: str = "sess_001") -> Frame:
    return Frame(
        frame_id=f"frame_{index}",
        session_id=session_id,
        index=index,
        pixels=np.zeros((4, 4), dtype=np.uint8),
        received_at_ms=0.0,
    )


class _FakeBackend:
    """可控的推理后端（记录调用次数，可按需返回违规标注）。"""

    name = "fake-trt"

    def __init__(self, labels=None, fail=False):
        self._labels = labels if labels is not None else [
            NavLabel(label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9)
        ]
        self._fail = fail
        self.loaded = False
        self.closed = False
        self.infer_calls = 0

    def load(self):
        self.loaded = True

    def infer(self, frame):
        self.infer_calls += 1
        if self._fail:
            raise RuntimeError("inference failed")
        return self._labels

    def close(self):
        self.closed = True


class TestLifecycle:
    def test_backend_lazily_loaded_on_first_inference(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend)

        assert backend.loaded is False
        service.process_frame(_frame(0), now=0.0)
        assert backend.loaded is True

    def test_close_releases_backend(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend)
        service.load()

        service.close()

        assert backend.closed is True

    def test_invalid_target_fps_rejected(self):
        with pytest.raises(ValueError):
            NavigationInferenceService(_FakeBackend(), target_fps=0)


class TestThermalThrottling:
    def test_normal_temperature_infers_first_frame(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend)

        result = service.process_frame(_frame(0), now=10.0)

        assert result is not None
        assert backend.infer_calls == 1

    def test_frame_rate_limit_skips_too_close_frames(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend, target_fps=15.0)

        first = service.process_frame(_frame(0), now=0.0)
        too_soon = service.process_frame(_frame(1), now=0.01)  # 15fps → 间隔 66.7ms
        later = service.process_frame(_frame(2), now=0.2)

        assert first is not None
        assert too_soon is None
        assert later is not None
        assert backend.infer_calls == 2
        assert service.stats()["frames_skipped"] == 1

    def test_frame_skip_level_infers_every_other_frame(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend)
        decision = service.record_temperature(82.0)  # 80-85°C → 隔帧

        assert decision.frame_skip == 1

        results = [service.process_frame(_frame(i), now=float(i)) for i in range(4)]

        assert [r is not None for r in results] == [True, False, True, False]
        assert backend.infer_calls == 2

    def test_nav_paused_above_85_celsius(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend)
        service.record_temperature(87.0)

        result = service.process_frame(_frame(0), now=0.0)

        assert result is None
        assert backend.infer_calls == 0
        assert service.stats()["nav_inference_allowed"] is False

    def test_emergency_pauses_everything_after_60s(self):
        backend = _FakeBackend()
        policy = GPUThermalPolicy(overheat_duration_seconds=60.0)
        service = NavigationInferenceService(backend, thermal_policy=policy)

        service.record_temperature(95.0, now=0.0)
        assert service.process_frame(_frame(0), now=0.5) is None

        service.record_temperature(95.0, now=60.0)
        assert service.decision.inference_paused is True
        assert service.target_fps == 0.0

    def test_recovery_restores_full_frame_rate(self):
        backend = _FakeBackend()
        service = NavigationInferenceService(backend)

        service.record_temperature(95.0, now=0.0)
        assert service.process_frame(_frame(0), now=0.1) is None

        service.record_temperature(60.0, now=1.0)
        assert service.process_frame(_frame(1), now=2.0) is not None


class TestLocalSafetyIntegration:
    def test_violating_labels_are_filtered_out(self):
        backend = _FakeBackend(
            labels=[
                NavLabel(label="organ_name", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9),
                NavLabel(label="grading_conclusion", x=0.1, y=0.1, width=0.2, height=0.2, confidence=0.9),
                NavLabel(label="boundary", x=5.0, y=0.1, width=0.2, height=0.2, confidence=0.9),
            ]
        )
        service = NavigationInferenceService(backend, safety_checker=LocalSafetyChecker())

        result = service.process_frame(_frame(0), now=0.0)

        assert [label.label for label in result.labels] == ["organ_name"]

    def test_result_carries_backend_and_latency(self):
        service = NavigationInferenceService(_FakeBackend())

        result = service.process_frame(_frame(7), now=0.0)

        assert result.frame_id == "frame_7"
        assert result.backend == "fake-trt"
        assert result.infer_latency_ms >= 0.0


class TestStats:
    def test_percentiles_none_before_any_inference(self):
        service = NavigationInferenceService(_FakeBackend())

        stats = service.stats()

        assert stats["p50_latency_ms"] is None
        assert stats["p99_latency_ms"] is None

    def test_percentiles_computed_after_inference(self):
        service = NavigationInferenceService(_FakeBackend())

        service.process_frame(_frame(0), now=0.0)

        stats = service.stats()
        assert stats["frames_in"] == 1
        assert stats["frames_inferred"] == 1
        assert stats["p99_latency_ms"] >= stats["p50_latency_ms"]

    def test_stats_exposes_thermal_level(self):
        service = NavigationInferenceService(_FakeBackend())
        service.record_temperature(82.0)

        assert service.stats()["thermal_level"] == "frame_skip"
