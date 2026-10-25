"""结节分割旁路的 mm² 换算与报告摘要（临床安全语义）。

钉死的三条规则：
  1. **缺 PixelSpacing 就不换算 mm²**（返回 None，报告只报像素面积）——
     拿默认值猜物理尺寸是危险的：医生会以为那是真实病灶大小。
  2. **A.81 安全闸（2026-10-21，本文件 2026-10-25 补齐）**：超声物理单位的
     **唯一**合法依据是 DICOM 的 `SequenceOfUltrasoundRegions`。只有
     `PixelSpacing` 而没有区域校准时（`calibration_status == "pixel_spacing_only"`），
     **也不换算** —— 本库 580 个文件实测全部没有区域校准，`PixelSpacing` 有的是
     标称值、有的与实际缩放不符，乘出来的 mm² 是"看起来很准的假数"。
  3. ``available=False`` 写**原因**，绝不含糊成"未检出"。

## 为什么本文件在 2026-10-25 被改写（真缺陷，不是风格调整）

原用例（`d6a6cc9`，2026-10-04）断言"只要有 PixelSpacing 就换算 mm²"。
10-21 的 `ec5063e` 加了 A.81 安全闸后，这条断言与**生产语义相反** ——
于是 `test_reads_two_values` / `test_computes_mm2` / `test_rectilinear_spacing`
三条**长期红灯**（每轮全量运行都失败），而全仓没人修它。

后果不是"少绿一条"：一条长期红的用例会让**真正的回归被淹没**在既有红灯里
（本项目的"零静默"原则同样适用于"零长期红灯"）。
本文件按**当前生产语义**重写，并显式加上"必须有区域校准才换算"的腿 ——
即：修完它以后，A.81 的闸门**有测试守着**，而不是只有代码里有。
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


def _dicom(pixel_spacing=None, *, region_calibrated: bool = True) -> bytes:
    """构造一份内存 DICOM。

    Args:
        pixel_spacing: `PixelSpacing` 值（行距, 列距）。
        region_calibrated: 是否带 `SequenceOfUltrasoundRegions`。
            **默认 True** —— 因为它是"允许换算 mm²"的唯一合法前提；
            要测"没校准就不换算"时显式传 False。
    """
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.sequence import Sequence
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
    if region_calibrated:
        # 区域校准的最小合法形态：一条 0x0001（区域=全部）的物理间距条目。
        # 管线只判"是否存在该序列"（`structure_of` → `calibration_status`），
        # 不解释每个字段，所以这里只需要一条结构合法的条目。
        region = Dataset()
        region.RegionSpatialFormat = 0x0001  # 2D region of interest
        region.RegionDataType = 0x0001  # pixel array
        region.RegionLocationMinX0 = 0
        region.RegionLocationMinY0 = 0
        region.RegionLocationMaxX1 = 7
        region.RegionLocationMaxY1 = 7
        region.PhysicalUnitsXDirection = 0x0003  # cm
        region.PhysicalUnitsYDirection = 0x0003
        region.PhysicalDeltaX = 0.02  # 0.02 cm = 0.2 mm
        region.PhysicalDeltaY = 0.02
        ds.SequenceOfUltrasoundRegions = Sequence([region])
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


class TestRegionCalibrationGate:
    """A.81 安全闸：**只有区域校准才允许换算物理单位**（本类是该闸门的测试）。"""

    def test_pixel_spacing_without_region_is_not_used(self):
        """有 PixelSpacing 但**没有区域校准** → 不换算（返回 None）。

        这是 580 个真实文件的形态（`SequenceOfUltrasoundRegions` 全为 0）。
        反证腿：去掉 `measurement_allowed` 判断，这条立刻红。
        """
        assert (
            _pixel_spacing_from_dicom(
                _dicom([0.2, 0.2], region_calibrated=False)
            )
            is None
        )

    def test_area_without_region_is_none(self):
        """没校准 → 面积也不给 mm²（宁可少一个数，不可给错一个数）。"""
        assert (
            _area_mm2_from_dicom(_dicom([0.2, 0.2], region_calibrated=False), 1000)
            is None
        )

    def test_region_calibrated_allows_conversion(self):
        """有区域校准 → 正常换算（闸门不是"一律不给"）。"""
        assert _pixel_spacing_from_dicom(_dicom([0.2, 0.2])) == [0.2, 0.2]


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
