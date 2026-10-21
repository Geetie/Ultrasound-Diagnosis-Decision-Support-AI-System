"""A.81 守卫：DICOM 结构感知层。

真机事实（580 个文件）：468 超声多帧 / 67 二次采集 / 45 超声单帧；7 帧占 12.2%；
**区域校准 580/580 全部缺失**。这些必须体现为代码里的判断，而不是文档里的备注。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.imaging.dicom_structure import (
    SOP_SECONDARY_CAPTURE,
    SOP_US_MULTIFRAME,
    SOP_US_SINGLEFRAME,
    calibration_status,
    frame_indices,
    measurement_allowed,
    segmentation_allowed,
    sop_kind,
    structure_of,
)


def test_sop_kind_maps_three_real_classes():
    assert sop_kind(SOP_US_MULTIFRAME) == "us_multiframe"
    assert sop_kind(SOP_US_SINGLEFRAME) == "us_singleframe"
    assert sop_kind(SOP_SECONDARY_CAPTURE) == "secondary_capture"
    assert sop_kind("1.2.3.4") == "other"
    assert sop_kind(None) == "other"


def test_frame_indices_single_and_multiframe():
    assert frame_indices(1) == [0]
    assert frame_indices(7) == [0, 3, 6]
    assert frame_indices(2) == [0, 1]
    assert frame_indices(0) == [0]  # 脏值按单帧处理，不抛异常


def test_calibration_requires_region_sequence():
    """只有区域校准才算"可换算物理单位"——PixelSpacing 单独存在不算。"""
    assert calibration_status(True, True) == "region_calibrated"
    assert calibration_status(False, True) == "pixel_spacing_only"
    assert calibration_status(False, False) == "uncalibrated"


def test_measurement_only_allowed_when_region_calibrated():
    """A.80：580/580 无区域校准 → 全部不允许换算 mm（避免给出假尺寸）。"""
    assert measurement_allowed("region_calibrated") is True
    assert measurement_allowed("pixel_spacing_only") is False
    assert measurement_allowed("uncalibrated") is False


def test_structure_of_marks_secondary_capture_as_burned_in():
    ds = SimpleNamespace(
        SOPClassUID=SOP_SECONDARY_CAPTURE, Rows=480, Columns=640, BitsStored=8
    )
    st = structure_of(ds)
    assert st["sop_kind"] == "secondary_capture"
    assert st["burned_in"] is True
    assert segmentation_allowed(st) == (False, "secondary_capture_burned_in")


def test_structure_of_multiframe_without_calibration():
    ds = SimpleNamespace(
        SOPClassUID=SOP_US_MULTIFRAME,
        NumberOfFrames="7",
        BitsStored=8,
        PhotometricInterpretation="MONOCHROME2",
        PixelSpacing=[0.1, 0.1],
    )
    st = structure_of(ds)
    assert st["frames"] == 7
    assert st["calibration"] == "pixel_spacing_only"
    assert measurement_allowed(st["calibration"]) is False
    assert segmentation_allowed(st) == (True, "")


def test_structure_of_tolerates_missing_fields():
    st = structure_of(SimpleNamespace())
    assert st["frames"] == 1
    assert st["sop_kind"] == "other"
    assert st["calibration"] == "uncalibrated"
    assert st["burned_in"] is False


@pytest.mark.parametrize("frames_raw", ["abc", None, 0])
def test_bad_frame_count_falls_back_to_one(frames_raw):
    st = structure_of(SimpleNamespace(SOPClassUID=SOP_US_SINGLEFRAME, NumberOfFrames=frames_raw))
    assert st["frames"] == 1
