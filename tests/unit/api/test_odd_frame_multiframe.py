"""A.52 守卫：原生多帧「每帧字节数为奇」必须在**入库侧**规范化，且不改任何像素值。

真机背景：8 位 549×389 = 213561 字节/帧（奇）→ 7 帧 1494927 字节，DICOM 值长度必须为偶
→ 写盘补 1 字节 → `1494928 / 7` 不整除 → 前端 dicom-image-loader 抛
`Invalid frame length: 1494928 for 7 frames`，影像区黑屏（后端 pydicom 能读，所以此前
从未在生产端暴露）。修法：8 位 → 16 位重写（值一一对应、尺寸/间距/窗位全不动）。
"""
from __future__ import annotations

from io import BytesIO

import numpy as np
import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from app.api.v1.imaging import _normalize_odd_frame_multiframe


def _make_multiframe(frames: int, rows: int, cols: int, bits: int = 8) -> tuple[bytes, pydicom.Dataset]:
    """造一个原生多帧 DICOM（可指定让每帧字节数为奇的尺寸）。"""
    dtype = np.uint8 if bits == 8 else np.uint16
    array = (
        np.arange(frames * rows * cols, dtype=np.int64).reshape(frames, rows, cols) % 256
    ).astype(dtype)
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = generate_uid()
    meta.MediaStorageSOPInstanceUID = generate_uid()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds = FileDataset("t.dcm", {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.Modality = "US"
    ds.PatientName = "ODD^FRAME"
    ds.PatientID = "odd-frame-1"
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = bits
    ds.BitsStored = bits
    ds.HighBit = bits - 1
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.NumberOfFrames = frames
    ds.PixelData = array.tobytes()
    buf = BytesIO()
    pydicom.dcmwrite(buf, ds, enforce_file_format=True)
    return buf.getvalue(), ds


def test_odd_frame_length_is_normalized_without_pixel_change():
    raw, ds = _make_multiframe(frames=7, rows=3, cols=3)  # 3*3*1 = 9 字节/帧（奇）
    assert ds.Rows * ds.Columns % 2 == 1, "前置：构造出奇数帧长"

    content, out_ds, how = _normalize_odd_frame_multiframe(raw, ds)

    assert how == "odd_frame_length_to_16bit"
    # ①编码合规：帧长变偶、总长可整除
    frame_bytes = out_ds.Rows * out_ds.Columns * (out_ds.BitsAllocated // 8)
    assert frame_bytes % 2 == 0
    assert len(out_ds.PixelData) % int(out_ds.NumberOfFrames) == 0
    # ②尺寸/间距/帧数不变（测量口径不受影响）
    assert (out_ds.Rows, out_ds.Columns, out_ds.NumberOfFrames) == (3, 3, 7)
    # ③像素值一一对应（零改动）
    np.testing.assert_array_equal(out_ds.pixel_array, ds.pixel_array)
    # ④磁盘字节可被重新读回且 pixel_array 与原始完全一致
    reread = pydicom.dcmread(BytesIO(content), force=True)
    np.testing.assert_array_equal(reread.pixel_array, ds.pixel_array)


def test_even_frame_length_is_left_untouched():
    """偶帧长（16 位，或偶列数的 8 位）不得被改动。"""
    raw, ds = _make_multiframe(frames=3, rows=4, cols=4)
    content, out_ds, how = _normalize_odd_frame_multiframe(raw, ds)
    assert how is None
    assert content == raw
    assert out_ds is ds
    assert out_ds is ds

    raw16, ds16 = _make_multiframe(frames=3, rows=3, cols=3, bits=16)  # 18 字节/帧（偶）
    content16, out16, how16 = _normalize_odd_frame_multiframe(raw16, ds16)
    assert how16 is None
    assert content16 == raw16
    assert out16 is ds16


def test_single_frame_is_left_untouched():
    """单帧不受影响（单帧没有「总长/帧数」整除约束）。"""
    raw, ds = _make_multiframe(frames=1, rows=3, cols=3)
    content, out_ds, how = _normalize_odd_frame_multiframe(raw, ds)
    assert how is None
    assert content == raw
    assert out_ds is ds
