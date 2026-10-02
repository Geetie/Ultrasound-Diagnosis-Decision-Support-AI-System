"""TN3K full eval (official split): trainval 2878 train + test 614 official test.

Dual backbone: EchoCare (GPU-first, CPU fallback) + BiomedCLIP (8003 HTTP).
Frozen features + LR probe. Output: reports/thyroid_full_report.json
"""
from __future__ import annotations

import csv
import json
import time
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

DATA = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")
OUT = Path("/mnt/ee16/home/student03/uai/reports/thyroid_full_report.json")
ECHOCARE_PT = Path("/mnt/ee16/home/student03/uai/models/echocare/model.pt")
CLIP_URL = "http://127.0.0.1:8003/v1/embeddings/image"

IMEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
ISTD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def load_labels(csv_path):
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for name, label in csv.reader(f):
            rows.append((name, int(label)))
    return rows


def load_image_tensor(path, size=256):
    try:
        img = Image.open(path).convert("RGB").resize((size, size))
    except Exception as exc:
        print(f"  [skip] {path.name}: {str(exc)[:60]}", flush=True)
        return None
    arr = torch.from_numpy(np.asarray(img)).permute(2, 0, 1).float() / 255.0
    return (arr - IMEAN) / ISTD


class EchoCareFeatures:
    def __init__(self):
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
        self.encoder.eval()
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.encoder.to(self.device)
        print(f"EchoCare device: {self.device}", flush=True)

    def __call__(self, x):
        with torch.no_grad():
            feats = self.encoder(x.to(self.device))
            last = feats[-1]
            return F.adaptive_avg_pool2d(last, 1).flatten(1).cpu().numpy()[0]


def clip_feature(path):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="image"; filename="{path.name}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(
        CLIP_URL,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
    except Exception as exc:
        print(f"  [clip-fail] {path.name}: {str(exc)[:60]}", flush=True)
        return None
    if isinstance(data, dict):
        emb = data.get("embedding") or data.get("embeddings") or data.get("data")
        if isinstance(emb, dict):
            emb = emb.get("embedding")
        if emb is None:
            return None
        return np.asarray(emb, dtype=np.float32).reshape(-1)
    return np.asarray(data, dtype=np.float32).reshape(-1)

def extract(rows, subdir, extractor, tag):
    feats_echo, feats_clip, labels = [], [], []
    skipped = 0
    t0 = time.time()
    for i, (name, label) in enumerate(rows):
        path = DATA / subdir / name
        x = load_image_tensor(path)
        if x is None:
            skipped += 1
            continue
        fc = clip_feature(path)
        if fc is None:
            skipped += 1
            continue
        x = x.unsqueeze(0)
        feats_echo.append(extractor(x))
        feats_clip.append(fc)
        labels.append(label)
        if (i + 1) % 200 == 0:
            print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    print(f"  [{tag}] done: {len(labels)} kept, {skipped} skipped ({time.time()-t0:.0f}s)", flush=True)
    return (
        np.asarray(feats_echo, dtype=np.float32),
        np.asarray(feats_clip, dtype=np.float32),
        np.asarray(labels),
        skipped,
    )


def eval_probe(train_x, train_y, test_x, test_y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, roc_auc_score

    clf = LogisticRegression(max_iter=3000, class_weight="balanced")
    clf.fit(train_x, train_y)
    proba = clf.predict_proba(test_x)[:, 1]
    pred = (proba >= 0.5).astype(int)
    return {
        "auc": round(float(roc_auc_score(test_y, proba)), 4),
        "accuracy": round(float(accuracy_score(test_y, pred)), 4),
        "balanced_accuracy": round(float(balanced_accuracy_score(test_y, pred)), 4),
        "n_test": len(test_y),
        "label_dist": dict(Counter(test_y.tolist())),
    }


def main():
    train_rows = load_labels(DATA / "label4trainval.csv")
    test_rows = load_labels(DATA / "label4test.csv")
    print(f"trainval={len(train_rows)} test={len(test_rows)}", flush=True)

    extractor = EchoCareFeatures()
    tr_echo, tr_clip, tr_y, sk_tr = extract(train_rows, "trainval-image", extractor, "train")
    te_echo, te_clip, te_y, sk_te = extract(test_rows, "test-image", extractor, "test")
    print(f"skipped: train={sk_tr} test={sk_te}", flush=True)

    report = {
        "dataset": "TN3K official split",
        "n_train": len(tr_y),
        "n_test": len(te_y),
        "echocare": eval_probe(tr_echo, tr_y, te_echo, te_y),
        "biomedclip": eval_probe(tr_clip, tr_y, te_clip, te_y),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
