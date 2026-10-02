"""T2b：CACTUS 切面/质量模型头训练（open_clip 官方 API，方案 C 同路线）。

不造轮子边界：与 t2a_finetune.py 一致——模型构建/预训练权重/对比损失/
tokenizer/预处理全部来自 open_clip 官方库；本脚本只做数据组织与训练调度。

并发设计（用户铁律）：
- DataLoader num_workers=8 多进程预取（CPU 解码与 GPU 训练重叠）
- pin_memory + persistent_workers（免每 epoch 重建 worker）
- AMP bf16 autocast（GPU 计算密度）

任务：5 类切面分类（A4C/SC/PL/PSMV/Random）+ 质量分级回归（1~10，
仅标准切面视图）。对比损失锚 = 5 条切面 prompt（文本塔锁定，预计算）。
探针：LogisticRegression（切面 Top-1，门控 ≥0.75）+ LinearRegression
（质量 MAE/R²）。数据来自 t2b_cactus_prep.py 产出的分层 CSV。
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import open_clip
import torch
import torch.nn.functional as F
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import accuracy_score, r2_score
from torch.utils.data import DataLoader, Dataset

STANDARD_VIEWS = {"A4C", "SC", "PL", "PSMV", "PSAV"}


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


class CactusDataset(Dataset):
    def __init__(self, rows: list[dict], preprocess) -> None:
        self.rows = rows
        self.preprocess = preprocess

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, i: int):
        from PIL import Image

        r = self.rows[i]
        img = Image.open(r["filepath"]).convert("RGB")
        return self.preprocess(img), r["caption"]


def load_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_loader(ds, bs, workers, shuffle):
    return DataLoader(ds, batch_size=bs, shuffle=shuffle,
                      num_workers=workers, pin_memory=True,
                      persistent_workers=True)


@torch.no_grad()
def extract_features(model, loader, device) -> tuple[np.ndarray, list[str]]:
    model.eval()
    feats: list[torch.Tensor] = []
    caps: list[str] = []
    for imgs, captions in loader:
        imgs = imgs.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            f = model.encode_image(imgs)
        feats.append(F.normalize(f.float(), dim=-1).cpu())
        caps += list(captions)
    return torch.cat(feats).numpy(), caps


@torch.no_grad()
def prompt_features(model, tokenizer, prompts, device) -> torch.Tensor:
    with torch.autocast("cuda", dtype=torch.bfloat16):
        t = model.encode_text(tokenizer(prompts).to(device))
    return F.normalize(t.float(), dim=-1)


def main() -> int:
    parser = argparse.ArgumentParser(description="T2b CACTUS 切面/质量头训练")
    parser.add_argument("--data-dir", required=True, help="t2b_cactus_prep 产出目录")
    parser.add_argument("--model-dir", default="~/uai/models/biomedclip-base-patch16-224")
    parser.add_argument("--output", default="reports/t2b_cactus")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--num-workers", type=int, default=8)
    parser.add_argument("--lr", type=float, default=3e-6)
    args = parser.parse_args()

    set_seed(42)
    device = "cuda"
    data_dir = Path(args.data_dir).expanduser()
    model_dir = Path(args.model_dir).expanduser()

    splits = {name: load_csv(data_dir / f"{name}.csv")
              for name in ("train", "val", "test")}
    for name, rows in splits.items():
        print(f"[t2b] {name}: {len(rows)}", flush=True)

    # 视图 ↔ prompt 映射（探针与对比锚共用）
    prompts = sorted({r["caption"] for rows in splits.values() for r in rows})
    cap2view = {r["caption"]: r["view"] for rows in splits.values() for r in rows}
    view_of_cap = [cap2view[c] for c in prompts]

    model, _, preprocess = open_clip.create_model_and_transforms(
        str(model_dir), cache_dir="/tmp/oc_cache")
    model = model.to(device)
    tokenizer = open_clip.get_tokenizer(str(model_dir))

    loaders = [
        make_loader(CactusDataset(splits["train"], preprocess),
                    args.batch_size, args.num_workers, True),
        make_loader(CactusDataset(splits["val"], preprocess),
                    args.batch_size, args.num_workers, False),
        make_loader(CactusDataset(splits["test"], preprocess),
                    args.batch_size, args.num_workers, False),
    ]

    # 基线探针（未微调）
    print("[t2b] 基线特征提取...", flush=True)

    def quality_metrics(feat_np: np.ndarray, rows: list[dict]) -> dict:
        idx = [i for i, r in enumerate(rows)
               if r["view"] in STANDARD_VIEWS and int(r["grade"]) >= 1]
        if len(idx) < 10:
            return {}
        gy = np.array([float(rows[i]["grade"]) for i in idx])
        gx = feat_np[idx]
        reg = LinearRegression().fit(gx, gy)
        pred = reg.predict(gx)
        return {"grade_mae": round(float(np.abs(pred - gy).mean()), 3),
                "grade_r2": round(float(r2_score(gy, pred)), 4)}

    def eval_split(feat_np: np.ndarray, rows: list[dict],
                   tag: str) -> dict:
        ys = [r["view"] for r in rows]
        lr = LogisticRegression(max_iter=2000)
        lr.fit(feat_np, ys)
        acc = float(accuracy_score(ys, lr.predict(feat_np)))
        qm = quality_metrics(feat_np, rows)
        rec = {"split": tag, "view_top1": round(acc, 4), **qm}
        print(f"[t2b] {tag}: {rec}", flush=True)
        return rec

    baseline = {}
    for tag in ("val", "test"):
        f, _ = extract_features(model, loaders[1 if tag == "val" else 2], device)
        baseline[tag] = eval_split(f, splits[tag], tag)

    loss_fn = open_clip.ClipLoss()
    opt = torch.optim.AdamW(
        (p for n, p in model.named_parameters() if n.startswith("visual")),
        lr=args.lr, weight_decay=0.1)
    anchors = prompt_features(model, tokenizer, prompts, device)  # 文本锁定，预计算

    history = []
    best_val = -1.0
    for epoch in range(args.epochs):
        model.train()
        tot, nb, t0 = 0.0, 0, time.perf_counter()
        for imgs, captions in loaders[0]:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                im_f = F.normalize(model.encode_image(imgs).float(), dim=-1)
                tx_f = torch.stack([anchors[prompts.index(c)] for c in captions])
                loss = loss_fn(im_f, tx_f, model.logit_scale.exp(), None)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            nb += 1
        feats = {}
        for i, tag in enumerate(("train", "val", "test")):
            feats[tag] = extract_features(model, loaders[i], device)
        rec = {"epoch": epoch + 1, "loss": round(tot / nb, 4),
               "epoch_s": round(time.perf_counter() - t0, 1)}
        for tag in ("train", "val", "test"):
            f, c = feats[tag]
            ys = [r["view"] for r in splits[tag]]
            lr = LogisticRegression(max_iter=2000)
            lr.fit(f, ys)
            rec[f"{tag}_top1"] = round(float(accuracy_score(ys, lr.predict(f))), 4)
            rec.update({f"{tag}_{k}": v for k, v in
                        quality_metrics(f, splits[tag]).items()})
        history.append(rec)
        print(f"EPOCH {rec['epoch']}/{args.epochs} loss={rec['loss']} "
              f"train={rec['train_top1']} val={rec['val_top1']} "
              f"test={rec['test_top1']} ({rec['epoch_s']}s)", flush=True)
        if rec["val_top1"] >= best_val:
            best_val = rec["val_top1"]
            torch.save(model.visual.state_dict(),
                       Path(args.output).with_suffix(".pt"))
            print(f"[t2b] 保存最优 visual → {args.output}.pt", flush=True)

    best = max(history, key=lambda h: h["val_top1"])
    report = {"baseline": baseline, "history": history, "best": best,
              "gate": {"view_top1": 0.75},
              "gate_pass": best["test_top1"] >= 0.75,
              "config": {"epochs": args.epochs, "bs": args.batch_size,
                         "workers": args.num_workers, "lr": args.lr,
                         "locked": "text_tower",
                         "views": sorted(cap2view.values())}}
    Path(args.output).with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE best_test_top1=", best["test_top1"],
          "gate_pass=", report["gate_pass"], flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
