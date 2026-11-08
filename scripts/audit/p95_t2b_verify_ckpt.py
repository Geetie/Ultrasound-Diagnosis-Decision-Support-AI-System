"""独立复算 T2b 重训结果（p95）—— 只评测已保存的权重，不训练。

## 为什么需要独立复算

p92 报出 `full_lr3e6` test acc **0.9031**。但此前 p87 报的 0.2099 是**假数字**
（标签-特征错位 bug），说明这个项目的训练脚本存在"数字不可直接信"的历史。

因此对 p92 的结果做**独立复算**：用同一份权重、同一套划分，
在一个**只做推理、不含训练代码**的脚本里重算 test acc。
两个脚本给出同一个数 → 数字可信。

## 关键设计（防重蹈 p87 的坑）

- **强制 `shuffle=False`**：特征抽取与标签必须严格按同一顺序。
  实测 p87 用 `shuffle=True` 导致 79.77% 标签错位。
- **`assert` 顺序一致性**：抽完特征后校验 `order == list(range(n))`，
  不一致直接失败（结构上不可能沉默通过）。
- **test 只评一次**：不在 test 上选 epoch。

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p95_t2b_verify_ckpt.py \
        --ckpt ~/uai/models/t2b_p92/full_lr3e6.pt \
        --report reports/p95_t2b_verify.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

SPLIT_SEED = 42


# --------------------------------------------------------------------- 划分
def load_splits(data_dir: Path, videos_per_split: tuple[int, int, int] = (80, 19, 17)):
    """按**视频**划分（与 p87/p92 同一 seed=42 口径）。"""
    import csv

    rows: list[dict] = []
    for csv_name in ("train.csv", "val.csv", "test.csv"):
        p = data_dir / csv_name
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                img = r.get("image") or r.get("filename") or r.get("path")
                if not img:
                    continue
                vid = Path(img).stem.split("_")[0]
                rows.append({
                    "path": str(data_dir / img) if not Path(img).is_absolute() else img,
                    "video": vid,
                    "caption": r.get("view") or r.get("label") or r.get("caption") or "",
                })
    return rows


class _DS(Dataset):
    """返回 (图像, 类别名, 样本下标) —— 下标用于事后顺序断言。"""

    def __init__(self, rows: list[dict], preprocess, cap2idx: dict[str, int]):
        self.rows = rows
        self.preprocess = preprocess
        self.cap2idx = cap2idx

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, i: int):
        from PIL import Image

        r = self.rows[i]
        img = Image.open(r.get("filepath") or r["path"]).convert("RGB")
        return self.preprocess(img), self.cap2idx[r["caption"]], i


@torch.no_grad()
def extract(model, rows, preprocess, cap2idx, device, batch_size=512):
    """抽特征。**强制 shuffle=False**，并断言顺序一致。"""
    model.eval()
    loader = DataLoader(
        _DS(rows, preprocess, cap2idx),
        batch_size=batch_size,
        shuffle=False,          # ← 关键：不允许打乱
        num_workers=8,
        pin_memory=True,
    )
    feats, labels, order = [], [], []
    for imgs, labs, idx in loader:
        imgs = imgs.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            f = model.encode_image(imgs)
        feats.append(torch.nn.functional.normalize(f.float(), dim=-1).cpu())
        labels.extend(labs.tolist())
        order.extend(idx.tolist())
    # 顺序硬校验：不一致说明抽特征时被打乱了（p87 的根因）
    if order != list(range(len(order))):
        raise SystemExit(
            f"特征与标签顺序错位！order[:10]={order[:10]}，"
            f"应为 {list(range(min(10, len(order))))}。拒绝出数字。"
        )
    return torch.cat(feats).numpy(), np.array(labels)


def macro_f1(y_true: np.ndarray, y_pred: np.ndarray, n_cls: int) -> float:
    f1s = []
    for c in range(n_cls):
        tp = int(((y_pred == c) & (y_true == c)).sum())
        fp = int(((y_pred == c) & (y_true != c)).sum())
        fn = int(((y_pred != c) & (y_true == c)).sum())
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        f1s.append(2 * p * r / (p + r) if (p + r) else 0.0)
    return float(np.mean(f1s))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="独立复算 T2b 重训（p95）")
    ap.add_argument("--ckpt", default=str(Path.home() / "uai/models/t2b_p92/full_lr3e6.pt"))
    ap.add_argument("--data-dir", default=str(Path.home() / "uai/data/t2b_cactus"))
    ap.add_argument("--model-dir",
                    default=str(Path.home() / "uai/models/biomedclip-base-patch16-224"))
    ap.add_argument("--report", default="reports/p95_t2b_verify.json")
    ap.add_argument("--bootstrap", type=int, default=1000)
    args = ap.parse_args(argv)

    ckpt_path = Path(args.ckpt).expanduser()
    if not ckpt_path.exists():
        print(json.dumps({"probe": "p95", "status": "blocked",
                          "reason": f"权重不存在: {ckpt_path}"}, ensure_ascii=False, indent=2))
        return 1

    import open_clip

    device = "cuda"
    data_dir = Path(args.data_dir).expanduser()

    # 划分：复用 p87 的 `video_disjoint_split`（它本身**没有** bug——bug 在抽特征时
    # 用了 shuffle=True。用同一个确定性划分函数才能与 p92 的数字直接比对）。
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import importlib.util

    p87_path = Path(__file__).resolve().parent / "p87_cactus_video_disjoint.py"
    spec = importlib.util.spec_from_file_location("p87", p87_path)
    p87 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p87)

    split_pack = p87.video_disjoint_split(data_dir, seed=SPLIT_SEED)
    splits = split_pack["splits"] if "splits" in split_pack else split_pack
    meta = split_pack.get("meta", {})
    print(f"划分: train={len(splits['train'])} val={len(splits['val'])} "
          f"test={len(splits['test'])} | 泄漏检查={meta.get('leakage_check')}", flush=True)

    # 建模型 + 载权重
    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(uri, device=device)

    ckpt = torch.load(ckpt_path, map_location="cpu", weights_only=False)
    # p92 保存的是**纯 visual tower 的 state_dict**（键形如 `trunk.blocks.0...`），
    # 没有外层包装 → 直接载到 model.visual 上，不能传给整个 model。
    sd = ckpt.get("state_dict") or ckpt.get("model") or ckpt
    missing, unexpected = model.visual.load_state_dict(sd, strict=False)
    print(f"载权重 → model.visual: missing={len(missing)} unexpected={len(unexpected)}", flush=True)
    if missing:
        raise SystemExit(
            f"权重不完整：{len(missing)} 个参数缺失（前3: {missing[:3]}）。"
            "拒绝在缺权重的模型上出数字。"
        )

    # 类别表
    cap2idx: dict[str, int] = {}
    for s in ("train", "val", "test"):
        for r in splits[s]:
            if r["caption"] not in cap2idx:
                cap2idx[r["caption"]] = len(cap2idx)
    print(f"类别数: {len(cap2idx)} {list(cap2idx)}", flush=True)

    t0 = time.monotonic()
    # 特征：train 拟合 LR，test 只评一次
    Xtr, ytr = extract(model, splits["train"], preprocess_val, cap2idx, device)
    Xte, yte = extract(model, splits["test"], preprocess_val, cap2idx, device)
    print(f"特征: train={Xtr.shape} test={Xte.shape} ({time.monotonic()-t0:.0f}s)", flush=True)

    from sklearn.linear_model import LogisticRegression

    clf = LogisticRegression(max_iter=2000).fit(Xtr, ytr)
    pred = clf.predict(Xte)
    acc = float((pred == yte).mean())
    f1 = macro_f1(yte, pred, len(cap2idx))

    # bootstrap CI
    rng = np.random.default_rng(20261108)
    accs = []
    n = len(yte)
    for _ in range(args.bootstrap):
        idx = rng.integers(0, n, n)
        accs.append(float((pred[idx] == yte[idx]).mean()))
    ci = [round(float(np.percentile(accs, 2.5)), 4), round(float(np.percentile(accs, 97.5)), 4)]

    # 混淆矩阵
    cm = [[int(((pred == i) & (yte == j)).sum()) for j in range(len(cap2idx))]
          for i in range(len(cap2idx))]

    report = {
        "probe": "p95_t2b_verify_ckpt",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": (
            "独立复算 p92 报出的 test acc，用只做推理的脚本验证数字可信。"
            "必要性：p87 曾报 0.2099 但因标签错位是假数字。"
        ),
        "ckpt": str(ckpt_path),
        "split": {"seed": SPLIT_SEED,
                  "n_train": len(splits["train"]), "n_test": len(splits["test"])},
        "classes": list(cap2idx),
        "metrics": {
            "test_accuracy": round(acc, 4),
            "test_accuracy_ci95": ci,
            "macro_f1": round(f1, 4),
            "n_test": int(n),
            "bootstrap_n": args.bootstrap,
        },
        "confusion_matrix": cm,
        "order_assertion": "PASS（抽特征强制 shuffle=False，且校验 order==range(n)）",
        "wall_s": round(time.monotonic() - t0, 1),
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
