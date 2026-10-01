"""TN3K 域适应微调：AUL 微调权重 → TN3K trainval 二分类头微调 → test 评估。

基线：AUL 权重零改动的跨数据集 AUC = 0.679（t2a_tn3k_generalization.json）。
目标：域适应后 TN3K test AUC ≥ 0.80。
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
from pathlib import Path

import numpy as np
import open_clip
import pyarrow.parquet as pq
import torch
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader, Dataset

CLASSES = ["Benign", "Malignant"]
CKPT_DIR = "/mnt/ee16/home/student03/uai/models/biomedclip-base-patch16-224"
AUL_WEIGHTS = "/mnt/ee16/home/student03/uai/src/reports/t2a_finetune.pt"
TEST_PARQUET = "/tmp/t2a_test_0.parquet"
TV_ROOT = Path("/tmp/tn3k_probe")


def set_seed(seed):
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)


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


class ParquetDS(Dataset):
    def __init__(self, df, preprocess):
        self.df = df.reset_index(drop=True)
        self.preprocess = preprocess

    def __len__(self):
        return len(self.df)

    def __getitem__(self, i):
        from PIL import Image

        row = self.df.iloc[i]
        img = Image.open(io.BytesIO(row["image"]["bytes"])).convert("RGB")
        return self.preprocess(img), int(row["label"])


def make_loader(ds, bs, workers, shuffle):
    return DataLoader(ds, batch_size=bs, shuffle=shuffle,
                      num_workers=workers, pin_memory=True,
                      persistent_workers=True)

def load_trainval():
    labels = {}
    with open(TV_ROOT / "label4trainval.csv", encoding="utf-8") as f:
        for row in csv.reader(f):
            if len(row) == 2:
                labels[row[0]] = int(row[1])
    rows = []
    for name, label in labels.items():
        p = TV_ROOT / "trainval-image" / name
        if p.exists() and p.stat().st_size > 200:
            with open(p, "rb") as f:
                rows.append({"bytes": f.read(), "label": label})
    return rows


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

    train_rows = load_trainval()
    random.seed(42)
    random.shuffle(train_rows)
    k = int(len(train_rows) * 0.9)
    tr_rows, va_rows = train_rows[:k], train_rows[k:]
    te_df = pq.read_table(TEST_PARQUET).to_pandas()
    print(f"trainval={len(train_rows)} tr={len(tr_rows)} va={len(va_rows)} te={len(te_df)}", flush=True)

    tr_loader = make_loader(MemDS(tr_rows, preprocess), args.batch_size, args.workers, True)
    va_loader = make_loader(MemDS(va_rows, preprocess), args.batch_size, args.workers, False)
    te_loader = make_loader(ParquetDS(te_df, preprocess), args.batch_size, args.workers, False)

    # 基线（域适应前）
    f_tr, y_tr = extract(model, tr_loader, device)
    f_va, y_va = extract(model, va_loader, device)
    f_te, y_te = extract(model, te_loader, device)
    base_va = mal_auc(f_va, y_va)
    base_te = mal_auc(f_te, y_te)
    print(f"BASELINE(域适应前) va={base_va:.4f} test={base_te:.4f}", flush=True)

    # 二分类头微调（visual + linear head，CE）
    head = torch.nn.Linear(512, 2).to(device)
    opt = torch.optim.AdamW(
        list(head.parameters())
        + [p for p in model.visual.parameters() if p.requires_grad],
        lr=args.lr, weight_decay=0.05)
    history = []
    best_va = 0.0
    for epoch in range(args.epochs):
        model.train()
        head.train()
        tot, nb = 0.0, 0
        for imgs, yb in tr_loader:
            imgs = imgs.to(device, non_blocking=True)
            y = yb.to(device)
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
        history.append({"epoch": epoch + 1, "loss": round(tot / nb, 4),
                        "val_auc": round(va, 4), "test_auc": round(te, 4)})
        print(f"EPOCH {epoch+1}/{args.epochs} loss={tot/nb:.4f} "
              f"va={va:.4f} test={te:.4f}", flush=True)
        if va > best_va:
            best_va = va
            torch.save(model.visual.state_dict(),
                       Path(args.output).with_suffix(".pt"))
    report = {
        "baseline_va_auc": round(base_va, 4),
        "baseline_test_auc": round(base_te, 4),
        "history": history,
        "best_val": round(best_va, 4),
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE best_val=", report["best_val"], flush=True)


if __name__ == "__main__":
    main()
