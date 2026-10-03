"""TN3K 分割底座对照：BiomedCLIP ViT-B/16 编码器 + 轻解码器（对照 EchoCare 冻结臂 0.503）。

同配方可比性说明：EchoCare 是 Swin（天然 4 级金字塔），BiomedCLIP ViT 无金字塔，
故取 ViT 最后 4 个 block 的 14×14 token 网格经 1x1 lateral 融合后解码——
解码器结构、epochs、lr、数据划分与 EchoCare 冻结臂完全一致。
"""
from __future__ import annotations

import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image

sys.path.insert(0, "/mnt/ee16/home/student03/uai/src/scripts")
from tn3k_seg_echocare import (  # noqa: E402
    BS,
    FPNDecoder,
    dice_loss,
    list_pairs,
    load_img_mask,
)

M = Path("/mnt/ee16/home/student03/uai/models")
OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_seg_echocare.json")
EPOCHS = 5
GRID = 14  # 224/16


def load_vit():
    CACHE = Path.home() / ".cache" / "huggingface" / "hub"
    REPO = CACHE / "models--microsoft--BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
    SNAP = REPO / "snapshots" / "offline"
    SNAP.mkdir(parents=True, exist_ok=True)
    for fn in ("open_clip_config.json", "open_clip_pytorch_model.bin", "tokenizer.json",
               "vocab.txt", "special_tokens_map.json", "tokenizer_config.json"):
        s, d = M / "biomedclip-base-patch16-224" / fn, SNAP / fn
        if s.exists() and not d.exists():
            shutil.copy(s, d)
    (REPO / "refs").mkdir(parents=True, exist_ok=True)
    (REPO / "refs" / "main").write_text("offline", encoding="utf-8")
    import open_clip

    model, _, preprocess = open_clip.create_model_and_transforms(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    return model.visual, preprocess


class ViTGridEncoder(nn.Module):
    """冻结 ViT，取最后 4 个 block 的 token 网格（768ch, 14x14）作为 4 个 level。

    实现用 forward hook 走整条 trunk —— 不依赖 open_clip 内部属性名
    （踩坑：不同版本 trunk.patch_dropout / patch_drop 名字不一致）。
    """

    def __init__(self, visual):
        super().__init__()
        self.visual = visual
        for p in self.parameters():
            p.requires_grad_(False)
        n = len(visual.trunk.blocks)
        self.level_idx = [n - 1, n - 4, n - 7, n - 10]

    def forward(self, x):
        trunk = self.visual.trunk
        outs: dict[int, torch.Tensor] = {}
        handles = []

        def _make(idx):
            def hook(_m, _i, o):
                outs[idx] = o
            return hook

        for idx in self.level_idx:
            handles.append(trunk.blocks[idx].register_forward_hook(_make(idx)))
        try:
            trunk(x)
        finally:
            for h in handles:
                h.remove()

        feats = []
        for idx in self.level_idx:  # 由浅到深
            t = outs[idx]
            patch = t[:, 1:] if t.shape[1] == GRID * GRID + 1 else t
            b, n_tok, c = patch.shape
            if n_tok != GRID * GRID:
                continue
            feats.append(patch.transpose(1, 2).reshape(b, c, GRID, GRID).float())
        while len(feats) < 4:  # 缺层时用最深的重复
            feats.append(feats[-1])
        return feats[:4]


def main() -> int:
    device = "cuda"
    torch.manual_seed(42)
    np.random.seed(42)
    t0 = time.time()

    visual, preprocess = load_vit()
    encoder = ViTGridEncoder(visual).to(device).eval()
    width = encoder.visual.trunk.blocks[0].mlp.fc1.in_features
    decoder = FPNDecoder(dims=(width, width, width, width), same_res=True).to(device)
    opt = torch.optim.AdamW(decoder.parameters(), lr=1e-3, weight_decay=0.05)

    # 复用同一套数据与预处理（256px /255，与 EchoCare 臂一致）
    train_pairs = list_pairs("trainval")
    test_pairs = list_pairs("test")
    print(f"[biomedclip-vit] train={len(train_pairs)} test={len(test_pairs)} width={width}", flush=True)

    def to_vit_batch(xs_256):
        """256→224 复用 open_clip preprocess 的插值与归一化参数。"""
        mean = preprocess.transforms[-1].mean
        std = preprocess.transforms[-1].std
        xs = F.interpolate(xs_256, size=(224, 224), mode="bicubic", align_corners=False)
        xs = xs.clamp(0, 1)
        m = torch.tensor(mean, device=xs.device).view(1, 3, 1, 1)
        s = torch.tensor(std, device=xs.device).view(1, 3, 1, 1)
        return (xs - m) / s

    for epoch in range(EPOCHS):
        decoder.train()
        perm = np.random.permutation(len(train_pairs))
        losses = []
        for i in range(0, len(perm), BS):
            batch = []
            for j in perm[i:i + BS]:
                im = load_img_mask(*train_pairs[j])
                if im is not None:
                    batch.append(im)
            if not batch:
                continue
            xs = torch.stack([b[0] for b in batch]).to(device)
            ys = torch.stack([b[1] for b in batch]).to(device)
            with torch.no_grad():
                feats = encoder(to_vit_batch(xs))
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = decoder(feats)
            logits = F.interpolate(logits, size=ys.shape[-2:], mode="bilinear", align_corners=False)
            loss = F.binary_cross_entropy_with_logits(
                logits.float(), ys.float()
            ) + dice_loss(logits.float(), ys.float())
            opt.zero_grad()
            loss.backward()
            opt.step()
            losses.append(float(loss.detach()))
        print(f"  [biomedclip-vit] epoch{epoch} loss={np.mean(losses):.4f} ({time.time()-t0:.0f}s)", flush=True)

    decoder.eval()
    encoder.eval()
    dscs, ious = [], []
    with torch.no_grad():
        for i in range(0, len(test_pairs), BS):
            batch = [load_img_mask(*p) for p in test_pairs[i:i + BS]]
            batch = [b for b in batch if b is not None]
            if not batch:
                continue
            xs = torch.stack([b[0] for b in batch]).to(device)
            ysb = torch.stack([b[1] for b in batch]).to(device)
            feats = encoder(to_vit_batch(xs))
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = decoder(feats)
            logits = F.interpolate(logits, size=ysb.shape[-2:], mode="bilinear", align_corners=False).float()
            pred = (torch.sigmoid(logits) > 0.5).float()
            inter = (pred * ysb).sum(dim=(1, 2, 3))
            union = (pred + ysb - pred * ysb).sum(dim=(1, 2, 3))
            for di, ui in zip(inter, union):
                denom = 2 * di + ui
                dscs.append(float(2 * di / denom) if denom > 0 else 1.0)
                ious.append(float(di / ui) if ui > 0 else 1.0)

    arm = {
        "dice_mean": round(float(np.mean(dscs)), 4),
        "dice_std": round(float(np.std(dscs)), 4),
        "iou_mean": round(float(np.mean(ious)), 4),
        "epochs": EPOCHS,
        "wall_seconds": round(time.time() - t0, 1),
    }
    print(f"[biomedclip-vit] {arm}", flush=True)

    report = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    report.setdefault("arms", {})
    report["arms"]["biomedclip_vit_frozen_5ep"] = arm
    report["base_ab_conclusion"] = (
        "同配方下 BiomedCLIP ViT 编码器 ≈ EchoCare → 差距来自配方（轻解码器/短训练）而非底座；"
        "换 nnU-Net 类任务原生架构是正确路线"
        if abs(arm["dice_mean"] - 0.5027) < 0.05
        else "两底座差距显著 → 底座选型也需重估"
    )
    report["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["arms"], ensure_ascii=False), flush=True)
    print("conclusion:", report["base_ab_conclusion"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
