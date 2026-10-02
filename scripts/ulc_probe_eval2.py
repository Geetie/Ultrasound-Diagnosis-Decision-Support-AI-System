import os

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import csv
import json
import time
import uuid
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

M = Path("/mnt/ee16/home/student03/uai/models")
SRC = M / "biomedclip-base-patch16-224"
CACHE = Path.home() / ".cache" / "huggingface" / "hub"
REPO_DIR = CACHE / "models--microsoft--BiomedCLIP-PubMedBERT_256-vit_base_patch16_224"
SNAP = REPO_DIR / "snapshots" / "offline"
SNAP.mkdir(parents=True, exist_ok=True)
for fn in ("open_clip_config.json", "open_clip_pytorch_model.bin"):
    dst = SNAP / fn
    if not dst.exists():
        shutil.copy(SRC / fn, dst)
refs = REPO_DIR / "refs"
refs.mkdir(parents=True, exist_ok=True)
(refs / "main").write_text("offline", encoding="utf-8")

import open_clip

model, _, preprocess = open_clip.create_model_and_transforms(
    "hf-hub:microsoft/BiomedCLIP-PubMedBERT_256-vit_base_patch16_224",
)
print("open_clip model loaded offline", flush=True)

sd = torch.load(str(M / "ultrasound_clip" / "checkpoints.pt"),
                map_location="cpu", weights_only=True)
usd = sd["state_dict"] if "state_dict" in sd else sd
visual_sd = {k.replace("base_clip.visual.", "", 1): v
             for k, v in usd.items() if k.startswith("base_clip.visual.")}
sk = set(model.visual.state_dict())
mk = set(visual_sd)
print(f"visual: skeleton={len(sk)} ulc={len(mk)} "
      f"missing={len(sk-mk)} unexpected={len(mk-sk)}", flush=True)
model.visual.load_state_dict(visual_sd, strict=False)

DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
device = "cuda" if torch.cuda.is_available() else "cpu"
model.visual.to(device).eval()
print(f"device: {device}", flush=True)


def load_labels(p):
    with open(p, encoding="utf-8") as f:
        return [(n, int(l)) for n, l in csv.reader(f)]


@torch.no_grad()
def extract(rows, subdir, tag):
    feats, labels = [], []
    batch = []
    skipped = 0
    t0 = time.time()
    for i, (name, label) in enumerate(rows):
        try:
            img = Image.open(DATA / subdir / name).convert("RGB")
            x = preprocess(img)
        except Exception:
            skipped += 1
            continue
        batch.append(x)
        labels.append(label)
        if len(batch) == 32:
            f = model.visual(torch.stack(batch).to(device))
            if isinstance(f, tuple):
                f = f[0]
            feats.extend(F.normalize(f, dim=-1).cpu().numpy())
            batch = []
        if (i + 1) % 400 == 0:
            print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    if batch:
        f = model.visual(torch.stack(batch).to(device))
        if isinstance(f, tuple):
            f = f[0]
        feats.extend(F.normalize(f, dim=-1).cpu().numpy())
    print(f"  [{tag}] kept={len(labels)} skipped={skipped} "
          f"({time.time()-t0:.0f}s)", flush=True)
    return np.asarray(feats, dtype=np.float32), np.asarray(labels)


def main():
    import csv

    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import (accuracy_score, balanced_accuracy_score,
                                 roc_auc_score)

    tr_x, tr_y = extract(load_labels(DATA / "label4trainval.csv"),
                         "trainval-image", "train")
    te_x, te_y = extract(load_labels(DATA / "label4test.csv"),
                         "test-image", "test")

    clf = LogisticRegression(max_iter=3000, class_weight="balanced")
    clf.fit(tr_x, tr_y)
    proba = clf.predict_proba(te_x)[:, 1]
    pred = (proba >= 0.5).astype(int)
    report = {
        "model": "Ultrasound-CLIP (official ckpt, visual tower)",
        "n_train": int(len(tr_y)), "n_test": int(len(te_y)),
        "auc": round(float(roc_auc_score(te_y, proba)), 4),
        "accuracy": round(float(accuracy_score(te_y, pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(te_y, pred)), 4),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    out = Path("/mnt/ee16/home/student03/uai/reports/ulc_probe_report.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"-> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())