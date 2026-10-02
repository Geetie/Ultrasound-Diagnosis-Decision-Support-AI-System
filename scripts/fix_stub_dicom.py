# -*- coding: utf-8 -*-
"""把 dicom_storage/ 里的**人造 DICOM 桩**补成合规文件。

问题：那些文件只有 7 个标签（SOPInstanceUID / Modality / PatientID /
StudyInstanceUID / SeriesInstanceUID / Rows / Columns / BitsAllocated），
缺 PhotometricInterpretation / BitsStored / HighBit / PixelRepresentation /
SamplesPerPixel —— 任何 DICOM 阅读器都渲染不出来（实测 cornerstone 报
"DICOM 加载失败"）。

做法：**保留原有像素数据与全部既有标签**，只补齐渲染所必需的元数据。
不伪造临床像素 —— 那是数据的事，这里只修元数据。
幂等：已是合规文件则跳过。
"""
import glob
import os

import pydicom
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

ROOT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
STORE = os.path.join(ROOT, "dicom_storage")

REQUIRED = [
    "PhotometricInterpretation",
    "SamplesPerPixel",
    "BitsStored",
    "HighBit",
    "PixelRepresentation",
]

fixed, skipped = [], []
for path in sorted(glob.glob(os.path.join(STORE, "*", "*", "*", "*.dcm"))):
    ds = pydicom.dcmread(path)
    missing = [k for k in REQUIRED if k not in ds]
    if not missing:
        skipped.append(path)
        continue

    rows, cols = int(ds.Rows), int(ds.Columns)
    bits = int(ds.BitsAllocated)

    # file_meta：原文件没有，补上
    if not hasattr(ds, "file_meta") or not ds.file_meta.TransferSyntaxUID:
        ds.file_meta = pydicom.dataset.FileMetaDataset()
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
        ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        ds.file_meta.ImplementationClassUID = generate_uid()

    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.SamplesPerPixel = 1
    ds.BitsStored = bits
    ds.HighBit = bits - 1
    ds.PixelRepresentation = 0  # 无符号

    # 像素数据：保留原有的；确实没有才补一块黑底（并说明是合成的）
    if "PixelData" not in ds or len(ds.PixelData) == 0:
        ds.PixelData = b"\x00" * (rows * cols * (bits // 8))
        synthesized = True
    else:
        synthesized = False

    # 给一个显示窗，便于直接出图
    if "WindowCenter" not in ds:
        ds.WindowCenter = 128
    if "WindowWidth" not in ds:
        ds.WindowWidth = 256

    ds.save_as(path, enforce_file_format=True)

    chk = pydicom.dcmread(path)
    assert chk.PhotometricInterpretation == "MONOCHROME2"
    assert chk.Rows == rows and chk.Columns == cols
    fixed.append((path, missing, synthesized))

print("已修复 %d 个，跳过 %d 个（已合规）" % (len(fixed), len(skipped)))
for path, missing, synth in fixed:
    print("  + %s" % os.path.relpath(path, ROOT))
    print("      补齐: %s%s" % (", ".join(missing), "  [像素为合成黑底]" if synth else ""))
print()
print("=== 复核 ===")
for path in sorted(glob.glob(os.path.join(STORE, "*", "*", "*", "*.dcm"))):
    ds = pydicom.dcmread(path, stop_before_pixels=True)
    print("  %-46s %s %sx%s %s bpp=%s" % (
        os.path.relpath(path, STORE), ds.Modality, ds.Rows, ds.Columns,
        ds.PhotometricInterpretation, ds.BitsStored,
    ))
