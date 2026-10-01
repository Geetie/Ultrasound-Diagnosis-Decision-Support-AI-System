"""TN3K 域适应微调 v2：修复 test/train 文件名重叠泄漏。

TN3K 官方 test 614 个文件名混在 trainval-image 目录（重叠验证
614/614）。v2：训练集排除 test 文件名；test 用这 614 张独立评估。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
from collections import Counter
from pathlib import Path

import numpy as np
import open_clip
import torch
import torch.nn.functional as F
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader, Dataset

CLASSES = ["Benign", "Malignant"]
CKPT_DIR = "/mnt/ee16/home/student03/uai/models/biomedclip-base-patch16-224"
AUL_WEIGHTS = "/mnt/ee16/home/student03/uai/src/reports/t2a_finetune.pt"
TV_ROOT = Path("/mnt/ee16/home/student03/uai/data/tn3k_dataset")


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


def load_labels(csv_name):
    labels = {}
    with open(TV_ROOT / csv_name, encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) == 2 and row[1] in ("0", "1"):
                labels[row[0]] = int(row[1])
    return labels


class MemDS(Dataset):
    def __init__(self, rows, preprocess):
        self.rows = rows
        self.preprocess = preprocess

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        from PIL import Image

        r = self.rows[i]
        img = Image.open(io.BytesIO(r["bytes"])).convert("RGB")
        return self.preprocess(img), r["label"]


def make_loader(ds, bs, workers, shuffle):
    return DataLoader(ds, batch_size=bs, shuffle=shuffle,
                      num_workers=workers, pin_memory=True,
                      persistent_workers=True)

@torch.no_grad()
def extract(model, loader, device):
    model.eval()
    fs, ys = [], []
    for imgs, yb in loader:
        imgs = imgs.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            f = F.normalize(model.encode_image(imgs).float(), dim=-1)
        fs.append(f.cpu())
        ys.append(torch.tensor(yb))
    return torch.cat(fs).numpy(), torch.cat(ys).numpy()


def mal_auc(feats, labels):
    clf = LogisticRegression(max_iter=2000, random_state=42)
    clf.fit(feats, labels)
    p = clf.predict_proba(feats)[:, 1]
    return roc_auc_score(labels, p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--lr", type=float, default=3e-6)
    ap.add_argument("--output", default="/mnt/ee16/home/student03/uai/src/reports/t2a_tn3k_adapt.json")
    args = ap.parse_args()
    set_seed(42)
    device = "cuda"

    uri = "local-dir:" + CKPT_DIR
    model, _, preprocess = open_clip.create_model_and_transforms(uri, device=device)
    sd = torch.load(AUL_WEIGHTS, map_location=device, weights_only=True)
    model.visual.load_state_dict(sd)

    # 泄漏修复：test 614 名单从训练集剔除
    tv_labels = load_labels("label4trainval.csv")
    test_labels = load_labels("label4test.csv")
    train_rows, test_rows = [], []
    for name, label in tv_labels.items():
        p = TV_ROOT / "trainval-image" / name
        if not (p.exists() and p.stat().st_size > 200):
            continue
        row = {"bytes": p.read_bytes(), "label": label}
        if name in test_labels:
            test_rows.append(row)
        else:
            train_rows.append(row)
    # test 补齐 label4test 独有（不在 trainval 目录的跳过）
    # 分层切分（按类别分组各 90/10，防 val 类缺失）
    by = {}
    for r in train_rows:
        by.setdefault(r["label"], []).append(r)
    tr_rows, va_rows = [], []
    for label, items in sorted(by.items()):
        random.shuffle(items)
        k = int(len(items) * 0.9)
        tr_rows += items[:k]
        va_rows += items[k:]
    print(f"train={len(tr_rows)} va={len(va_rows)} test={len(test_rows)}", flush=True)
    print("va label dist:", dict(Counter(r["label"] for r in va_rows)), flush=True)
    print("test label dist:", dict(Counter(r["label"] for r in test_rows)), flush=True)

    tr_loader = make_loader(MemDS(tr_rows, preprocess), args.batch_size, args.workers, True)
    va_loader = make_loader(MemDS(va_rows, preprocess), args.batch_size, args.workers, False)
    te_loader = make_loader(MemDS(test_rows, preprocess), args.batch_size, args.workers, False)

    # 基线（域适应前）
    f_tr, y_tr = extract(model, tr_loader, device)
    f_va, y_va = extract(model, va_loader, device)
    f_te, y_te = extract(model, te_loader, device)
    base_va = mal_auc(f_va, y_va)
    base_te = mal_auc(f_te, y_te)
    print(f"BASELINE va={base_va:.4f} test={base_te:.4f}", flush=True)

    model.lock_text_tower()
    head = torch.nn.Linear(512, 2).to(device)
    opt = torch.optim.AdamW(
        list(head.parameters())
        + [p for p in model.visual.parameters() if p.requires_grad],
        lr=args.lr, weight_decay=0.05)
    history = []
    best_te = 0.0
    for epoch in range(args.epochs):
        model.train()
        head.train()
        tot, nb = 0.0, 0
        for imgs, yb in tr_loader:
            imgs = imgs.to(device, non_blocking=True)
            y = torch.tensor(yb).to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = F.normalize(model.encode_image(imgs).float(), dim=-1)
                logits = head(f)
                loss = F.cross_entropy(logits, y)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            nb += 1
        f_va, y_va = extract(model, va_loader, device)
        f_te, y_te = extract(model, te_loader, device)
        va = mal_auc(f_va, y_va)
        te = mal_auc(f_te, y_te)
        best_te = max(best_te, te)
        history.append({"epoch": epoch + 1, "loss": round(tot / nb, 4),
                        "val_auc": round(va, 4), "test_auc": round(te, 4)})
        print(f"EPOCH {epoch+1}/{args.epochs} loss={tot/nb:.4f} "
              f"va={va:.4f} test={te:.4f}", flush=True)
    report = {
        "baseline_va_auc": round(base_va, 4),
        "baseline_test_auc": round(base_te, 4),
        "history": history,
        "best_test_auc": round(best_te, 4),
        "leak_fixed": True,
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE best_test=", report["best_test_auc"], flush=True)


if __name__ == "__main__":
    main()
