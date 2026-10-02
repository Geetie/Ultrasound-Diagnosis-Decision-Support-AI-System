# -*- coding: utf-8 -*-
"""甲状腺冒烟训练（T2a 式：冻结底座特征 + LR 探针）。

双底座对比：
- EchoCare（Swin UNETR V2 encoder 120M，GMP→2048 维，CPU 推理）
- BiomedCLIP（8003 服务 /v1/embeddings/image，512 维，HTTP）

数据：TN3K 3493 张（trainval 2878 + test 614），二分类标签（0/1）。
冒烟规模默认 100 train / 60 test（分层），正式训练另开 GPU 窗口。

产出：reports/thyroid_smoke_report.json
"""
from __future__ import annotations

import argparse
import csv
import json
import random
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
OUT = Path("/mnt/ee16/home/student03/uai/reports/thyroid_smoke_report.json")
ECHOCARE_PT = Path("/mnt/ee16/home/student03/uai/models/echocare/model.pt")
CLIP_URL = "http://127.0.0.1:8003/v1/embeddings/image"

IMEAN = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
ISTD = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)


def load_labels(csv_path: Path) -> list[tuple[str, int]]:
    rows = []
    with open(csv_path, encoding="utf-8") as f:
        for name, label in csv.reader(f):
            rows.append((name, int(label)))
    return rows


def stratified_sample(
    rows: list[tuple[str, int]], n: int, seed: int
) -> list[tuple[str, int]]:
    rng = random.Random(seed)
    by_label: dict[int, list] = {}
    for name, label in rows:
        by_label.setdefault(label, []).append(name)
    per = n // len(by_label)
    picked = []
    for label, names in sorted(by_label.items()):
        rng.shuffle(names)
        picked.extend((name, label) for name in names[:per])
    rng.shuffle(picked)
    return picked


def load_image_tensor(path: Path, size: int = 256) -> torch.Tensor | None:
    try:
        img = Image.open(path).convert("RGB").resize((size, size))
    except Exception as exc:
        print(f"  [skip unreadable] {path.name}: {exc}", flush=True)
        return None
    arr = torch.from_numpy(np.asarray(img)).permute(2, 0, 1).float() / 255.0
    return (arr - IMEAN) / ISTD


class EchoCareFeatures:
    def __init__(self) -> None:
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

    def __call__(self, x: torch.Tensor) -> np.ndarray:
        with torch.no_grad():
            feats = self.encoder(x)
            last = feats[-1]  # [1, 2048, 8, 8]
            return F.adaptive_avg_pool2d(last, 1).flatten(1).numpy()[0]


def clip_feature(path: Path) -> np.ndarray:
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
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    if isinstance(data, dict):
        emb = data.get("embedding") or data.get("embeddings") or data.get("data")
        if isinstance(emb, dict):
            emb = emb.get("embedding")
        return np.asarray(emb, dtype=np.float32).reshape(-1)
    return np.asarray(data, dtype=np.float32).reshape(-1)


def extract_features(
    rows: list[tuple[str, int]],
    subdir: str,
    extractor: EchoCareFeatures,
    tag: str,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    feats_echo: list[np.ndarray] = []
    feats_clip: list[np.ndarray] = []
    labels: list[int] = []
    skipped = [0]
    t0 = time.time()
    for i, (name, label) in enumerate(rows):
        path = DATA / subdir / name
        x = load_image_tensor(path)
        if x is None:
            skipped[0] += 1
            continue
        x = x.unsqueeze(0)
        feats_echo.append(extractor(x))
        feats_clip.append(clip_feature(path))
        labels.append(label)
        if (i + 1) % 25 == 0:
            print(f"  [{tag}] {i+1}/{len(rows)} ({time.time()-t0:.0f}s)", flush=True)
    return (
        np.asarray(feats_echo, dtype=np.float32),
        np.asarray(feats_clip, dtype=np.float32),
        np.asarray(labels),
    ), skipped[0]


def eval_probe(
    train_x: np.ndarray, train_y: np.ndarray,
    test_x: np.ndarray, test_y: np.ndarray,
) -> dict:
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, balanced_accuracy_score, roc_auc_score

    clf = LogisticRegression(max_iter=2000, class_weight="balanced")
    clf.fit(train_x, train_y)
    proba = clf.predict_proba(test_x)[:, 1]
    pred = (proba >= 0.5).astype(int)
    return {
        "auc": round(float(roc_auc_score(test_y, proba)), 4),
        "accuracy": round(float(accuracy_score(test_y, pred)), 4),
        "balanced_accuracy": round(
            float(balanced_accuracy_score(test_y, pred)), 4
        ),
        "n_test": int(len(test_y)),
        "label_dist": dict(Counter(test_y.tolist())),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-train", type=int, default=100)
    ap.add_argument("--n-test", type=int, default=60)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    all_rows = load_labels(DATA / "label4trainval.csv")
    train_rows = stratified_sample(all_rows, args.n_train, args.seed)
    # P1-6 note: test-image 目前是 LFS 指针文件（未解引用），改用 trainval
    # 内部划分——先取 train，再从剩余行抽 test，保证不相交。
    train_names = {n for n, _ in train_rows}
    remaining = [r for r in all_rows if r[0] not in train_names]
    test_rows = stratified_sample(remaining, args.n_test, args.seed + 1)
    print(f"train={len(train_rows)} test={len(test_rows)}", flush=True)
    print("train dist:", dict(Counter(l for _, l in train_rows)), flush=True)

    print("== feature extraction (EchoCare CPU + BiomedCLIP HTTP) ==", flush=True)
    extractor = EchoCareFeatures()
    (tr_echo, tr_clip, tr_y), n_skip_tr = extract_features(
        train_rows, "trainval-image", extractor, "train"
    )
    (te_echo, te_clip, te_y), n_skip_te = extract_features(
        test_rows, "trainval-image", extractor, "test"
    )
    print(f"skipped unreadable: train={n_skip_tr} test={n_skip_te}", flush=True)
    print(f"echo dims={tr_echo.shape[1]} clip dims={tr_clip.shape[1]}", flush=True)

    print("== LR probes ==", flush=True)
    report = {
        "dataset": "TN3K",
        "n_train": len(train_rows),
        "n_test": len(test_rows),
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
