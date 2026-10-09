"""把 CAMUS 真实超声帧转成走查用 DICOM（用户制度：测试一律真实数据，禁合成样本）。

背景（2026-10-09）：A.38 走查误用 make_test_dicom.py 生成的合成 DICOM
（全零纹理假图），被用户叫停 —— 走查/演示/评测的影像样本必须来自真实采集，
本项目即 CAMUS 公开心脏超声数据集（服务器 ~/uai/data/camus/，nii + Masks GT）。

用法（在服务器上跑，venv 有 nibabel/pydicom）：
    ~/uai/venv/bin/python scripts/camus_frame_to_dicom.py \
        --out /tmp/camus_real_4ch.dcm --count 2 --patient CAMUS-REAL-001
然后把文件 scp 回本机，走查脚本 --dicom 指向它。

不加 PHI、不伪造采集设备字段：Modality=US、真实像素矩阵与采样参数，
患者名用数据集占位（走查账号本身是演练账号，数据不出内网）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import nibabel as nib
import numpy as np
import pydicom
from pydicom.dataset import Dataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

CAMUS_ROOT = Path.home() / "uai" / "data" / "camus" / "Images"


def pick_study(root: Path, keyword: str = "4CH_half") -> Path:
    """挑一个 4CH_half 序列（心脏四腔心，帧数适中、组织结构真实丰富）。"""
    if not root.exists():
        raise SystemExit(f"CAMUS 数据不存在：{root}")
    # 目录是平铺的：patient0301_4CH_half_sequence.nii.gz
    for cand in sorted(root.glob(f"*{keyword}*.nii.gz")):
        return cand
    raise SystemExit(f"没找到含 {keyword} 的序列（root={root}）")


def convert(nii_path: Path, out: Path, count: int, patient_id: str) -> None:
    img = nib.load(nii_path)
    vol = np.asarray(img.dataobj, dtype=np.float32)  # (H, W, T)
    # 真实像素间距（mm）：CAMUS header 带 zooms（patient0001 4CH = 0.308mm，
    # xyzt_units=mm）。测量工具的 mm 读数全靠它 —— 不写 PixelSpacing，cornerstone
    # 只能给出像素单位，医生量出来的是无单位数（违反真实数据制度的变体）。
    zx, zy, _ = img.header.get_zooms()[:3]
    units = int(img.header["xyzt_units"]) & 0x03
    if units != 2 or zx <= 0 or zy <= 0:
        raise SystemExit(
            f"{nii_path.name} header 无 mm 像素间距（zooms={img.header.get_zooms()}, "
            f"units={units}）—— 拒绝写假 PixelSpacing"
        )
    pixel_spacing = [float(zy), float(zx)]  # DICOM 行距/列距 = nii 的 y/x zoom
    frames = [
        np.clip(vol[..., t], 0, 255).astype(np.uint8)
        for t in range(min(count, vol.shape[2]))
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    study_uid = generate_uid()
    series_uid = generate_uid()
    sop_uids = [generate_uid() for _ in frames]
    for idx, frame in enumerate(frames):
        ds = Dataset()
        ds.file_meta = pydicom.dataset.FileMetaDataset()
        ds.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.5.1.4.1.1.3.1"  # US Multi-frame
        ds.file_meta.MediaStorageSOPInstanceUID = sop_uids[idx]
        ds.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        ds.SOPClassUID = ds.file_meta.MediaStorageSOPClassUID
        ds.SOPInstanceUID = sop_uids[idx]
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = series_uid
        ds.PatientID = patient_id
        ds.PatientName = f"CAMUS^REALFRAME{idx + 1}"
        ds.Modality = "US"
        ds.ImageType = ["DERIVED", "PRIMARY"]
        ds.SamplesPerPixel = 1
        ds.PhotometricInterpretation = "MONOCHROME2"
        ds.Rows, ds.Columns = frame.shape
        ds.BitsAllocated = ds.BitsStored = 8
        ds.HighBit = 7
        ds.PixelRepresentation = 0
        ds.InstanceNumber = idx + 1
        ds.PixelSpacing = pixel_spacing
        ds.Units = "mm"
        # 真实帧写入 PixelData —— 与合成样本（纯图形+假纹理）的本质区别
        ds.PixelData = frame.tobytes()
        pydicom.dcmwrite(out.with_name(f"{out.stem}_{idx}.dcm"), ds, enforce_file_format=True)
    print(f"OK: {len(frames)} 帧 ← {nii_path.name} → {out.parent}/{out.stem}_*.dcm")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True, help="输出路径前缀，如 /tmp/camus_real_4ch.dcm")
    ap.add_argument("--count", type=int, default=2, help="取前 N 帧（默认 2，供多帧走查）")
    ap.add_argument("--patient", default="CAMUS-REAL-001", help="患者占位编号")
    ap.add_argument("--keyword", default="4CH_half", help="序列关键字")
    args = ap.parse_args()
    nii_path = pick_study(CAMUS_ROOT, args.keyword)
    convert(nii_path, Path(args.out), args.count, args.patient)


if __name__ == "__main__":
    main()
