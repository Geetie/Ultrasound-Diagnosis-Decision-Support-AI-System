"""T-GB-EDG-03 边缘端本地安全检查（标签白名单 + 坐标范围）单测。"""
import pytest

from edge.protocols import NavLabel
from edge.safety import DEFAULT_ALLOWED_LABELS, LocalSafetyChecker

pytestmark = pytest.mark.track_edg


def _label(name="organ_name", x=0.1, y=0.2, w=0.3, h=0.4, confidence=0.9):
    return NavLabel(label=name, x=x, y=y, width=w, height=h, confidence=confidence)


class TestFieldWhitelist:
    def test_whitelisted_fields_kept_others_dropped(self):
        checker = LocalSafetyChecker()
        payload = {
            "organ_name": "thyroid",
            "echogenicity": "isoechoic",
            "grading_conclusion": "TI-RADS 4A",  # 分级结论不得上行
            "patient_name": "张三",
        }

        filtered = checker.filter_fields(payload)

        assert filtered == {"organ_name": "thyroid", "echogenicity": "isoechoic"}
        assert "grading_conclusion" not in filtered
        assert "patient_name" not in filtered

    def test_check_reports_filtered_fields_but_still_passes(self):
        checker = LocalSafetyChecker()
        result = checker.check({"organ_name": "liver", "secret_field": 1})

        # 字段被过滤是预期行为，不算失败
        assert result.passed is True
        assert any(v.startswith("fields_filtered:") for v in result.violations)
        assert result.filtered == {"organ_name": "liver"}

    def test_default_whitelist_matches_fusion_whitelist(self):
        # 与 app/fusion 的白名单同口径（不含分级结论类术语）
        assert "grading" not in DEFAULT_ALLOWED_LABELS
        assert "diagnosis" not in DEFAULT_ALLOWED_LABELS
        assert "organ_name" in DEFAULT_ALLOWED_LABELS

    def test_custom_whitelist(self):
        checker = LocalSafetyChecker(allowed_labels=["only_this"])

        result = checker.check({"only_this": 1, "other": 2})

        assert result.filtered == {"only_this": 1}


class TestCoordinateValidation:
    def test_valid_normalized_box_passes(self):
        result = LocalSafetyChecker().check({}, [_label()])

        assert result.passed is True
        assert len(result.filtered["labels"]) == 1
        assert result.rejected_labels == []

    @pytest.mark.parametrize(
        "kwargs,expected_fragment",
        [
            ({"x": -0.1}, "coordinate_out_of_range:x"),
            ({"y": 1.2}, "coordinate_out_of_range:y"),
            ({"x": 0.9, "w": 0.2}, "coordinate_out_of_range:x2"),
            ({"y": 0.9, "h": 0.2}, "coordinate_out_of_range:y2"),
            ({"w": 0.0}, "non_positive_box_size"),
            ({"h": -0.1}, "non_positive_box_size"),
        ],
    )
    def test_out_of_range_box_rejected(self, kwargs, expected_fragment):
        result = LocalSafetyChecker().check({}, [_label(**kwargs)])

        assert result.passed is False
        assert any(expected_fragment in v for v in result.violations)
        assert len(result.rejected_labels) == 1

    def test_non_whitelisted_label_rejected(self):
        result = LocalSafetyChecker().check({}, [_label(name="grading_conclusion")])

        assert result.passed is False
        assert any(v.startswith("label_not_whitelisted:") for v in result.violations)

    def test_confidence_floor(self):
        checker = LocalSafetyChecker(min_confidence=0.5)

        result = checker.check({}, [_label(confidence=0.3)])

        assert result.passed is False
        assert any(v.startswith("confidence_below_min:") for v in result.violations)

    def test_mixed_batch_partitions_labels(self):
        checker = LocalSafetyChecker()
        good = _label()
        bad = _label(x=2.0)

        accepted, violations, rejected = checker.filter_labels([good, bad])

        assert accepted == [good]
        assert rejected == [bad]
        assert violations

    def test_invalid_range_rejected_at_construction(self):
        with pytest.raises(ValueError):
            LocalSafetyChecker(coord_range=(1.0, 0.0))


class TestResultSerialization:
    def test_as_dict_is_json_friendly(self):
        result = LocalSafetyChecker().check({"unknown": 1}, [_label(x=5.0)])

        payload = result.as_dict()

        assert payload["passed"] is False
        assert payload["rejected_label_count"] == 1
        # 行外字段被过滤掉，不会出现在 filtered_keys 中
        assert "unknown" not in payload["filtered_keys"]
        assert isinstance(payload["violations"], list)
