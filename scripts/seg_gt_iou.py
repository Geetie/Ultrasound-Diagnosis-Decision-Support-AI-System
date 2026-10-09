#!/usr/bin/env python3
"""分割准确性 GT 对照（feature_list: seg-accuracy-unverified-gt）。

对 CAMUS cardiac 体数据逐帧跑 cardiac 分割，与自带 GT mask 算 IoU，
输出 JSON 报告到 ~/uai/reports/。**在服务器上跑**（需要 venv + 权重）：

    cd ~/uai/src && ~/uai/venv/bin/python scripts/seg_gt_iou.py \
        --image ~/uai/data/camus/Images/patient0001_2CH_half_sequence.nii.gz \
        --mask  ~/uai/data/camus/Masks/patient0001_2CH_half_sequence_gt.nii.gz \
        --organ cardiac --max-frames 5

IoU < 0.6 记为不合格帧。**不进门禁常规**（GPU 依赖），交付前人工跑一次并留存报告。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np


def main() -> int:
    ap = argparse.ArgumentParser(description="分割 GT 对照 IoU")
    ap.add_argument("--image", required=True)
    ap.add_argument("--mask", required=True)
    ap.add_argument("--organ", default="cardiac")
    ap.add_argument("--max-frames", type=int, default=5)
    ap.add_argument("--out-dir", default=str(Path.home() / "uai" / "reports"))
    args = ap.parse_args()

    import nibabel as nib
    from PIL import Image

    from app.inference.nodule_segmentation import NoduleSegmenter

    vol = np.asarray(nib.load(args.image).dataobj)
    gt_vol = np.asarray(nib.load(args.mask).dataobj)
    n = min(args.max_frames, vol.shape[-1])
    segmenter = NoduleSegmenter.get(args.organ)

    per_frame = []
    for idx in range(n):
        frame = vol[..., idx].astype(np.float32)
        frame = (frame - frame.min()) / max(1e-6, frame.max() - frame.min())
        tmp = Path("/tmp/_seg_gt_frame.png")
        Image.fromarray((frame * 255).astype(np.uint8)).save(tmp)

        t = time.monotonic()
        try:
            seg = segmenter.infer(tmp.read_bytes())
        except Exception as exc:
            per_frame.append({"frame": idx, "error": str(exc)[:120]})
            continue
        elapsed = time.monotonic() - t

        # GT：CAMUS 标注含多结构（LV腔/心肌/心房），取全部 GT>0 为对照目标
        gt = gt_vol[..., idx] > 0
        pred_mask = np.asarray(seg.get("mask"), dtype=bool) if "mask" in seg else None
        if pred_mask is None or pred_mask.shape != gt.shape:
            per_frame.append({"frame": idx, "error": "mask shape mismatch or missing"})
            continue

        inter = int((pred_mask & gt).sum())
        union = int((pred_mask | gt).sum())
        iou = round(inter / union, 4) if union else None
        per_frame.append({
            "frame": idx,
            "iou": iou,
            "pred_px": int(pred_mask.sum()),
            "gt_px": int(gt.sum()),
            "area_mm2": seg.get("area_mm2"),
            "mean_prob": seg.get("mean_prob"),
            "elapsed_s": round(elapsed, 2),
        })

    ious = [f["iou"] for f in per_frame if isinstance(f.get("iou"), float)]
    summary = {
        "image": args.image,
        "mask": args.mask,
        "organ": args.organ,
        "frames": len(per_frame),
        "iou_mean": round(float(np.mean(ious)), 4) if ious else None,
        "iou_min": min(ious) if ious else None,
        "pass_0_6": sum(1 for i in ious if i >= 0.6),
        "fail_frames": [
            f["frame"] for f in per_frame
            if isinstance(f.get("iou"), float) and f["iou"] < 0.6
        ],
        "per_frame": per_frame,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (out_dir / f"seg_gt_iou_{args.organ}_{stamp}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {k: v for k, v in summary.items() if k != "per_frame"},
        ensure_ascii=False, indent=2))
    return 0 if ious and summary["iou_mean"] and summary["iou_mean"] >= 0.6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
