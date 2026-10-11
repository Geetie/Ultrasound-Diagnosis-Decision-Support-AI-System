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
import sys
import time
from pathlib import Path

import numpy as np

# 允许从任意 cwd 直接运行：把仓库根（scripts/ 的上一级）加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    ap = argparse.ArgumentParser(description="分割 GT 对照 IoU")
    ap.add_argument("--image", default=None, help="单病例影像 nii（多病例模式可不传）")
    ap.add_argument("--mask", default=None, help="单病例 GT nii")
    ap.add_argument("--organ", default="cardiac")
    ap.add_argument("--max-frames", type=int, default=5)
    ap.add_argument(
        "--multi",
        type=int,
        default=0,
        help="多病例稳定度模式：自动扫描 CAMUS 前 N 个 4CH 病例（单病例模式忽略）",
    )
    ap.add_argument(
        "--label",
        default="2",
        help="GT 对照口径（CAMUS 标签）：默认 2=MYO心肌 —— A.40 口径调研实测"
             " unet_camus.pt 的分割目标就是心肌（MYO IoU 0.58≈官方 Dice 0.84；"
             "LV 0.12、ATR 0.0、全并集 0.31 都是口径错配）",
    )
    ap.add_argument("--out-dir", default=str(Path.home() / "uai" / "reports"))
    args = ap.parse_args()

    if args.multi > 0:
        return multi_patient_mode(args)

    if not args.image or not args.mask:
        ap.error("单病例模式需要 --image/--mask；多病例用 --multi N")
    return single_mode(args)


def single_mode(args: argparse.Namespace) -> int:
    import nibabel as nib

    from app.inference.nodule_segmentation import NoduleSegmenter

    vol = np.asarray(nib.load(args.image).dataobj)
    gt_vol = np.asarray(nib.load(args.mask).dataobj)
    n = min(args.max_frames, vol.shape[-1])
    segmenter = NoduleSegmenter.get(args.organ)

    per_frame = _eval_case(segmenter, vol, gt_vol, n, args.label)

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


def _eval_case(
    segmenter, vol: np.ndarray, gt_vol: np.ndarray, n: int, label: str
) -> list[dict]:
    """对单个病例逐帧推理并按口径算 IoU（单病例/多病例两模式共用）。"""
    from io import BytesIO

    from PIL import Image

    per_frame: list[dict] = []
    for idx in range(n):
        frame = vol[..., idx].astype(np.float32)
        frame = (frame - frame.min()) / max(1e-6, frame.max() - frame.min())
        tmp = Path("/tmp/_seg_gt_frame.png")
        Image.fromarray((frame * 255).astype(np.uint8)).save(tmp)

        t = time.monotonic()
        try:
            # infer 的字节入参是 **keyword-only**（image_bytes=）；位置传参会把
            # PNG 字节串当 image_path 打开（[Errno 36] File name too long）
            seg = segmenter.infer(image_bytes=tmp.read_bytes())
        except Exception as exc:
            per_frame.append({"frame": idx, "error": str(exc)[:120]})
            continue
        elapsed = time.monotonic() - t

        # GT：CAMUS 标注含多结构（1=LV腔 2=MYO心肌 3=ATR心房）。
        # --label 选对照口径：all=并集（最严），单标签=模型真实分割目标（默认 MYO）。
        gt = (
            gt_vol[..., idx] > 0
            if label == "all"
            else gt_vol[..., idx] == float(label)
        )
        # SegmentationResult 的 mask 是 PNG 字节（mask_png），解码回数组；
        # out_size 默认与输入图一致 → 形状应与 GT 帧一致
        pred_mask = np.asarray(Image.open(BytesIO(seg.mask_png))) > 127
        if pred_mask.shape != gt.shape:
            per_frame.append({
                "frame": idx,
                "error": f"mask shape mismatch: pred={pred_mask.shape} gt={gt.shape}",
            })
            continue

        inter = int((pred_mask & gt).sum())
        union = int((pred_mask | gt).sum())
        iou = round(inter / union, 4) if union else None
        per_frame.append({
            "frame": idx,
            "iou": iou,
            "pred_px": int(pred_mask.sum()),
            "gt_px": int(gt.sum()),
            "bbox": seg.bbox,
            "mean_prob": seg.mean_prob,
            "latency_ms": seg.latency_ms,
            "elapsed_s": round(elapsed, 2),
        })
    return per_frame


def multi_patient_mode(args: argparse.Namespace) -> int:
    """多病例稳定度（seg-gt-routine）：扫描 CAMUS 前 N 个 4CH 病例，MYO 口径汇总。

    单病例 IoU 是一个点，稳定度要看分布 —— 输出 per-patient 均值/最差病例/
    达标占比，供「分割准确性优化」的验收（MYO IoU 稳定 ≥0.6）。
    """
    from pathlib import Path as _Path

    import nibabel as nib

    from app.inference.nodule_segmentation import NoduleSegmenter

    root = _Path.home() / "uai" / "data" / "camus"
    images = sorted((root / "Images").glob("*4CH_half*.nii.gz"))[: args.multi]
    if not images:
        raise SystemExit(f"CAMUS 4CH 序列没找到（root={root / 'Images'}）")
    segmenter = NoduleSegmenter.get(args.organ)

    per_patient = []
    for img_path in images:
        mask_path = root / "Masks" / img_path.name.replace("_sequence.nii.gz", "_sequence_gt.nii.gz")
        if not mask_path.exists():
            per_patient.append({"patient": img_path.stem, "error": "gt missing"})
            continue
        vol = np.asarray(nib.load(str(img_path)).dataobj)
        gt_vol = np.asarray(nib.load(str(mask_path)).dataobj)
        n = min(args.max_frames, vol.shape[-1])
        per_frame = _eval_case(segmenter, vol, gt_vol, n, args.label)
        ious = [f["iou"] for f in per_frame if isinstance(f.get("iou"), float)]
        per_patient.append({
            "patient": img_path.name.replace("_4CH_half_sequence.nii.gz", ""),
            "frames": len(ious),
            "iou_mean": round(float(np.mean(ious)), 4) if ious else None,
            "iou_min": min(ious) if ious else None,
        })
        print(f"{img_path.stem}: iou_mean={per_patient[-1]['iou_mean']}", flush=True)

    pmeans = [p["iou_mean"] for p in per_patient if p.get("iou_mean") is not None]
    summary = {
        "mode": "multi_patient",
        "label": args.label,
        "patients_requested": args.multi,
        "patients_evaluated": len(pmeans),
        "frames_per_patient": args.max_frames,
        "iou_mean": round(float(np.mean(pmeans)), 4) if pmeans else None,
        "iou_std_across_patients": round(float(np.std(pmeans)), 4) if pmeans else None,
        "iou_min_patient": min(pmeans) if pmeans else None,
        "patients_pass_0_6": sum(1 for m in pmeans if m >= 0.6),
        "per_patient": per_patient,
    }
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (out_dir / f"seg_gt_iou_multi_{args.organ}_{stamp}.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(
        {k: v for k, v in summary.items() if k != "per_patient"},
        ensure_ascii=False, indent=2))
    return 0 if pmeans and summary["iou_mean"] and summary["iou_mean"] >= 0.6 else 1


if __name__ == "__main__":
    raise SystemExit(main())
