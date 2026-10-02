"""M3 边缘端尺寸换算单测。"""
import numpy as np
import pytest

from edge.measurement import (
    DEFAULT_PIXEL_SPACING_MM,
    ELLIPSOID_VOLUME_FACTOR,
    PIXEL_SPACING_SOURCE_DEFAULT,
    PIXEL_SPACING_SOURCE_DICOM,
    PixelSpacing,
    from_dicom_metadata,
    measure_labels,
    measurement_from_bbox,
)
from edge.protocols import Frame, NavLabel

pytestmark = pytest.mark.track_edg


class _Dataset:
    """pydicom Dataset 的最小替身（属性访问）。"""

    def __init__(self, **fields):
        for key, value in fields.items():
            setattr(self, key, value)


class _Element:
    def __init__(self, value):
        self.value = value


class TestPixelSpacing:
    def test_default_marks_estimated(self):
        spacing = PixelSpacing.default()
        assert spacing.source == PIXEL_SPACING_SOURCE_DEFAULT
        assert spacing.row_mm == DEFAULT_PIXEL_SPACING_MM

    def test_rejects_non_positive(self):
        with pytest.raises(ValueError):
            PixelSpacing(row_mm=0.0, column_mm=0.1)

    def test_from_dicom_object_with_raw_list(self):
        spacing = from_dicom_metadata(_Dataset(PixelSpacing=[0.2, 0.3]))
        assert (spacing.row_mm, spacing.column_mm) == (0.2, 0.3)
        assert spacing.source == PIXEL_SPACING_SOURCE_DICOM

    def test_from_dicom_data_element(self):
        spacing = from_dicom_metadata(_Dataset(PixelSpacing=_Element(["0.5", "0.5"])))
        assert spacing.row_mm == 0.5
        assert spacing.source == PIXEL_SPACING_SOURCE_DICOM

    def test_imager_pixel_spacing_fallback(self):
        spacing = from_dicom_metadata(_Dataset(ImagerPixelSpacing=[0.4, 0.4]))
        assert spacing.column_mm == 0.4
        assert spacing.source == PIXEL_SPACING_SOURCE_DICOM

    def test_pixel_spacing_preferred_over_imager(self):
        spacing = from_dicom_metadata(
            _Dataset(PixelSpacing=[0.2, 0.2], ImagerPixelSpacing=[0.9, 0.9])
        )
        assert spacing.row_mm == 0.2

    @pytest.mark.parametrize(
        "metadata",
        [
            None,
            {},
            _Dataset(),
            _Dataset(PixelSpacing=[0.0, 0.1]),
            _Dataset(PixelSpacing=["bad", "worse"]),
            _Dataset(PixelSpacing=[0.2]),
        ],
    )
    def test_falls_back_with_default_source(self, metadata):
        assert from_dicom_metadata(metadata).source == PIXEL_SPACING_SOURCE_DEFAULT

    def test_mapping_input(self):
        spacing = from_dicom_metadata({"PixelSpacing": [0.25, 0.25]})
        assert spacing.source == PIXEL_SPACING_SOURCE_DICOM


class TestMeasurementFromBbox:
    FRAME = (500, 400)  # (height, width) 像素

    def test_square_bbox_with_dicom_spacing(self):
        # bbox 宽 0.25 → 100px × 0.2mm = 20mm；高 0.1 → 50px × 0.2mm = 10mm
        result = measurement_from_bbox(
            [0.0, 0.0, 0.25, 0.1], self.FRAME, PixelSpacing(0.2, 0.2)
        )
        assert result["long_mm"] == 20.0
        assert result["short_mm"] == 10.0
        assert result["aspect_ratio"] == 2.0
        assert result["volume_mm3"] == pytest.approx(
            round(ELLIPSOID_VOLUME_FACTOR * 20.0 * 10.0 * 10.0, 3)
        )
        assert result["pixel_spacing_source"] == PIXEL_SPACING_SOURCE_DICOM
        assert result["unit"] == "mm"

    def test_asymmetric_spacing_uses_row_and_column(self):
        # 列间距 0.5 → 宽 200px × 0.5 = 100mm；行间距 0.1 → 高 50px × 0.1 = 5mm
        result = measurement_from_bbox(
            [0.0, 0.0, 0.5, 0.1], self.FRAME, PixelSpacing(row_mm=0.1, column_mm=0.5)
        )
        assert result["long_mm"] == 100.0
        assert result["short_mm"] == 5.0

    def test_missing_spacing_marks_default(self):
        result = measurement_from_bbox([0.0, 0.0, 0.2, 0.2], self.FRAME)
        assert result["pixel_spacing_source"] == PIXEL_SPACING_SOURCE_DEFAULT

    def test_distances_are_not_guessed(self):
        """仅有标注框推不出解剖距离，必须留空而不是猜。"""
        result = measurement_from_bbox([0.0, 0.0, 0.2, 0.2], self.FRAME)
        assert result["distance_to_capsule_mm"] is None
        assert result["distance_to_organ_edge_mm"] is None
        assert result["distance_to_skin_mm"] is None
        assert result["anteroposterior_mm"] is None

    def test_plane_type_recorded(self):
        result = measurement_from_bbox(
            [0.0, 0.0, 0.2, 0.2], self.FRAME, plane_type="longitudinal"
        )
        assert result["measurement_type"] == "二维longitudinal"

    @pytest.mark.parametrize(
        "bbox,frame",
        [
            (None, (10, 10)),
            ([], (10, 10)),
            ([0.0, 0.0, 0.2], (10, 10)),
            ([0.0, 0.0, 0.0, 0.2], (10, 10)),
            ([0.0, 0.0, -0.1, 0.2], (10, 10)),
            ([0.0, 0.0, 0.2, 0.2], None),
            ([0.0, 0.0, 0.2, 0.2], (0, 10)),
            (["a", 0.0, 0.2, 0.2], (10, 10)),
        ],
    )
    def test_invalid_input_returns_none(self, bbox, frame):
        """产出错误尺寸比不产出更危险，故不合法输入一律返回 None。"""
        assert measurement_from_bbox(bbox, frame) is None


class TestMeasureLabels:
    FRAME = (500, 400)

    def _label(self, name="nodule", w=0.25, h=0.1):
        return NavLabel(
            label=name, x=0.0, y=0.0, width=w, height=h, confidence=0.9
        )

    def test_measures_each_label(self):
        # 帧为 (height=500, width=400)：高度方向 0.05*500px*0.2mm=5mm 为长径
        results = measure_labels(
            [self._label("nodule"), self._label("calcification", w=0.05, h=0.05)],
            self.FRAME,
            PixelSpacing(0.2, 0.2),
        )
        assert results["nodule"]["long_mm"] == 20.0
        assert results["calcification"]["long_mm"] == 5.0

    def test_keeps_largest_across_frames(self):
        """导航流逐帧上报同一病灶，取最大值避免尺寸随帧抖动。"""
        results = measure_labels(
            [self._label("nodule", w=0.1, h=0.1), self._label("nodule", w=0.3, h=0.3)],
            self.FRAME,
            PixelSpacing(0.2, 0.2),
        )
        assert results["nodule"]["long_mm"] == 30.0

    def test_skips_invalid_labels(self):
        bad = NavLabel(label="bad", x=0.0, y=0.0, width=0.0, height=0.1, confidence=0.9)
        assert measure_labels([bad], self.FRAME) == {}

    def test_empty_input(self):
        assert measure_labels([], self.FRAME) == {}


class TestFrameCarriesPixelSpacing:
    def test_frame_default_is_none(self):
        frame = Frame(
            frame_id="f1",
            session_id="s1",
            index=0,
            pixels=np.zeros((4, 4), dtype=np.uint8),
            received_at_ms=0.0,
        )
        assert frame.pixel_spacing_mm is None

    def test_frame_accepts_spacing(self):
        frame = Frame(
            frame_id="f1",
            session_id="s1",
            index=0,
            pixels=np.zeros((4, 4), dtype=np.uint8),
            received_at_ms=0.0,
            pixel_spacing_mm=(0.2, 0.3),
        )
        assert frame.pixel_spacing_mm == (0.2, 0.3)


class TestDicomScpExtraction:
    def test_returns_none_when_absent(self):
        from edge.dicom_scp import DicomSCPService

        assert DicomSCPService._extract_pixel_spacing_mm(_Dataset()) is None

    def test_returns_tuple_when_present(self):
        from edge.dicom_scp import DicomSCPService

        assert DicomSCPService._extract_pixel_spacing_mm(
            _Dataset(PixelSpacing=[0.2, 0.3])
        ) == (0.2, 0.3)
