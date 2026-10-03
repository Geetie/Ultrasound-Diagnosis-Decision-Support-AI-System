"""DICOM 像素提取单测（合成 DICOM，不依赖真实影像文件）。"""
from __future__ import annotations

import io

import numpy as np
import pytest
from PIL import Image

from app.imaging.dicom_pixels import (
    DicomPixelExtractionError,
    extract_frame_from_bytes,
    extract_frame_from_dataset,
)

pytestmark = [pytest.mark.track_inf]


def _make_dicom(arr: np.ndarray, *, frames: int = 1, modality: str = "US"):
    """合成一个最小可解析的 DICOM dataset（像素 + 必要元数据）。"""
    from pydicom.dataset import Dataset, FileMetaDataset
    from pydicom.uid import ExplicitVRLittleEndian, generate_uid

    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.preamble = b"\x00" * 128
    ds.Modality = modality
    # 传入数组约定：(frames, rows, cols) 或 (rows, cols)
    rows, cols = (arr.shape[1], arr.shape[2]) if arr.ndim == 3 else (arr.shape[0], arr.shape[1])
    ds.Rows = int(rows)
    ds.Columns = int(cols)
    if frames > 1:
        ds.NumberOfFrames = frames
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = arr.astype(np.uint16).tobytes()
    return ds


@pytest.fixture
def single_frame_ds():
    arr = (np.arange(64 * 48, dtype=np.float32).reshape(64, 48) % 255)
    return _make_dicom(arr, frames=1)


@pytest.fixture
def cine_ds():
    # 18 帧，每帧填充不同值 → 验证取中间帧。
    # 注意：pydicom 多帧的 pixel_array 是 **(frames, rows, cols)**，
    # 故构造时帧必须在**第一轴**（NIfTI 才是最后一轴，别混）。
    frames = [np.full((32, 32), i * 10, dtype=np.float32) for i in range(18)]
    return _make_dicom(np.stack(frames, axis=0), frames=18)


def _decode(png_bytes: bytes) -> np.ndarray:
    return np.asarray(Image.open(io.BytesIO(png_bytes)).convert("L"))


class TestSingleFrame:
    def test_extracts_correct_size(self, single_frame_ds):
        f = extract_frame_from_dataset(single_frame_ds)
        assert (f.width, f.height) == (48, 64)  # Columns × Rows
        assert f.total_frames == 1
        assert f.frame_index == 0

    def test_png_is_rgb_and_decodable(self, single_frame_ds):
        f = extract_frame_from_dataset(single_frame_ds)
        img = Image.open(io.BytesIO(f.png_bytes))
        assert img.format == "PNG"
        assert img.mode == "RGB"          # in_channels=3 契约
        assert img.size == (48, 64)
        assert _decode(f.png_bytes).shape == (64, 48)

    def test_modality_recorded(self, single_frame_ds):
        assert extract_frame_from_dataset(single_frame_ds).source_modality == "US"

    def test_pixels_preserved(self, single_frame_ds):
        f = extract_frame_from_dataset(single_frame_ds)
        got = _decode(f.png_bytes)
        # uint16 输入 0~254 → 直接原样（hi<=255 且 lo>=0 不做拉伸）
        assert np.array_equal(got, single_frame_ds.pixel_array.astype(np.uint8))

    def test_negative_and_large_values_stretched(self):
        arr = np.linspace(-1000, 3000, 32 * 32, dtype=np.float32).reshape(32, 32)
        ds = _make_dicom(arr, frames=1)
        f = extract_frame_from_dataset(ds)
        got = _decode(f.png_bytes)
        assert got.min() == 0 and got.max() == 255

    def test_constant_array_safe(self):
        ds = _make_dicom(np.full((16, 16), 42.0, dtype=np.float32), frames=1)
        got = _decode(extract_frame_from_dataset(ds).png_bytes)
        assert got.shape == (16, 16)


class TestMultiFrame:
    """多帧契约：默认取中间帧、帧轴在**第一轴**（pydicom 语义，非 NIfTI）、越界夹取。

    像素内容在 TestSingleFrame 已覆盖；这里刻意只断言结构与帧选择，
    避免把"合成数据的字节序"也变成测试 brittle 点。
    """

    def test_picks_middle_frame_by_default(self, cine_ds):
        f = extract_frame_from_dataset(cine_ds)
        assert f.total_frames == 18
        assert f.frame_index == 9          # 18 // 2
        assert (f.width, f.height) == (32, 32)

    def test_explicit_frame_index(self, cine_ds):
        f = extract_frame_from_dataset(cine_ds, frame_index=3)
        assert f.frame_index == 3
        assert (f.width, f.height) == (32, 32)

    def test_frame_axis_is_first(self, cine_ds):
        """回归钉子：pydicom 多帧是 (frames, rows, cols)；按最后一轴取会拿到错误切片。"""
        raw = cine_ds.pixel_array
        assert raw.ndim == 3
        assert raw.shape[0] == 18, "pydicom 多帧首轴必须是帧数"
        assert raw.shape[1:] == (32, 32)

    def test_out_of_range_clamped(self, cine_ds):
        assert extract_frame_from_dataset(cine_ds, frame_index=99).frame_index == 17

    def test_negative_index_clamped_to_zero(self, cine_ds):
        assert extract_frame_from_dataset(cine_ds, frame_index=-5).frame_index == 0


class TestBytesAndErrors:
    def test_from_bytes_roundtrip(self, single_frame_ds):
        buf = io.BytesIO()
        single_frame_ds.save_as(buf)
        f = extract_frame_from_bytes(buf.getvalue())
        assert (f.width, f.height) == (48, 64)

    def test_garbage_bytes_raise(self):
        with pytest.raises(DicomPixelExtractionError, match="解析失败"):
            extract_frame_from_bytes(b"not-a-dicom-file-at-all")

    def test_empty_bytes_raise(self):
        with pytest.raises(DicomPixelExtractionError):
            extract_frame_from_bytes(b"")

    def test_as_dict_shape(self, single_frame_ds):
        d = extract_frame_from_dataset(single_frame_ds).as_dict()
        assert {"width", "height", "frame_index", "total_frames", "png_bytes"} <= set(d)
