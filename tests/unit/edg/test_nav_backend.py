"""T-GA-EDG-02 导航推理后端（letterbox/NMS/坐标转换 + TensorRT 适配）单测。"""
import numpy as np
import pytest

from edge.nav_backend import (
    NullNavBackend,
    TensorRTNavBackend,
    create_nav_backend,
    nms,
    postprocess_detections,
    preprocess_frame,
)
from edge.protocols import Frame

pytestmark = pytest.mark.track_edg

LABELS = ["organ_name", "boundary", "echogenicity"]


def _frame(height=100, width=200, index=0) -> Frame:
    return Frame(
        frame_id=f"frame_{index}",
        session_id="sess_001",
        index=index,
        pixels=np.full((height, width), 128, dtype=np.uint8),
        received_at_ms=0.0,
    )


class TestPreprocess:
    def test_output_tensor_shape_and_channels(self):
        tensor, _, _, _ = preprocess_frame(_frame(100, 200), (512, 512))

        assert tensor.shape == (1, 3, 512, 512)
        assert tensor.dtype == np.float32

    def test_letterbox_scale_and_padding(self):
        # 200x100 → 512 宽：scale=2.56，resized_h=256，上下各补 128
        _, scale, pad_x, pad_y = preprocess_frame(_frame(100, 200), (512, 512))

        assert scale == pytest.approx(2.56)
        assert pad_x == pytest.approx(0.0)
        assert pad_y == pytest.approx(128.0)

    def test_normalization_to_unit_range(self):
        frame = _frame(10, 10)
        frame = Frame(
            frame_id=frame.frame_id,
            session_id=frame.session_id,
            index=frame.index,
            pixels=np.full((10, 10), 255, dtype=np.uint8),
            received_at_ms=0.0,
        )

        tensor, _, _, _ = preprocess_frame(frame, (20, 20))

        assert tensor.max() == pytest.approx(1.0)

    def test_non_2d_frame_rejected(self):
        frame = Frame(
            frame_id="f",
            session_id="s",
            index=0,
            pixels=np.zeros((4, 4, 3), dtype=np.uint8),
            received_at_ms=0.0,
        )

        with pytest.raises(ValueError):
            preprocess_frame(frame)


class TestNMS:
    def test_overlapping_same_class_suppressed(self):
        detections = np.array(
            [
                [0, 0, 10, 10, 0.9, 0],
                [1, 1, 11, 11, 0.8, 0],  # 与上者高度重叠
            ],
            dtype=np.float32,
        )

        keep = nms(detections, iou_threshold=0.5)

        assert keep == [0]

    def test_different_classes_kept(self):
        detections = np.array(
            [
                [0, 0, 10, 10, 0.9, 0],
                [0, 0, 10, 10, 0.8, 1],
            ],
            dtype=np.float32,
        )

        keep = nms(detections, iou_threshold=0.5)

        assert sorted(keep) == [0, 1]

    def test_disjoint_boxes_kept(self):
        detections = np.array(
            [
                [0, 0, 10, 10, 0.9, 0],
                [50, 50, 60, 60, 0.7, 0],
            ],
            dtype=np.float32,
        )

        assert sorted(nms(detections, iou_threshold=0.5)) == [0, 1]

    def test_empty_input(self):
        assert nms(np.zeros((0, 6), dtype=np.float32)) == []


class TestPostprocess:
    def test_letterbox_round_trip_covers_full_frame(self):
        # 输入画布上的完整有效区域 → 应还原为整幅原图 (0,0,1,1)
        detections = np.array([[0.0, 128.0, 512.0, 384.0, 0.95, 0]], dtype=np.float32)

        labels = postprocess_detections(
            detections,
            frame_shape=(100, 200),
            scale=2.56,
            pad_x=0.0,
            pad_y=128.0,
            labels=LABELS,
        )

        assert len(labels) == 1
        assert labels[0].x == pytest.approx(0.0)
        assert labels[0].y == pytest.approx(0.0)
        assert labels[0].width == pytest.approx(1.0)
        assert labels[0].height == pytest.approx(1.0)
        assert labels[0].label == "organ_name"
        assert labels[0].confidence == pytest.approx(0.95)

    def test_score_threshold_filters(self):
        detections = np.array([[0, 0, 10, 10, 0.2, 0]], dtype=np.float32)

        labels = postprocess_detections(
            detections, frame_shape=(100, 100), score_threshold=0.5
        )

        assert labels == []

    def test_unknown_class_falls_back_to_class_name(self):
        detections = np.array([[0, 0, 10, 10, 0.9, 7]], dtype=np.float32)

        labels = postprocess_detections(
            detections, frame_shape=(100, 100), labels=LABELS
        )

        assert labels[0].label == "class_7"

    def test_coordinates_clamped_to_frame(self):
        detections = np.array([[-50, -50, 500, 500, 0.9, 0]], dtype=np.float32)

        labels = postprocess_detections(
            detections, frame_shape=(100, 200), scale=2.56, pad_x=0.0, pad_y=128.0
        )

        assert labels[0].x >= 0.0
        assert labels[0].y >= 0.0
        assert labels[0].x + labels[0].width <= 1.0 + 1e-6
        assert labels[0].y + labels[0].height <= 1.0 + 1e-6

    def test_empty_detections(self):
        assert postprocess_detections(
            np.zeros((0, 6), dtype=np.float32), frame_shape=(10, 10)
        ) == []

    def test_invalid_shape_rejected(self):
        with pytest.raises(ValueError):
            postprocess_detections(
                np.zeros((3, 5), dtype=np.float32), frame_shape=(10, 10)
            )

    def test_zero_frame_shape_rejected(self):
        with pytest.raises(ValueError):
            postprocess_detections(
                np.array([[0, 0, 1, 1, 0.9, 0]], dtype=np.float32),
                frame_shape=(0, 10),
            )


class _FakeRunner:
    name = "fake-trt"

    def __init__(self, detections):
        self._detections = detections
        self.loaded = 0
        self.closed = 0
        self.last_input = None

    @property
    def input_shape(self):
        return (1, 3, 512, 512)

    def load(self):
        self.loaded += 1

    def run(self, input_tensor):
        self.last_input = input_tensor
        return self._detections

    def close(self):
        self.closed += 1


class TestTensorRTBackend:
    def test_load_is_idempotent_and_lazy(self):
        runner = _FakeRunner(np.zeros((0, 6), dtype=np.float32))
        backend = TensorRTNavBackend(engine_path="nav.plan", runner=runner)

        assert runner.loaded == 0
        backend.load()
        backend.load()

        assert runner.loaded == 1

    def test_infer_returns_postprocessed_labels(self):
        detections = np.array([[0.0, 128.0, 512.0, 384.0, 0.95, 1]], dtype=np.float32)
        runner = _FakeRunner(detections)
        backend = TensorRTNavBackend(
            engine_path="nav.plan", runner=runner, labels=LABELS
        )

        labels = backend.infer(_frame(100, 200))

        assert len(labels) == 1
        assert labels[0].label == "boundary"
        assert runner.last_input.shape == (1, 3, 512, 512)

    def test_close_releases_runner(self):
        runner = _FakeRunner(np.zeros((0, 6), dtype=np.float32))
        backend = TensorRTNavBackend(engine_path="nav.plan", runner=runner)
        backend.load()

        backend.close()

        assert runner.closed == 1


class TestFactory:
    def test_missing_engine_falls_back_to_null_backend(self, tmp_path):
        backend = create_nav_backend(str(tmp_path / "absent.plan"))

        assert isinstance(backend, NullNavBackend)
        assert backend.infer(_frame()) == []

    def test_existing_engine_creates_tensorrt_backend(self, tmp_path):
        engine = tmp_path / "nav.plan"
        engine.write_bytes(b"fake")

        backend = create_nav_backend(str(engine), labels=LABELS)

        assert isinstance(backend, TensorRTNavBackend)

    def test_none_engine_returns_null_backend(self):
        assert isinstance(create_nav_backend(None), NullNavBackend)

    def test_null_backend_load_and_close_are_safe(self):
        backend = NullNavBackend()

        backend.load()
        backend.close()

        assert backend.infer(_frame()) == []
