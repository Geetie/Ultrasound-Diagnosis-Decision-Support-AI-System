"""M3 文本自动测量单测。"""
import pytest

from app.knowledge.measurement_extract import (
    ELLIPSOID_VOLUME_FACTOR,
    PIXEL_SPACING_SOURCE_TEXT,
    measurement_features,
    parse_dimensions,
    parse_distances,
    structured_measurement_from_text,
)
from app.knowledge.risk_rules import FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE

pytestmark = [pytest.mark.track_rag, pytest.mark.track_data]


class TestParseDimensions:
    @pytest.mark.parametrize(
        "text,expected",
        [
            ("大小 1.2x0.8 cm", (12.0, 8.0, None)),
            ("大小 1.2×0.8×0.9cm", (12.0, 9.0, 8.0)),
            ("大小 12*8mm", (12.0, 8.0, None)),
            ("大小1.2乘0.8cm", (12.0, 8.0, None)),
        ],
    )
    def test_unit_normalised_to_mm(self, text, expected):
        """单位必须换算：把 "12*8mm" 当成 cm 会造成 10 倍量级错误。"""
        assert parse_dimensions(text) == expected

    def test_defaults_to_cm_when_unit_missing(self):
        assert parse_dimensions("大小 1.2x0.8") == (12.0, 8.0, None)

    def test_sorted_descending(self):
        assert parse_dimensions("大小 0.8x1.2cm") == (12.0, 8.0, None)

    @pytest.mark.parametrize("text", ["未见明显异常", "直径 8mm", "", None, "   "])
    def test_no_dimension_text(self, text):
        assert parse_dimensions(text) is None


class TestParseDistances:
    def test_capsule_distance(self):
        assert parse_distances("距包膜 0.3cm") == {"distance_to_capsule_mm": 3.0}

    def test_organ_edge_distance(self):
        assert parse_distances("距脏器边缘约 3mm") == {"distance_to_organ_edge_mm": 3.0}

    def test_skin_distance(self):
        assert parse_distances("距皮肤1.5 cm") == {"distance_to_skin_mm": 15.0}

    def test_multiple_distances(self):
        found = parse_distances("距包膜 0.3cm，距皮肤 1.5cm")
        assert found == {"distance_to_capsule_mm": 3.0, "distance_to_skin_mm": 15.0}

    def test_no_distance(self):
        assert parse_distances("边界不清") == {}
        assert parse_distances(None) == {}


class TestStructuredMeasurement:
    def test_two_dimensional_measurement(self):
        measurement = structured_measurement_from_text("甲状腺结节 1.2x0.8cm，边界不清")
        assert measurement is not None
        assert measurement.long_mm == 12.0
        assert measurement.short_mm == 8.0
        assert measurement.anteroposterior_mm is None
        assert measurement.aspect_ratio == 1.5
        assert measurement.volume_mm3 == pytest.approx(
            round(ELLIPSOID_VOLUME_FACTOR * 12.0 * 8.0 * 8.0, 3)
        )
        assert measurement.measurement_type == "二维"
        assert measurement.pixel_spacing_source == PIXEL_SPACING_SOURCE_TEXT
        assert measurement.unit == "mm"

    def test_three_dimensional_measurement(self):
        measurement = structured_measurement_from_text("结节 1.2×0.8×0.9cm")
        assert measurement.anteroposterior_mm == 8.0
        assert measurement.measurement_type == "三维"
        assert measurement.volume_mm3 == pytest.approx(
            round(ELLIPSOID_VOLUME_FACTOR * 12.0 * 9.0 * 8.0, 3)
        )

    def test_distances_only(self):
        measurement = structured_measurement_from_text("结节距包膜 0.3cm")
        assert measurement is not None
        assert measurement.distance_to_capsule_mm == 3.0
        assert measurement.long_mm is None

    def test_returns_none_when_nothing_measurable(self):
        """无尺寸也无距离时不得返回空 Measurement（否则前端渲染全空表格）。"""
        assert structured_measurement_from_text("未见明显异常") is None
        assert structured_measurement_from_text(None) is None

    def test_organ_hint_accepted(self):
        measurement = structured_measurement_from_text("1.2x0.8cm", organ="liver")
        assert measurement is not None


class TestMeasurementFeatures:
    def test_taller_than_wide_emits_risk_feature(self):
        measurement = structured_measurement_from_text("1.2x0.8cm")
        assert measurement_features(measurement) == [FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE]

    def test_round_lesion_emits_nothing(self):
        measurement = structured_measurement_from_text("0.8x0.8cm")
        assert measurement_features(measurement) == []

    def test_none_measurement(self):
        assert measurement_features(None) == []
