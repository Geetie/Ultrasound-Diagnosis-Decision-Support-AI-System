"""TN3K 分割评测 - EchoCare encoder（冻结）+ FPN 轻解码器。

Recipe self-designed（EchoCare repo 无分割下游配方）：
- encoder: MONAI SwinTransformer V2（EchoCare model.pt，strict 加载，冻结）
- decoder: 三级 top-down FPN（C3→C2→C1→C0），lr 1e-3，bf16，5 epochs，bs 16，256px，/255
- loss: BCEWithLogits + Dice；eval: DSC / IoU（test 614，阈值 0.5）
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image

logger = logging.getLogger(__name__)

DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
ECHOCARE_PT = Path("/mnt/ee16/home/student03/uai/models/echocare/model.pt")
OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_seg_echocare.json")
SIZE = 256
BS = 16
EPOCHS = 5


def list_pairs(sub):
    img_dir = DATA / f"{sub}-image"
    mask_dir = DATA / f"{sub}-mask"
    pairs = []
    for p in sorted(img_dir.glob("*.jpg")):
        m = mask_dir / p.name
        if m.exists():
            pairs.append((p, m))
    return pairs


def load_img_mask(img_path, mask_path):
    try:
        img = Image.open(img_path).convert("RGB").resize((SIZE, SIZE))
        msk = Image.open(mask_path).convert("L").resize((SIZE, SIZE))
    except Exception:
        logger.warning("load_img_mask: 未预期异常，按原逻辑降级", exc_info=True)
        return None
    x = torch.from_numpy(np.asarray(img).copy()).permute(2, 0, 1).float() / 255.0
    y = (torch.from_numpy(np.asarray(msk).copy()).float() > 127).unsqueeze(0)
    return x, y


def build_encoder():
    from monai.networks.nets.swin_unetr import SwinTransformer

    state = torch.load(ECHOCARE_PT, map_location="cpu", weights_only=True)
    if hasattr(state, "state_dict"):
        state = state.state_dict()
    state.pop("mask_token", None)
    enc = SwinTransformer(
        in_chans=3, embed_dim=128, window_size=[8, 8], patch_size=[2, 2],
        depths=[2, 2, 18, 2], num_heads=[4, 8, 16, 32], mlp_ratio=4.0,
        qkv_bias=True, use_checkpoint=False, spatial_dims=2, use_v2=True,
    )
    enc.load_state_dict(state, strict=True)
    return enc


class ConvBlock(nn.Module):
    def __init__(self, cin, cout):
        super().__init__()
        self.net = nn.Sequential(
            nn.Conv2d(cin, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
            nn.Conv2d(cout, cout, 3, padding=1, bias=False),
            nn.BatchNorm2d(cout),
            nn.ReLU(inplace=True),
        )

    def forward(self, x):
        return self.net(x)


class FPNDecoder(nn.Module):
    """四段金字塔（Swin 输出 feats[1..4] = 256/512/1024/2048 @ /4/8/16/32）
    → top-down 融合 → 1ch logits。"""

    def __init__(self, dims=(256, 512, 1024, 2048), width=128, same_res=False):
        super().__init__()
        self.same_res = same_res
        c1, c2, c3, c4 = dims
        self.lat4 = nn.Conv2d(c4, width, 1)
        self.lat3 = nn.Conv2d(c3, width, 1)
        self.lat2 = nn.Conv2d(c2, width, 1)
        self.lat1 = nn.Conv2d(c1, width, 1)
        self.smooth3 = ConvBlock(width, width)
        self.smooth2 = ConvBlock(width, width)
        self.smooth1 = ConvBlock(width, width)
        self.head = nn.Conv2d(width, 1, 1)

    def forward(self, feats):
        # 兼容两种输入：SwinV2 返回 5 级（feats[0] 是 /2 的 stem，丢掉）、
        # ViT hook 版直接给 4 级
        if len(feats) == 5:
            feats = feats[1:]
        c1, c2, c3, c4 = feats  # /4 /8 /16 /32
        p4 = self.lat4(c4)
        if self.same_res:
            # ViT hook 版：四级同分辨率，只做 1x1 lateral 后逐级相加
            p3 = self.smooth3(self.lat3(c3) + p4)
            p2 = self.smooth2(self.lat2(c2) + p3)
            p1 = self.smooth1(self.lat1(c1) + p2)
        else:
            p3 = self.smooth3(self.lat3(c3) + F.interpolate(p4, scale_factor=2, mode="bilinear", align_corners=False))
            p2 = self.smooth2(self.lat2(c2) + F.interpolate(p3, scale_factor=2, mode="bilinear", align_corners=False))
            p1 = self.smooth1(self.lat1(c1) + F.interpolate(p2, scale_factor=2, mode="bilinear", align_corners=False))
        return self.head(p1)  # /4 → 上采样到全分辨率由 eval/train 做


def dice_loss(logits, target, eps=1.0):
    pred = torch.sigmoid(logits)
    inter = (pred * target).sum(dim=(2, 3))
    union = pred.sum(dim=(2, 3)) + target.sum(dim=(2, 3))
    return 1 - ((2 * inter + eps) / (union + eps)).mean()


def main():
    t0 = time.time()
    device = "cuda"
    torch.manual_seed(42)
    np.random.seed(42)

    encoder = build_encoder()
    for p in encoder.parameters():
        p.requires_grad_(False)
    encoder.to(device).eval()
    print("encoder loaded (frozen)", flush=True)

    net = FPNDecoder().to(device)
    opt = torch.optim.AdamW(net.parameters(), lr=1e-3, weight_decay=0.05)

    train_pairs = list_pairs("trainval")
    test_pairs = list_pairs("test")
    print(f"train={len(train_pairs)} test={len(test_pairs)}", flush=True)

    for epoch in range(EPOCHS):
        net.train()
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
            with torch.no_grad():
                feats = encoder(xs)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = net([f.float() for f in feats])
                logits = F.interpolate(logits, size=ys.shape[-2:], mode="bilinear", align_corners=False)
                loss = F.binary_cross_entropy_with_logits(
                    logits.float(), ys.float()
                ) + dice_loss(logits.float(), ys.float())
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss))
            if not np.isfinite(losses[-1]):
                print(f"  WARN non-finite loss at epoch{epoch} step{i}", flush=True)
                break
        print(f"  epoch{epoch} loss={np.mean(losses):.4f} ({time.time()-t0:.0f}s)", flush=True)

    # eval
    net.eval()
    dscs, ious = [], []
    with torch.no_grad():
        for i in range(0, len(test_pairs), BS):
            chunk = test_pairs[i:i + BS]
            batch = [load_img_mask(ip, mp) for ip, mp in chunk]
            batch = [b for b in batch if b is not None]
            if not batch:
                continue
            xs = torch.stack([b[0] for b in batch]).to(device)
            ysb = torch.stack([b[1] for b in batch]).to(device)
            feats = encoder(xs)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = net([f.float() for f in feats])
            logits = F.interpolate(
                logits, size=ysb.shape[-2:], mode="bilinear", align_corners=False
            ).float()
            pred = (torch.sigmoid(logits) > 0.5).float()
            inter = (pred * ysb).sum(dim=(1, 2, 3))
            card = pred.sum(dim=(1, 2, 3)) + ysb.sum(dim=(1, 2, 3))
            union = card - inter
            for di, ui in zip(inter, union):
                denom = 2 * di + ui
                dscs.append(float(2 * di / denom) if denom > 0 else 1.0)
                ious.append(float(di / ui) if ui > 0 else 1.0)

    result = {
        "protocol": "EchoCare encoder(frozen, SwinV2 official weights) + FPN decoder, BCE+Dice, 5 epochs, self-designed recipe",
        "train": len(train_pairs), "test": len(test_pairs),
        "dice_mean": round(float(np.mean(dscs)), 4),
        "dice_std": round(float(np.std(dscs)), 4),
        "iou_mean": round(float(np.mean(ious)), 4),
        "wall_seconds": round(time.time() - t0, 1),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result), flush=True)
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
