"""TN3K finetune v2 - EchoCare (Swin encoder + linear head, CE, 3 seeds).

Recipe self-designed (EchoCare repo has NO downstream classification recipe):
AdamW encoder lr 1e-5 / head lr 1e-3, bf16, 5 epochs, bs 16, 256px, /255 norm.
"""
from __future__ import annotations

import csv
import json
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score, roc_curve

DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
ECHOCARE_PT = Path("/mnt/ee16/home/student03/uai/models/echocare/model.pt")
OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_ft_echocare_v2.json")


def load_rows(sub):
    rows = []
    with open(DATA / f"label4{'trainval' if 'train' in sub else 'test'}.csv",
              encoding="utf-8") as f:
        for name, label in csv.reader(f):
            p = DATA / sub / name
            if p.exists():
                rows.append((name, int(label), p))
    return rows


def load_image(path, size=256):
    try:
        img = Image.open(path).convert("RGB").resize((size, size))
    except Exception:
        return None
    arr = torch.from_numpy(np.asarray(img).copy()).permute(2, 0, 1).float() / 255.0
    return arr


class EchoCareCls(nn.Module):
    def __init__(self):
        super().__init__()
        from monai.networks.nets.swin_unetr import SwinTransformer

        state = torch.load(ECHOCARE_PT, map_location="cpu", weights_only=True)
        if hasattr(state, "state_dict"):
            state = state.state_dict()
        state.pop("mask_token", None)
        self.encoder = SwinTransformer(
            in_chans=3, embed_dim=128, window_size=[8, 8], patch_size=[2, 2],
            depths=[2, 2, 18, 2], num_heads=[4, 8, 16, 32], mlp_ratio=4.0,
            qkv_bias=True, use_checkpoint=False, spatial_dims=2, use_v2=True,
        )
        self.encoder.load_state_dict(state, strict=True)
        self.head = nn.Linear(2048, 2)

    def forward(self, x):
        feats = self.encoder(x)
        pooled = F.adaptive_avg_pool2d(feats[-1], 1).flatten(1)
        return self.head(pooled)

def run_variant(seed: int, epochs: int = 5, bs: int = 16):
    torch.manual_seed(seed)
    np.random.seed(seed)
    net = EchoCareCls().to("cuda")

    def rows(sub):
        return load_rows(sub)

    train_rows = rows("trainval-image")
    test_rows = rows("test-image")
    opt = torch.optim.AdamW([
        {"params": net.encoder.parameters(), "lr": 1e-5},
        {"params": net.head.parameters(), "lr": 1e-3},
    ], weight_decay=0.05)
    scaler = torch.amp.GradScaler("cuda")

    def evaluate():
        net.eval()
        proba, ys = [], []
        with torch.no_grad():
            for i in range(0, len(test_rows), bs):
                chunk = test_rows[i:i + bs]
                xs = [load_image(p) for _, _, p in chunk]
                keep = [(x, y) for x, (_, _, y) in zip(xs, chunk, strict=False) if x is not None]
                if not keep:
                    continue
                xs = torch.stack([k[0] for k in keep]).to("cuda")
                ys.extend([k[1] for k in keep])
                proba.extend(F.softmax(net(xs), dim=-1)[:, 1].cpu().tolist())
        ys = np.array(ys)
        proba = np.array(proba)
        auc = roc_auc_score(ys, proba)
        fpr, tpr, thr = roc_curve(ys, proba)
        youden = thr[int(np.argmax(tpr - fpr))]
        pred = (proba >= youden).astype(int)
        return {
            "auc": round(float(auc), 4),
            "acc_youden": round(float(accuracy_score(ys, pred)), 4),
            "balanced_acc": round(float(balanced_accuracy_score(ys, pred)), 4),
            "f1": round(float(f1_score(ys, pred)), 4),
        }

    t0 = time.time()
    for epoch in range(epochs):
        net.train()
        perm = np.random.permutation(len(train_rows))
        losses = []
        for i in range(0, len(perm), bs):
            idx = perm[i:i + bs]
            xs, ys = [], []
            for j in idx:
                _name, label, p = train_rows[j]
                x = load_image(p)
                if x is not None:
                    xs.append(x)
                    ys.append(label)
            if not xs:
                continue
            xs = torch.stack(xs).to("cuda")
            ys = torch.tensor(ys, device="cuda")
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = net(xs)
                loss = F.cross_entropy(logits, ys)
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            losses.append(float(loss))
        print(f"  [echocare seed{seed}] epoch{epoch} loss={np.mean(losses):.4f} "
              f"({time.time()-t0:.0f}s)", flush=True)
    result = evaluate()
    net.to("cpu")
    torch.cuda.empty_cache()
    return result


def main():
    seeds = [42, 43, 44]
    runs = []
    for seed in seeds:
        r = run_variant(seed)
        r["seed"] = seed
        runs.append(r)
        print(f"  [echocare seed{seed}] {r}", flush=True)
    agg = {k: (round(float(np.mean([r[k] for r in runs])), 4),
               round(float(np.std([r[k] for r in runs])), 4))
           for k in ("auc", "acc_youden", "balanced_acc", "f1")}
    report = {"protocol": "v2 echocare finetune (CE, encoder+head, bf16, official split, self-designed recipe)",
              "runs": runs, "mean_std": agg,
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    print(json.dumps(agg), flush=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
