"""TN3K finetune v2 - BiomedCLIP / ULC visual towers (CE, official split, 3 seeds).

Recipe: freeze text tower (unused), finetune visual + linear head,
AdamW lr 5e-6 (visual) / 1e-3 (head), bf16 autocast, 4 epochs, bs 32.
Recipe is self-designed (no official downstream recipe exists for either);
documented in A.19.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from PIL import Image
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score, roc_auc_score, roc_curve

logger = logging.getLogger(__name__)

M = Path("/mnt/ee16/home/student03/uai/models")
DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
OUT = Path("/mnt/ee16/home/student03/uai/reports/tn3k_ft_v2.json")

IMEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
ISTD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def load_rows(sub):
    import csv
    rows = []
    with open(DATA / f"label4{'trainval' if 'train' in sub else 'test'}.csv", encoding="utf-8") as f:
        for name, label in csv.reader(f):
            p = DATA / sub / name
            if p.exists():
                rows.append((name, int(label), p))
    return rows


def load_image(path, size=224):
    try:
        img = Image.open(path).convert("RGB").resize((size, size))
    except Exception:
        logger.warning("load_image: 未预期异常，按原逻辑降级", exc_info=True)
        return None
    arr = torch.from_numpy(np.asarray(img).copy()).permute(2, 0, 1).float() / 255.0
    return (arr - IMEAN) / ISTD


def build(model_kind: str):
    import open_clip

    model, _, _ = open_clip.create_model_and_transforms(
        "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
    )
    if model_kind == "ulc":
        sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                        map_location="cpu", weights_only=True)
        usd = sd.get("state_dict", sd)
        vsd = {k.replace("base_clip.visual.", "", 1): v
               for k, v in usd.items() if k.startswith("base_clip.visual.")}
        model.visual.load_state_dict(vsd, strict=False)
    head = nn.Linear(512, 2)
    device = "cuda" if torch.cuda.is_available() else "cpu"
    model.visual.to(device)
    head.to(device)
    return model, head, device

def run_variant(model_kind: str, seed: int, epochs: int = 4, bs: int = 32):
    torch.manual_seed(seed)
    np.random.seed(seed)
    model, head, device = build(model_kind)

    def rows(sub):
        import csv
        out = []
        with open(DATA / f"label4{'trainval' if sub == 'trainval-image' else 'test'}.csv",
                  encoding="utf-8") as f:
            for name, label in csv.reader(f):
                p = DATA / sub / name
                if p.exists():
                    out.append((name, int(label), p))
        return out

    train_rows = rows("trainval-image")
    test_rows = rows("test-image")

    params = [
        {"params": model.visual.parameters(), "lr": 5e-6},
        {"params": head.parameters(), "lr": 1e-3},
    ]
    opt = torch.optim.AdamW(params, weight_decay=0.1)
    scaler = torch.amp.GradScaler("cuda")

    def evaluate():
        model.visual.eval()
        head.eval()
        proba, ys = [], []
        with torch.no_grad():
            for i in range(0, len(test_rows), bs):
                chunk = test_rows[i:i + bs]
                xs = [load_image(p) for _, _, p in chunk]
                keep = [(x, y) for x, (_, y, _) in zip(xs, chunk, strict=False) if x is not None]
                if not keep:
                    continue
                xs = torch.stack([k[0] for k in keep]).to(device)
                ys.extend([k[1] for k in keep])
                feats = F.normalize(model.visual(xs), dim=-1)
                proba.extend(F.softmax(head(feats), dim=-1)[:, 1].cpu().tolist())
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
    model.visual.train()
    head.train()
    for epoch in range(epochs):
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
            xs = torch.stack(xs).to(device)
            ys = torch.tensor(ys, device=device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                feats = F.normalize(model.visual(xs), dim=-1)
                logits = head(feats)
                loss = F.cross_entropy(logits, ys)
            opt.zero_grad()
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            losses.append(float(loss))
        print(f"  [{model_kind} seed{seed}] epoch{epoch} "
              f"loss={np.mean(losses):.4f} ({time.time()-t0:.0f}s)", flush=True)
    result = evaluate()
    model.visual.to("cpu")
    head.to("cpu")
    torch.cuda.empty_cache()
    return result


def main():
    seeds = [42, 43, 44]
    report = {"protocol": "v2 finetune (CE, visual+head, bf16, official split)",
              "generated_at": time.strftime("%Y-%m-%d %H:%M:%S")}
    for kind in ("biomedclip", "ulc"):
        runs = []
        for seed in seeds:
            r = run_variant(kind, seed)
            r["seed"] = seed
            runs.append(r)
            print(f"  [{kind} seed{seed}] {r}", flush=True)
        agg = {k: (round(float(np.mean([r[k] for r in runs])), 4),
                   round(float(np.std([r[k] for r in runs])), 4))
               for k in ("auc", "acc_youden", "balanced_acc", "f1")}
        report[f"{kind}_finetune"] = {"runs": runs, "mean_std": agg}
        print(json.dumps(agg), flush=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
