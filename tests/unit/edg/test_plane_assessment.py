"""M1 切面标准性评估适配器单测（边缘端）。"""
import numpy as np
import pytest

from edge.plane_assessment import (
    PLANE_LONGITUDINAL,
    PLANE_NON_STANDARD,
    PLANE_TRANSVERSE,
    LocalFakePlaneAssessmentAdapter,
    NullPlaneAssessmentAdapter,
    PlaneAssessmentAdapter,
    create_plane_assessment_adapter,
    get_plane_assessment_adapter,
    plane_assessment_to_dict,
    set_plane_assessment_adapter,
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


def _noisy(shape=(200, 150), seed: int = 0) -> np.ndarray:
    rng = np.random.RandomState(seed)
    return (rng.rand(*shape) * 200 + 30).astype(np.uint8)


@pytest.fixture(autouse=True)
def _clear_injected_adapter():
    set_plane_assessment_adapter(None)
    yield
    set_plane_assessment_adapter(None)


class TestNullAdapter:
    def test_returns_none(self):
        adapter = NullPlaneAssessmentAdapter()
        assert adapter.assess(_frame(_noisy())) is None
        adapter.load()
        adapter.close()

    def test_satisfies_protocol(self):
        assert isinstance(NullPlaneAssessmentAdapter(), PlaneAssessmentAdapter)


class TestFactory:
    @pytest.mark.parametrize("name", ["", "null", "none", "disabled", "NULL"])
    def test_null_names(self, name):
        assert isinstance(create_plane_assessment_adapter(name), NullPlaneAssessmentAdapter)

    @pytest.mark.parametrize("name", ["local_fake", "fake", "LOCAL_FAKE"])
    def test_fake_names(self, name):
        assert isinstance(
            create_plane_assessment_adapter(name), LocalFakePlaneAssessmentAdapter
        )

    def test_unknown_name_fails_loudly(self):
        """未知名字必须显式报错——静默降级为假实现会掩盖现场未接真机的事实。"""
        with pytest.raises(ValueError, match="未支持的切面评估适配器配置"):
            create_plane_assessment_adapter("tensorrt")

    def test_injected_adapter_takes_precedence(self):
        class _Injected:
            name = "tensorrt"

            def load(self):
                pass

            def assess(self, frame, organ_code=None):
                return None

            def close(self):
                pass

        set_plane_assessment_adapter(_Injected())
        adapter = create_plane_assessment_adapter("tensorrt")
        assert adapter.name == "tensorrt"
        assert get_plane_assessment_adapter() is adapter


class TestLocalFakeAdapter:
    def test_deterministic(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        frame = _frame(_noisy())
        first = adapter.assess(frame)
        second = adapter.assess(frame)
        assert first == second

    def test_plane_score_within_contract_range(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        for seed in range(5):
            for shape in [(200, 150), (150, 200), (64, 64)]:
                result = adapter.assess(_frame(_noisy(shape, seed)))
                assert 0.0 <= result.plane_score <= 1.0

    def test_tall_frame_is_longitudinal(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        result = adapter.assess(_frame(_noisy((200, 120))), organ_code="thyroid")
        assert result.plane_type == PLANE_LONGITUDINAL
        assert result.organ_code == "thyroid"

    def test_wide_frame_is_transverse(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        result = adapter.assess(_frame(_noisy((120, 200))))
        assert result.plane_type == PLANE_TRANSVERSE

    def test_dark_frame_is_non_standard_and_flagged(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        dark = np.full((100, 100), 5, dtype=np.uint8)
        result = adapter.assess(_frame(dark))
        assert result.plane_type == PLANE_NON_STANDARD
        assert result.is_standard is False
        assert "image_too_dark" in result.issues

    def test_bright_frame_is_non_standard(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        bright = np.full((100, 100), 250, dtype=np.uint8)
        result = adapter.assess(_frame(bright))
        assert result.plane_type == PLANE_NON_STANDARD
        assert "image_too_bright" in result.issues

    def test_flat_frame_marked_low_contrast(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        flat = np.full((100, 100), 128, dtype=np.uint8)
        result = adapter.assess(_frame(flat))
        assert "low_contrast" in result.issues
        assert result.is_standard is False

    def test_good_frame_is_standard(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        result = adapter.assess(_frame(_noisy()))
        assert result.is_standard is True
        assert "plane_below_standard" not in result.issues

    def test_visible_structures_shrink_when_not_standard(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        standard = adapter.assess(_frame(_noisy()), organ_code="thyroid")
        flat = np.full((100, 100), 128, dtype=np.uint8)
        non_standard = adapter.assess(_frame(flat), organ_code="thyroid")
        assert len(standard.required_structures_visible) == 3
        assert len(non_standard.required_structures_visible) == 2

    def test_unknown_organ_has_no_structure_catalog(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        result = adapter.assess(_frame(_noisy()), organ_code="unknown_organ")
        assert result.required_structures_visible == []

    def test_invalid_frame_shape_returns_none(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        rgb = np.zeros((10, 10, 3), dtype=np.uint8)
        assert adapter.assess(_frame(rgb)) is None

    def test_invalid_threshold_rejected(self):
        with pytest.raises(ValueError, match="standard_threshold"):
            LocalFakePlaneAssessmentAdapter(standard_threshold=1.5)


class TestSerialization:
    def test_payload_keys_match_backend_contract(self):
        adapter = LocalFakePlaneAssessmentAdapter()
        payload = plane_assessment_to_dict(adapter.assess(_frame(_noisy()), organ_code="liver"))
        assert set(payload.keys()) == {
            "plane_type",
            "is_standard",
            "plane_score",
            "organ_code",
            "issues",
            "required_structures_visible",
        }
        assert isinstance(payload["issues"], list)
        assert isinstance(payload["required_structures_visible"], list)
