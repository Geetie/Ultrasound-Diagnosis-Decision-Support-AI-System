"""A.80 守卫：DICOM 结构抽取必须**容错且不臆测**。

真机踩过的坑：DICOM 缺 `NumberOfFrames`（单帧）时不能当成 0 帧；
`NumberOfFrames` 可能是字符串（IS VR）；没有 `file_meta` 时不能 AttributeError。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

_SRC = (
    Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p59_dicom_structure.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_p59_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ds(**kw):
    base = {"file_meta": SimpleNamespace(TransferSyntaxUID="1.2.840.10008.1.2.4.70")}
    base.update(kw)
    return SimpleNamespace(**base)


def test_single_frame_defaults_to_one():
    p59 = _load()
    info = p59.summarize_file(_ds(Modality="US", Rows=332, Columns=365))
    assert info["frames"] == 1
    assert info["rows"] == 332 and info["cols"] == 365


def test_number_of_frames_as_string_is_parsed():
    p59 = _load()
    info = p59.summarize_file(_ds(NumberOfFrames="37"))
    assert info["frames"] == 37


def test_bad_number_of_frames_falls_back_to_one():
    p59 = _load()
    assert p59.summarize_file(_ds(NumberOfFrames="abc"))["frames"] == 1
    assert p59.summarize_file(_ds(NumberOfFrames=None))["frames"] == 1


def test_missing_file_meta_does_not_raise():
    p59 = _load()
    info = p59.summarize_file(SimpleNamespace(Modality="US"))
    assert info["transfer_syntax"] == ""
    assert info["modality"] == "US"


def test_us_regions_and_flags_are_reported():
    p59 = _load()
    ds = _ds(
        SequenceOfUltrasoundRegions=[object(), object()],
        PixelSpacing=[0.1, 0.1],
        WindowCenter=128,
        PixelData=b"x",
    )
    info = p59.summarize_file(ds)
    assert info["us_regions"] == 2
    assert info["has_pixel_spacing"] is True
    assert info["window_center"] is True
    assert info["has_pixel_data"] is True
