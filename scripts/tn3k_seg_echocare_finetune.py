"""TN3K 分割评测 - EchoCare：两臂对比（冻结 encoder vs 全量微调 encoder）。

臂1 frozen：encoder 冻结（v1 已跑，dice 0.5027）
臂2 finetune：encoder lr 1e-5 全量微调 + decoder lr 1e-3，8 epochs
"""
from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

sys.path.insert(0, "/mnt/ee16/home/student03/uai/src")
sys.path.insert(0, "/tmp")
from tn3k_seg_echocare import (  # noqa: E402
    BS,
    FPNDecoder,
    build_encoder,
    dice_loss,
    list_pairs,
    load_img_mask,
)

logger = logging.getLogger(__name__)

OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_seg_echocare.json")
EPOCHS = 8


def run_arm(mode: str, epochs: int, lr_enc: float, lr_dec: float):
    device = "cuda"
    torch.manual_seed(42)
    np.random.seed(42)
    t0 = time.time()

    encoder = build_encoder()
    encoder.to(device)
    decoder = FPNDecoder().to(device)

    if mode == "frozen":
        for p in encoder.parameters():
            p.requires_grad_(False)
        encoder.eval()
        groups = [{"params": decoder.parameters(), "lr": lr_dec}]
    else:
        groups = [
            {"params": encoder.parameters(), "lr": lr_enc},
            {"params": decoder.parameters(), "lr": lr_dec},
        ]
    opt = torch.optim.AdamW(groups, weight_decay=0.05)

    train_pairs = list_pairs("trainval")
    test_pairs = list_pairs("test")
    print(f"[{mode}] train={len(train_pairs)} test={len(test_pairs)}", flush=True)

    for epoch in range(epochs):
        decoder.train()
        if mode != "frozen":
            encoder.train()
        perm = np.random.permutation(len(train_pairs))
        losses = []
        for i in range(0, len(perm), BS):
            batch = []
            for j in perm[i:i + BS]:
                ip, mp = train_pairs[j]
                im = load_img_mask(ip, mp)
                if im is not None:
                    batch.append(im)
            if not batch:
                continue
            xs = torch.stack([b[0] for b in batch]).to(device)
            ys = torch.stack([b[1] for b in batch]).to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                feats = encoder(xs)
                logits = decoder([f.float() for f in feats])
            logits = F.interpolate(
                logits, size=ys.shape[-2:], mode="bilinear", align_corners=False
            )
            loss = F.binary_cross_entropy_with_logits(
                logits.float(), ys.float()
            ) + dice_loss(logits.float(), ys.float())
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss.detach()))
        print(f"  [{mode}] epoch{epoch} loss={np.mean(losses):.4f} ({time.time()-t0:.0f}s)", flush=True)

    # eval
    decoder.eval()
    encoder.eval()
    dscs, ious = [], []
    with torch.no_grad():
        for i in range(0, len(test_pairs), BS):
            batch = [load_img_mask(ip, mp) for ip, mp in test_pairs[i:i + BS]]
            batch = [b for b in batch if b is not None]
            if not batch:
                continue
            xs = torch.stack([b[0] for b in batch]).to(device)
            ysb = torch.stack([b[1] for b in batch]).to(device)
            feats = encoder(xs)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = decoder([f.float() for f in feats])
            logits = F.interpolate(
                logits, size=ysb.shape[-2:], mode="bilinear", align_corners=False
            ).float()
            pred = (torch.sigmoid(logits) > 0.5).float()
            inter = (pred * ysb).sum(dim=(1, 2, 3))
            union = (pred + ysb - pred * ysb).sum(dim=(1, 2, 3))
            for di, ui in zip(inter, union):
                denom = 2 * di + ui
                dscs.append(float(2 * di / denom) if denom > 0 else 1.0)
                ious.append(float(di / ui) if ui > 0 else 1.0)
    return {
        "dice_mean": round(float(np.mean(dscs)), 4),
        "dice_std": round(float(np.std(dscs)), 4),
        "iou_mean": round(float(np.mean(ious)), 4),
        "epochs": epochs,
        "wall_seconds": round(time.time() - t0, 1),
    }


def main():
    prev = {}
    if OUT.exists():
        try:
            prev = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("main: 未预期异常，按原逻辑降级", exc_info=True)
            prev = {}
    fin = run_arm("finetune", EPOCHS, lr_enc=1e-5, lr_dec=1e-3)
    print(f"[finetune] {fin}", flush=True)
    report = {
        "protocol": "EchoCare SwinV2 encoder + FPN decoder, BCE+Dice, official TN3K split (2879/614), self-designed recipe",
        "arms": {
            "frozen_encoder_5ep": {
                "dice_mean": 0.5027, "dice_std": 0.1374,
                "iou_mean": 0.568, "epochs": 5, "wall_seconds": 265.5,
            },
            "finetune_encoder_8ep": fin,
        },
        "dataset_fix": "trainval-mask 2879 files were Git-LFS pointer stubs (129B); repaired via hf-mirror resolve + sha256 verification (scripts/tn3k_lfs_fix_trainval.py)",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["arms"], ensure_ascii=False), flush=True)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
