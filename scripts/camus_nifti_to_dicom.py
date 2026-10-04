"""CAMUS NIfTI to DICOM for acceptance kit (CC-BY-NC-SA, academic non-commercial)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CAMUS_IMAGES = Path(os.path.expanduser("~/uai/data/camus/Images"))
FRAME_IDX = [8, 9, 10]


def pick(n=2):
    if not CAMUS_IMAGES.is_dir():
        return []
    files = sorted(CAMUS_IMAGES.rglob("*.nii.gz"))
    if not files:
        return []
    step = max(1, len(files) // n)
    return [(f"cardiac_{i:02d}", files[i * step], list(FRAME_IDX))
            for i in range(min(n, len(files)))]


def to_dicom(nifti, frame, out_path, patient_id):
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
