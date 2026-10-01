"""T2a 少样本微调实验：BiomedCLIP 视觉塔微调（open_clip 官方 API）。

不造轮子边界：模型构建/预训练权重加载/对比损失/tokenizer/预处理
全部来自 open_clip 官方库（pip open_clip_torch 3.3.0）；本脚本只做
数据组织与训练调度薄适配层。

并发设计（用户铁律）：
- DataLoader num_workers=8 多进程预取（CPU 解码与 GPU 训练重叠）
- pin_memory + persistent_workers（免每 epoch 重建 worker）
- AMP bf16 autocast（GPU 计算密度）

实验：AUL 735 张（Normal/Benign/Malignant），分层 70/15/15，
冻结文本塔只训视觉塔 → 微调前后 M-vs-rest AUC 对照（基线 0.515）。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import open_clip
import torch
import torch.nn.functional as F
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from torch.utils.data import DataLoader, Dataset

CLASSES = ["Normal", "Benign", "Malignant"]
PROMPTS = {
    "Normal": "an ultrasound image of a benign normal thyroid",
    "Benign": "an ultrasound image of a benign thyroid nodule",
    "Malignant": "an ultrasound image of a malignant thyroid cancer nodule",
}


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class AULDataset(Dataset):
    """image 路径 + label；预处理由 open_clip 官方 transform 提供。"""

    def __init__(self, samples: list[tuple[Path, int]], preprocess) -> None:
        self.samples = samples
        self.preprocess = preprocess

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, i: int):
        from PIL import Image

        path, label = self.samples[i]
        img = Image.open(path).convert("RGB")
        return self.preprocess(img), label


def stratified_split(samples, seed=42, ratios=(0.7, 0.15, 0.15)):
    by_cls: dict[int, list] = {}
    for s in samples:
        by_cls.setdefault(s[1], []).append(s)
    rng = random.Random(seed)
    train, val, test = [], [], []
    for label, items in sorted(by_cls.items()):
        rng.shuffle(items)
        n = len(items)
        n_tr = int(n * ratios[0])
        n_va = int(n * ratios[1])
        train += items[:n_tr]
        val += items[n_tr:n_tr + n_va]
        test += items[n_tr + n_va:]
    return train, val, test


def make_loader(ds, bs, workers, shuffle):
    return DataLoader(ds, batch_size=bs, shuffle=shuffle,
                      num_workers=workers, pin_memory=True,
                      persistent_workers=True)


@torch.no_grad()
def extract_features(model, loader, device):
    model.eval()
    feats, labels = [], []
    for imgs, ys in loader:
        imgs = imgs.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            f = model.encode_image(imgs)
            f = F.normalize(f.float(), dim=-1)
        feats.append(f.cpu())
        labels.append(torch.tensor(ys))
    return torch.cat(feats).numpy(), torch.cat(labels).numpy()


@torch.no_grad()
def zero_shot_auc(model, tokenizer, loader, device) -> float:
    model.eval()
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        text = tokenizer([PROMPTS[c] for c in CLASSES]).to(device)
        tf = F.normalize(model.encode_text(text).float(), dim=-1)
    feats, labels = extract_features(model, loader, device)
    sim = feats @ tf.float().cpu().numpy().T
    p_mal = sim[:, 2] - sim[:, 0]
    y = (labels == 2).astype(int)
    return roc_auc_score(y, p_mal)


def probe_auc(feats, labels, seed=42) -> float:
    clf = LogisticRegression(max_iter=2000, random_state=seed)
    clf.fit(feats, labels)
    p = clf.predict_proba(feats)[:, 2]
    return roc_auc_score((labels == 2).astype(int), p)


def run_baseline(model, tokenizer, loaders, device) -> dict:
    tr, va, te = loaders
    f_tr, y_tr = extract_features(model, tr, device)
    f_va, y_va = extract_features(model, va, device)
    f_te, y_te = extract_features(model, te, device)
    all_f = np.vstack([f_tr, f_va, f_te])
    all_y = np.concatenate([y_tr, y_va, y_te])
    return {
        "probe_auc_all": round(probe_auc(all_f, all_y), 4),
        "probe_auc_test": round(probe_auc(f_te, y_te), 4),
        "zeroshot_auc_test": round(zero_shot_auc(model, tokenizer, te, device), 4),
        "features": (f_tr, y_tr, f_va, y_va, f_te, y_te),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ckpt", default="/mnt/ee16/home/student03/uai/models/biomedclip-base-patch16-224")
    ap.add_argument("--data-dir", default="/mnt/ee16/home/student03/uai/data/aul")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--output", default="/mnt/ee16/home/student03/uai/src/reports/t2a_finetune.json")
    args = ap.parse_args()
    set_seed(42)
    device = "cuda"

    # 1) 官方库加载（local-dir: schema——与 8003 嵌入服务同款，零网络）
    uri = "local-dir:" + args.ckpt
    model, _, preprocess = open_clip.create_model_and_transforms(uri, device=device)
    tokenizer = open_clip.get_tokenizer(uri)

    # 2) 数据（分层 70/15/15）
    samples: list[tuple[Path, int]] = []
    root = Path(args.data_dir)
    for ci, cls in enumerate(CLASSES):
        for img in sorted((root / cls / "image").glob("*")):
            if img.suffix.lower() in (".png", ".jpg", ".jpeg"):
                samples.append((img, ci))
    if len(samples) < 100:
        print(f"FATAL: only {len(samples)} images found", flush=True)
        return 2
    train, val, test = stratified_split(samples)
    print(f"samples={len(samples)} train={len(train)} "
          f"val={len(val)} test={len(test)}", flush=True)
    loaders = (
        make_loader(AULDataset(train, preprocess), args.batch_size, args.num_workers, True),
        make_loader(AULDataset(val, preprocess), args.batch_size, args.num_workers, False),
        make_loader(AULDataset(test, preprocess), args.batch_size, args.num_workers, False),
    )

    # 3) 基线（微调前）
    t0 = time.perf_counter()
    base = run_baseline(model, tokenizer, loaders, device)
    print(f"BASELINE probe_all={base['probe_auc_all']} "
          f"probe_test={base['probe_auc_test']} "
          f"zeroshot={base['zeroshot_auc_test']} "
          f"({time.perf_counter() - t0:.0f}s)", flush=True)

    # 4) 微调：锁文本塔只训视觉塔（官方 API），AMP bf16
    model.lock_text_tower()
    opt = torch.optim.AdamW(
        [p for p in model.visual.parameters() if p.requires_grad],
        lr=args.lr, weight_decay=0.1)
    loss_fn = open_clip.ClipLoss()
    history = []
    # 文本塔已锁：3 条类 prompt 特征固定，预计算一次（省每 batch 重编码）
    with torch.no_grad(), torch.autocast("cuda", dtype=torch.bfloat16):
        class_text = F.normalize(model.encode_text(tokenizer(
            [PROMPTS[c] for c in CLASSES]).to(device)).float(), dim=-1)
    class_by_label = {int(i): class_text[i] for i in range(len(CLASSES))}
    for epoch in range(args.epochs):
        model.train()
        tot, nb, te = 0.0, 0, time.perf_counter()
        for imgs, ys in loaders[0]:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                im_f = F.normalize(model.encode_image(imgs).float(), dim=-1)
                tx_f = torch.stack([class_by_label[int(y)] for y in ys])
                logit_scale = model.logit_scale.exp()
                loss = loss_fn(im_f, tx_f, logit_scale, None)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            nb += 1
        f_tr, y_tr = extract_features(model, loaders[0], device)
        f_va, y_va = extract_features(model, loaders[1], device)
        f_te, y_te = extract_features(model, loaders[2], device)
        rec = {"epoch": epoch + 1, "loss": round(tot / nb, 4),
               "train_auc": round(probe_auc(f_tr, y_tr), 4),
               "val_auc": round(probe_auc(f_va, y_va), 4),
               "test_auc": round(probe_auc(f_te, y_te), 4),
               "epoch_s": round(time.perf_counter() - te, 1)}
        history.append(rec)
        print(f"EPOCH {rec['epoch']}/{args.epochs} loss={rec['loss']} "
              f"train={rec['train_auc']} val={rec['val_auc']} "
              f"test={rec['test_auc']} ({rec['epoch_s']}s)", flush=True)
        # 保存最优（按 val）
        best = max(history, key=lambda h: h["val_auc"])
        if rec is best or rec["val_auc"] >= best["val_auc"]:
            ckpt_path = Path(args.output).with_suffix(".pt")
            torch.save(model.visual.state_dict(), ckpt_path)

    report = {
        "baseline": {k: v for k, v in base.items() if k != "features"},
        "history": history,
        "best_val": max(h["val_auc"] for h in history),
        "config": {"epochs": args.epochs, "bs": args.batch_size,
                   "workers": args.num_workers, "lr": args.lr,
                   "locked": "text_tower"},
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE best_val=", report["best_val"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
