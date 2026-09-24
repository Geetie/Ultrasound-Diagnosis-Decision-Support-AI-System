"""M6 图像质量评估适配器单测（边缘端）。"""
import numpy as np
import pytest

from edge.image_quality import (
    IMPACT_MAJOR,
    IMPACT_MINOR,
    IMPACT_NONE,
    ImageQualityAdapter,
    LocalFakeImageQualityAdapter,
    NullImageQualityAdapter,
    create_image_quality_adapter,
    get_image_quality_adapter,
    image_quality_to_dict,
    set_image_quality_adapter,
)
from edge.protocols import Frame

pytestmark = pytest.mark.track_edg


def _frame(pixels: np.ndarray, frame_id: str = "f1") -> Frame:
    return Frame(
        frame_id=frame_id,
        session_id="sess_001",
        index=0,
        pixels=pixels,
        received_at_ms=0.0,
    )


def _rich(shape=(200, 200), seed: int = 1) -> np.ndarray:
    """高对比、曝光合理的合成帧（局部块状纹理，非逐像素噪声）。"""
    rng = np.random.RandomState(seed)
    base = np.zeros(shape, dtype=np.float32)
    block = 8
    for row in range(0, shape[0], block):
        for col in range(0, shape[1], block):
            base[row: row + block, col: col + block] = rng.randint(40, 220)
    return base.astype(np.uint8)


@pytest.fixture(autouse=True)
def _clear_injected_adapter():
    set_image_quality_adapter(None)
    yield
    set_image_quality_adapter(None)


class TestNullAdapter:
    def test_returns_none(self):
        assert NullImageQualityAdapter().assess(_frame(_rich())) is None

    def test_satisfies_protocol(self):
        assert isinstance(NullImageQualityAdapter(), ImageQualityAdapter)


class TestFactory:
    @pytest.mark.parametrize("name", ["", "null", "none", "disabled"])
    def test_null_names(self, name):
        assert isinstance(create_image_quality_adapter(name), NullImageQualityAdapter)

    @pytest.mark.parametrize("name", ["local_fake", "fake"])
    def test_fake_names(self, name):
        assert isinstance(
            create_image_quality_adapter(name), LocalFakeImageQualityAdapter
        )

    def test_unknown_name_fails_loudly(self):
        with pytest.raises(ValueError, match="未支持的图像质量适配器配置"):
            create_image_quality_adapter("onnx")

    def test_injected_adapter_takes_precedence(self):
        class _Injected:
            name = "onnx"

            def load(self):
                pass

            def assess(self, frame):
                return None

            def close(self):
                pass

        set_image_quality_adapter(_Injected())
        assert create_image_quality_adapter("onnx").name == "onnx"
        assert get_image_quality_adapter() is not None


class TestLocalFakeAdapter:
    def test_deterministic(self):
        adapter = LocalFakeImageQualityAdapter()
        frame = _frame(_rich())
        assert adapter.assess(frame) == adapter.assess(frame)

    def test_all_scores_within_contract_range(self):
        adapter = LocalFakeImageQualityAdapter()
        for image in (_rich(), np.zeros((50, 50), np.uint8), np.full((50, 50), 255, np.uint8)):
            result = adapter.assess(_frame(image))
            for value in (
                result.overall_score,
                result.blur_score,
                result.artifact_score,
                result.gain_score,
            ):
                assert 0.0 <= value <= 1.0

    def test_good_frame_has_no_hint(self):
        adapter = LocalFakeImageQualityAdapter()
        result = adapter.assess(_frame(_rich()))
        assert result.overall_score >= 0.7
        assert result.diagnostic_impact == IMPACT_NONE
        assert result.issues == []

    def test_dark_frame_flagged(self):
        adapter = LocalFakeImageQualityAdapter()
        result = adapter.assess(_frame(np.full((80, 80), 10, np.uint8)))
        assert "image_too_dark" in result.issues
        assert result.diagnostic_impact == IMPACT_MAJOR

    def test_bright_frame_flagged(self):
        adapter = LocalFakeImageQualityAdapter()
        result = adapter.assess(_frame(np.full((80, 80), 250, np.uint8)))
        assert "image_too_bright" in result.issues

    def test_flat_frame_is_blurred_and_low_contrast(self):
        adapter = LocalFakeImageQualityAdapter()
        result = adapter.assess(_frame(np.full((80, 80), 128, np.uint8)))
        assert "severe_blur" in result.issues
        assert "low_contrast" in result.issues
        assert result.blur_score == 0.0
        assert result.diagnostic_impact == IMPACT_MAJOR

    def test_saturation_clipping_detected(self):
        adapter = LocalFakeImageQualityAdapter()
        image = np.full((100, 100), 128, np.uint8)
        image[:20, :] = 0  # 20% 像素被裁剪到黑
        result = adapter.assess(_frame(image))
        assert "saturation_clipping" in result.issues

    def test_impact_grading_monotonic(self):
        """质量越差，影响程度不应更轻。"""
        adapter = LocalFakeImageQualityAdapter()
        good = adapter.assess(_frame(_rich()))
        flat = adapter.assess(_frame(np.full((80, 80), 128, np.uint8)))
        order = {IMPACT_NONE: 0, IMPACT_MINOR: 1, IMPACT_MAJOR: 2}
        assert order[flat.diagnostic_impact] >= order[good.diagnostic_impact]

    def test_invalid_frame_shape_returns_none(self):
        adapter = LocalFakeImageQualityAdapter()
        assert adapter.assess(_frame(np.zeros((5, 5, 3), np.uint8))) is None

    def test_single_pixel_frame_does_not_crash(self):
        adapter = LocalFakeImageQualityAdapter()
        result = adapter.assess(_frame(np.array([[128]], dtype=np.uint8)))
        assert 0.0 <= result.overall_score <= 1.0


class TestSerialization:
    def test_payload_keys_match_backend_contract(self):
        adapter = LocalFakeImageQualityAdapter()
        payload = image_quality_to_dict(adapter.assess(_frame(_rich())))
        assert set(payload.keys()) == {
            "overall_score",
            "blur_score",
            "artifact_score",
            "gain_score",
            "issues",
            "diagnostic_impact",
        }
        assert isinstance(payload["issues"], list)
