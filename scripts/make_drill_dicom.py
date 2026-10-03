"""为端到端演练造合规 US DICOM（从 TN3K 真实超声图）。

比 scripts/jpg2dcm.py 多了三处**演练必需**的标签：
  - PatientID（存储层 strip_phi 会改写它，先给个可识别值便于核对）
  - StudyDate / SeriesNumber（clinician 字段抽取要读）
  - PixelSpacing（面积换算：mm² = 像素数 × 行距 × 列距）

用法：python scripts/make_drill_dicom.py <输入图> <输出.dcm>
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid


def build(src: Path, dst: Path, patient_id: str = "DRILL-PATIENT-001") -> dict:
    img = Image.open(src).convert("L")
    arr = np.asarray(img, dtype=np.uint8)
    rows, cols = arr.shape

    ds = FileDataset(None, {}, file_meta=FileMetaDataset(), preamble=b"\0" * 128)
    ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.file_meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
    ds.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    ds.file_meta.ImplementationClassUID = generate_uid()

    ds.SOPClassUID = SecondaryCaptureImageStorage
    ds.SOPInstanceUID = ds.file_meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = generate_uid()
    ds.SeriesInstanceUID = generate_uid()

    ds.Modality = "US"
    ds.PatientID = patient_id
    ds.PatientName = "DRILL^PHANTOM"
    ds.PatientBirthDate = "19700101"
    ds.PatientSex = "F"
    ds.StudyDate = "20261004"
    ds.StudyDescription = "Drill thyroid ultrasound"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    ds.StudyID = "1"
    ds.AccessionNumber = "DRILL1"
    ds.ReferringPhysicianName = ""

    # 超声惯例：设备/区域描述
    ds.Manufacturer = "Drill"
    ds.ManufacturerModelName = "Synthetic"
    ds.SoftTissueMechanicalIndex = "1.0"

    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.Rows = rows
    ds.Columns = cols
    ds.BitsAllocated = 8
    ds.BitsStored = 8
    ds.HighBit = 7
    ds.PixelRepresentation = 0
    # 甲状腺超声典型：横向 0.2mm / 纵向 0.2mm（用于面积 mm² 换算）
    ds.PixelSpacing = [0.2, 0.2]
    ds.RescaleIntercept = "0"
    ds.RescaleSlope = "1"
    ds.WindowCenter = "128"
    ds.WindowWidth = "256"

    ds.PixelData = arr.tobytes()
    ds["PixelData"].is_undefined_length = False

    dst.parent.mkdir(parents=True, exist_ok=True)
    ds.save_as(str(dst), enforce_file_format=True)

    return {
        "path": str(dst),
        "rows": rows,
        "cols": cols,
        "sop_instance_uid": ds.SOPInstanceUID,
        "pixel_spacing_mm": [0.2, 0.2],
        "size_bytes": dst.stat().st_size,
    }


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: make_drill_dicom.py <src_image> <out.dcm>")
        return 2
    info = build(Path(sys.argv[1]), Path(sys.argv[2]))
    print(f"OK {info['path']} {info['rows']}x{info['cols']} "
          f"{info['size_bytes']}B sop={info['sop_instance_uid']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
