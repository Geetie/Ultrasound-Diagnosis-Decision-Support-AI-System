"""TN3K 分割：任务原生架构基线（从头训练 UNet + 增强 + 25 epochs）。

依据：底座对照实验证明轻解码器配方天花板 ~0.55，底座（EchoCare 0.503 /
BiomedCLIP ViT 0.551）不是瓶颈，故改用任务原生架构。
协议：TN3K 官方划分 2879 train / 614 test（无抽样），报告 DSC/IoU 全量。
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_seg_unet.json")
SIZE = 256
BS = 16
EPOCHS = 25
LR = 1e-3


def list_pairs(sub):
    img_dir, mask_dir = DATA / f"{sub}-image", DATA / f"{sub}-mask"
    return [(p, mask_dir / p.name) for p in sorted(img_dir.glob("*.jpg")) if (mask_dir / p.name).exists()]


def load_pair(img_path, mask_path):
    from PIL import Image

    try:
        img = Image.open(img_path).convert("RGB")
        msk = Image.open(mask_path).convert("L")
    except Exception:
        logger.warning("load_pair: 未预期异常，按原逻辑降级", exc_info=True)
        return None
    # 保持长宽比缩放 + 中心裁剪（超声图长宽比差异大，避免直接拉伸）
    w, h = img.size
    s = SIZE / max(w, h)
    nw, nh = max(SIZE, int(w * s + 0.5)), max(SIZE, int(h * s + 0.5))
    img = img.resize((nw, nh), Image.BILINEAR)
    msk = msk.resize((nw, nh), Image.NEAREST)
    left, top = (nw - SIZE) // 2, (nh - SIZE) // 2
    img = img.crop((left, top, left + SIZE, top + SIZE))
    msk = msk.crop((left, top, left + SIZE, top + SIZE))
    x = torch.from_numpy(np.asarray(img).copy()).permute(2, 0, 1).float() / 255.0
    y = (torch.from_numpy(np.asarray(msk).copy()).float() > 127).float().unsqueeze(0)
    return x, y


def augment(x, y, rng):
    if rng.random() < 0.5:
        x, y = torch.flip(x, [-1]), torch.flip(y, [-1])
    if rng.random() < 0.5:
        x, y = torch.flip(x, [-2]), torch.flip(y, [-2])
    k = int(rng.integers(0, 4))
    if k:
        x, y = torch.rot90(x, k, [-2, -1]), torch.rot90(y, k, [-2, -1])
    scale = float(rng.uniform(0.85, 1.15))
    theta = float(rng.uniform(-12, 12)) * np.pi / 180
    grid = F.affine_grid(
        torch.tensor([[np.cos(theta) / scale, -np.sin(theta) / scale, 0.0],
                      [np.sin(theta) / scale, np.cos(theta) / scale, 0.0]], dtype=torch.float32).unsqueeze(0),
        (1, 3, SIZE, SIZE), align_corners=False,
    )
    x = F.grid_sample(x.unsqueeze(0), grid, align_corners=False)[0]
    y = F.grid_sample(y.unsqueeze(0), grid, align_corners=False, mode="nearest")[0]
    if rng.random() < 0.3:
        x = (x * float(rng.uniform(0.85, 1.15))).clamp(0, 1)
    return x, y


def build_unet():
    from monai.networks.nets import UNet

    return UNet(
        spatial_dims=2, in_channels=3, out_channels=1,
        channels=(32, 64, 128, 256, 320), strides=(2, 2, 2, 2),
        num_res_units=2, dropout=0.1,
    )


def dice_bce(logits, target, eps=1.0):
    bce = F.binary_cross_entropy_with_logits(logits.float(), target.float())
    pred = torch.sigmoid(logits.float())
    inter = (pred * target).sum(dim=(2, 3))
    union = pred.sum(dim=(2, 3)) + target.sum(dim=(2, 3))
    dice = 1 - ((2 * inter + eps) / (union + eps)).mean()
    return bce + dice


def main() -> int:
    device = "cuda"
    torch.manual_seed(42)
    np.random.seed(42)
    rng = np.random.default_rng(42)
    t0 = time.time()

    net = build_unet().to(device)
    n_params = sum(p.numel() for p in net.parameters())
    print(f"UNet params: {n_params/1e6:.1f}M", flush=True)
    opt = torch.optim.AdamW(net.parameters(), lr=LR, weight_decay=0.01)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=EPOCHS)

    train_pairs = list_pairs("trainval")
    test_pairs = list_pairs("test")
    print(f"train={len(train_pairs)} test={len(test_pairs)} (official split, no sampling)", flush=True)

    best = {"dice": 0.0}
    for epoch in range(EPOCHS):
        net.train()
        perm = rng.permutation(len(train_pairs))
        losses = []
        for i in range(0, len(perm), BS):
            xs, ys = [], []
            for j in perm[i:i + BS]:
                im = load_pair(*train_pairs[j])
                if im is None:
                    continue
                im = augment(*im, rng)
                xs.append(im[0])
                ys.append(im[1])
            if not xs:
                continue
            xs = torch.stack(xs).to(device)
            ys = torch.stack(ys).to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = net(xs)
                loss = dice_bce(logits, ys)
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss.detach()))
        sched.step()

        # 每 5 epoch 评一次（评的是官方 614 全量 test，非抽样）
        if (epoch + 1) % 5 == 0 or epoch == EPOCHS - 1:
            dscs, ious = evaluate(net, test_pairs, device)
            d = float(np.mean(dscs))
            if d > best["dice"]:
                best = {"dice": d, "iou": float(np.mean(ious)), "epoch": epoch + 1}
            print(f"  epoch{epoch+1} loss={np.mean(losses):.4f} "
                  f"test_DSC={d:.4f} IoU={float(np.mean(ious)):.4f} "
                  f"({time.time()-t0:.0f}s)", flush=True)

    dscs, ious = evaluate(net, test_pairs, device)
    ckpt_dir = Path("/mnt/ee16/home/student03/uai/models/nodule_seg")
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt = ckpt_dir / "unet_tn3k.pt"
    torch.save(
        {
            "state_dict": {k: v.cpu() for k, v in net.state_dict().items()},
            "arch": "monai.UNet(spatial_dims=2, channels=(32,64,128,256,320), "
                    "strides=(2,2,2,2), num_res_units=2, dropout=0.1)",
            "input_size": SIZE,
            "train_split": "TN3K official trainval (2879)",
            "test_dice": round(float(np.mean(dscs)), 4),
            "test_iou": round(float(np.mean(ious)), 4),
            "epochs": EPOCHS,
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
        ckpt,
    )
    result = {
        "protocol": f"MONAI UNet from scratch, 25 epochs, AdamW lr1e-3 cosine, bs16, 256px, flip/rot/scale/affine+brightness aug, official TN3K split (2879/614, no sampling)",
        "params_M": round(n_params / 1e6, 1),
        "dice_mean": round(float(np.mean(dscs)), 4),
        "dice_std": round(float(np.std(dscs)), 4),
        "iou_mean": round(float(np.mean(ious)), 4),
        "best_during_training": {k: (round(v, 4) if isinstance(v, float) else v) for k, v in best.items()},
        "wall_seconds": round(time.time() - t0, 1),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    print(f"-> {OUT}")
    return 0


@torch.no_grad()
def evaluate(net, test_pairs, device):
    net.eval()
    dscs, ious = [], []
    for i in range(0, len(test_pairs), BS):
        batch = [load_pair(*p) for p in test_pairs[i:i + BS]]
        batch = [b for b in batch if b is not None]
        if not batch:
            continue
        xs = torch.stack([b[0] for b in batch]).to(device)
        ys = torch.stack([b[1] for b in batch]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            logits = net(xs)
        logits = F.interpolate(logits.float(), size=ys.shape[-2:], mode="bilinear", align_corners=False)
        pred = (torch.sigmoid(logits) > 0.5).float()
        inter = (pred * ys).sum(dim=(1, 2, 3))
        card = pred.sum(dim=(1, 2, 3)) + ys.sum(dim=(1, 2, 3))
        union = card - inter
        for di, ui, ci in zip(inter, union, card):
            dscs.append(float(2 * di / ci) if ci > 0 else 1.0)
            ious.append(float(di / ui) if ui > 0 else 1.0)
    net.train()
    return dscs, ious


if __name__ == "__main__":
    raise SystemExit(main())
