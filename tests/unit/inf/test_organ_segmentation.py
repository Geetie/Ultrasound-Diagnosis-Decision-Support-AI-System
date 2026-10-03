"""多器官分割注册表单测（不加载权重）。"""
from __future__ import annotations

import pytest

from app.inference.nodule_segmentation import SegmentationUnavailableError
from app.inference.organ_segmentation import (
    REGISTRY,
    get_spec,
    list_specs,
    resolve,
)

pytestmark = [pytest.mark.track_inf]


class TestRegistry:
    def test_three_organs_registered(self):
        assert set(REGISTRY) == {"thyroid", "cardiac", "liver"}

    def test_thyroid_is_trained_with_official_metrics(self):
        s = get_spec("thyroid")
        assert s.available is True
        assert s.status == "trained"
        assert s.metrics["dice"] == 0.7961
        assert s.metrics["iou"] == 0.7004
        assert "614" in s.metrics["eval"]

    def test_cardiac_untrained_but_data_available(self):
        s = get_spec("cardiac")
        assert s.available is False
        assert s.status == "untrained"
        assert s.dataset and "CAMUS" in s.dataset
        assert s.unavailable_reason() and "尚未训练" in s.unavailable_reason()

    def test_liver_has_no_public_data(self):
        s = get_spec("liver")
        assert s.status == "data_unavailable"
        assert s.weight_path is None
        assert s.unavailable_reason() and "无公开" in s.unavailable_reason()

    def test_case_insensitive_lookup(self):
        assert get_spec("Thyroid").organ == "thyroid"
        assert get_spec(" CARDIAC ").organ == "cardiac"

    def test_unknown_organ_raises_with_registered_list(self):
        with pytest.raises(KeyError) as ei:
            get_spec("pancreas")
        assert "thyroid" in str(ei.value)

    def test_resolve_returns_trained_spec(self):
        assert resolve("thyroid").available is True

    def test_resolve_raises_for_untrained(self):
        with pytest.raises(SegmentationUnavailableError, match="尚未训练"):
            resolve("cardiac")

    def test_resolve_raises_for_no_data(self):
        with pytest.raises(SegmentationUnavailableError, match="无公开"):
            resolve("liver")

    def test_list_specs_shape(self):
        d = list_specs()
        assert set(d) == {"thyroid", "cardiac", "liver"}
        for v in d.values():
            assert {"organ", "status", "available", "arch", "input_size"} <= set(v)
        assert d["liver"]["unavailable_reason"]
