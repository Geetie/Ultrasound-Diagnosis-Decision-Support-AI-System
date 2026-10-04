#!/usr/bin/env python3
"""CAMUS NIfTI → DICOM（交互核验材料包用）。

为什么需要
----------
CAMUS-Lite 是 NIfTI（``.nii.gz``，每例 18 帧心动序列），而上传端点**只收
DICOM**（``app/api/v1/imaging.py`` 强制 Modality=US + 必需标签），所以核验包里
的心脏用例必须先转换。取中间 3 帧（收缩期附近）各一张。

许可：CC-BY-NC-SA-4.0，**学术非商用**。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

CAMUS_IMAGES = Path(os.path.expanduser("~/uai/data/camus/Images"))
FRAME_IDX = [8, 9, 10]


def pick(n: int = 2) -> list[tuple[str, Path, list[int]]]:
    """挑 n 个 CAMUS 病例（分散取，避免都来自同一病人）。"""
    if not CAMUS_IMAGES.is_dir():
        return []
    files = sorted(CAMUS_IMAGES.rglob("*.nii.gz"))
    if not files:
        return []
    step = max(1, len(files) // n)
    return [
        (f"cardiac_{i:02d}", files[i * step], list(FRAME_IDX))
        for i in range(min(n, len(files)))
    ]


def to_dicom(nifti: Path, frame: int, out_path: Path, patient_id: str) -> dict:
    """NIfTI 单帧 → 8bit 灰度 PNG → 合规 DICOM（复用演练的 make_drill_dicom）。"""
    import nibabel as nib
    import numpy as np
    from make_drill_dicom import build as build_dicom
    from PIL import Image

    arr = np.asarray(nib.load(str(nifti)).dataobj)[:, :, frame]
    if arr.max() > 1.5:
        arr = arr / 255.0
    png = out_path.with_suffix(".png")
    Image.fromarray(np.clip(arr * 255, 0, 255).astype(np.uint8)).save(png)
    try:
        return build_dicom(png, out_path, patient_id=patient_id)
    finally:
        png.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="CAMUS NIfTI → DICOM（核验包）")
    ap.add_argument("--out", default=os.path.expanduser("~/acceptance_kit"))
    ap.add_argument("--n", type=int, default=2)
    args = ap.parse_args()

    out_dir = Path(args.out) / "dicom"
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for cid, nifti, frames in pick(args.n):
        for fi in frames:
            dcm = out_dir / f"{cid}_f{fi}.dcm"
            try:
                info = to_dicom(nifti, fi, dcm, f"ACC-{cid}")
            except Exception as exc:
                print(f"skip {cid} frame {fi}: {type(exc).__name__}: {exc}")
                continue
            manifest.append({
                "id": f"{cid}_f{fi}",
                "organ": "cardiac",
                "dicom": str(dcm),
                "source": f"CAMUS-Lite: {nifti.name}",
                "license": "CC-BY-NC-SA-4.0（学术非商用）",
                "size": f"{info['rows']}x{info['cols']}",
                "frame": fi,
                "expected_checks": [
                    "诊断意见非空（不含 llm_unavailable）",
                    "开启分割旁路时 cardiac（DSC 0.8395）应产出左心室轮廓",
                ],
            })

    mp = Path(args.out) / "cardiac_manifest.json"
    mp.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"cardiac_items": len(manifest), "manifest": str(mp)},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
