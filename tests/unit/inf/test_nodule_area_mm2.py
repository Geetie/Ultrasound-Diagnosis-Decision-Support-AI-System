"""结节分割旁路的 mm² 换算与报告摘要（临床安全语义）。

钉死的两条规则：
  1. **缺 PixelSpacing 就不换算 mm²**（返回 None，报告只报像素面积）——
     拿默认值猜物理尺寸是危险的：医生会以为那是真实病灶大小。
  2. ``available=False`` 写**原因**，绝不含糊成"未检出"。
"""
from __future__ import annotations

import io

import numpy as np
import pytest

from app.services.diagnosis_pipeline import (
    _area_mm2_from_dicom,
    _pixel_spacing_from_dicom,
)
from app.services.report_service import _format_nodule_segmentation

pytestmark = [pytest.mark.track_inf]


def _dicom(pixel_spacing=None) -> bytes:
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.preamble = b"\0" * 128
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.Modality = "US"
    ds.Rows = 8
    ds.Columns = 8
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = np.zeros((8, 8), dtype=np.uint16).tobytes()
    if pixel_spacing is not None:
        ds.PixelSpacing = pixel_spacing
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    return buf.getvalue()


class TestPixelSpacing:
    def test_reads_two_values(self):
        assert _pixel_spacing_from_dicom(_dicom([0.2, 0.2])) == [0.2, 0.2]

    def test_missing_returns_none(self):
        assert _pixel_spacing_from_dicom(_dicom(None)) is None

    def test_single_value_rejected(self):
        # 只有一个数不足以换算二维面积 → 不猜
        assert _pixel_spacing_from_dicom(_dicom([0.2])) is None

    def test_non_positive_rejected(self):
        assert _pixel_spacing_from_dicom(_dicom([0.0, 0.2])) is None

    def test_garbage_bytes_return_none(self):
        assert _pixel_spacing_from_dicom(b"garbage") is None

    def test_empty_bytes_return_none(self):
        assert _pixel_spacing_from_dicom(b"") is None


class TestAreaMm2:
    def test_computes_mm2(self):
        # 1000 像素 × 0.2mm × 0.2mm = 40 mm²
        assert _area_mm2_from_dicom(_dicom([0.2, 0.2]), 1000) == 40.0

    def test_returns_none_without_spacing(self):
        assert _area_mm2_from_dicom(_dicom(None), 1000) is None

    def test_zero_area_returns_none(self):
        assert _area_mm2_from_dicom(_dicom([0.2, 0.2]), 0) is None

    def test_rectilinear_spacing(self):
        # 行距 0.3 / 列距 0.5 → 1000 px * 0.15 = 150 mm²
        assert _area_mm2_from_dicom(_dicom([0.3, 0.5]), 1000) == 150.0


class TestReportSummary:
    def test_includes_mm2_when_available(self):
        s = _format_nodule_segmentation({
            "available": True, "area_px": 4243, "area_mm2": 169.72,
            "lesion_area_ratio": 0.035, "bbox": [1, 2, 3, 4],
        })
        assert "4243" in s and "169.7" in s and "3.5%" in s

    def test_omits_mm2_when_absent_but_keeps_px(self):
        s = _format_nodule_segmentation({
            "available": True, "area_px": 4243, "lesion_area_ratio": 0.035,
        })
        assert "4243" in s
        assert "mm²" not in s, "没有 PixelSpacing 时不得出现 mm²"

    def test_unavailable_states_reason_not_not_found(self):
        s = _format_nodule_segmentation({
            "available": False, "reason": "liver 无公开超声分割数据集",
        })
        assert "未执行" in s
        assert "无公开超声分割数据集" in s
        assert "未检出" not in s, "『能力没跑』绝不能写成『未检出』"

    def test_missing_segmentation_says_not_executed(self):
        assert "未执行" in _format_nodule_segmentation(None)
        assert "未执行" in _format_nodule_segmentation({})

    def test_available_but_no_numbers(self):
        s = _format_nodule_segmentation({"available": True})
        assert s  # 不崩，且有兜底文案
