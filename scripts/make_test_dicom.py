#!/usr/bin/env python3
"""生成一个**合规的**最小 US DICOM 文件，供真机走查「装载 DICOM」用。

为什么需要它
------------
真机跑「装载 DICOM → 自动发起判读」时，直接用 `dicom_storage/` 里的文件会被后端拒：

    Missing required DICOM tags: PatientName

原因是**存储层按设计做了 PHI 剥离**（`app/services/dicom_storage.py` 的
`strip_dicom_phi`，fail-closed）—— 落盘副本已把 PatientName / PatientID 删掉。
所以「从库里取出来的文件」再传回去必然通不过校验，这是脱敏与入参校验的正常交互，
不是 bug。

字段严格照 `app/api/v1/imaging.py` 的 `REQUIRED_DICOM_TAGS`（10 项）
+ `Modality == "US"` + `BitsAllocated ∈ [1,16]` 填齐，缺一个都会被后端 400 挡回。

⚠️ PatientName / PatientID 是**虚构占位**（SMOKETEST），不含真实患者数据（铁律 #10）。

用法：``python scripts/make_test_dicom.py <输出路径>``
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid


def build() -> Dataset:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.MediaStorageSOPClassUID = pydicom.uid.UID("1.2.840.10008.5.1.4.1.1.6.1")
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

    # 身份 / 检查（占位值，非真实患者）
    ds.PatientName = "SMOKETEST^UI"
    ds.PatientID = "SMOKE-001"
    ds.PatientSex = "F"
    ds.PatientAge = "052Y"
    ds.StudyDate = "20261005"
    ds.StudyDescription = "UI walkthrough synthetic US"

    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID

    ds.Modality = "US"
    ds.Manufacturer = "UAI-SMOKE"
    ds.BodyPartExamined = "THYROID"

    # 后端只校 Rows/Columns/BitsAllocated，但要能看出是超声楔形
    rows, columns = 256, 512
    ds.Rows = rows
    ds.Columns = columns
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.PixelSpacing = [0.3, 0.3]

    yy, xx = np.mgrid[0:rows, 0:columns].astype(np.float32)
    depth = (yy - rows * 0.5) / rows
    width = np.abs(xx - columns / 2) / (columns / 2)
    sector = (np.abs(width) <= 1.0 - depth * 0.8) & (depth >= 0)
    speckle = np.random.default_rng(20261005).normal(0, 0.05, size=(rows, columns))
    img = np.where(sector, depth * 0.6 + speckle, 0.0)
    nodule = ((yy - rows * 0.58) ** 2 / (rows * 0.075) ** 2
              + (xx - columns * 0.60) ** 2 / (columns * 0.085) ** 2) <= 1.0
    img = np.where(nodule, 0.92, img)
    # ⚠️ 必须乘满量程再取整：astype(uint16) 对 [0,1] 浮点是**截断**不是缩放，
    # 直接转会把整幅图像截成全 0（一片黑）—— A.35 走查"影像空白"的第四层根因
    ds.PixelData = (np.clip(img, 0, 1) * 65535).astype(np.uint16).tobytes()
    return ds


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = Path(sys.argv[1])
    out.parent.mkdir(parents=True, exist_ok=True)
    ds = build()
    ds.save_as(str(out), enforce_file_format=True)
    print(f"wrote {out} ({out.stat().st_size} bytes)")
    print(f"PatientName={ds.PatientName} PatientID={ds.PatientID} Modality={ds.Modality} "
          f"Rows={ds.Rows} Columns={ds.Columns} BitsAllocated={ds.BitsAllocated}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
