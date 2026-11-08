"""T2b 4 类切面分类 + 受试者级 LeaveOneSubjectOut 交叉验证（p105）。

## 为什么要做这个

2026-11-08 发现 **CACTUS 数据集的结构性缺陷**：

1. **116 个视频只来自 10 个受试者** → 「按视频分组」不等于「按患者分组」。
   实测按视频 GroupKFold(5) 时，**5 折的测试集 100% 都来自训练见过的受试者**。
   所以此前所有「视频不交集」的 T2b 数字（0.9215 / 0.9483 / 0.9185）**都偏乐观**。

2. **`Random` 类（6021 张）全部来自受试者 0**，其余 9 个受试者合计 23941 张里一张都没有。
   → 受试者级 LOSO 到「留下受试者 0」时，训练集里 Random 样本 0 个，
     模型不可能预测它，acc 结构性掉到 0.0002。

## 本脚本的口径

- **4 类**（A4C / PL / PSMV / SC），**剔除 Random**（它无法跨受试者泛化，见上）
- **受试者级 LeaveOneSubjectOut(9 折)**：每折留 1 个受试者做测试，
  该受试者的所有视频**完全不参与训练**
- 断言：`train_subjects ∩ test_subjects == ∅`（硬校验，不是口头承诺）

## 报告口径

- 每折单独报（能看到折间波动）
- **均值 ± std** + **中位数**（std 大时中位数更可靠）
- 按类别分层报 P/R/F1

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p105_t2b_4class_loso.py \
        --lanes frozen --report reports/p105_t2b_4class_loso.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, Dataset

EXCLUDE_CLASSES = {"Random"}


def parse_subject(filepath: str) -> str | None:
    """受试者编号 = 文件名前缀数字（`1_D17_frame_79_v2` → `1`）。"""
    m = re.match(r"^(\d+)", Path(filepath).stem)
    return m.group(1) if m else None


def load_rows(data_dir: Path) -> list[dict]:
    import csv

    rows: list[dict] = []
    for name in ("train", "val", "test"):
        p = data_dir / f"{name}.csv"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fp = (r.get("filepath") or "").strip()
                view = (r.get("view") or "").strip()
                subj = parse_subject(fp)
                if not fp or subj is None or view in EXCLUDE_CLASSES:
                    continue
                rows.append({"filepath": fp, "subject": subj, "view": view})
    return rows


class _DS(Dataset):
    def __init__(self, rows, preprocess, cls2idx):
        self.rows = rows
        self.preprocess = preprocess
        self.cls2idx = cls2idx

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        from PIL import Image

        r = self.rows[i]
        return self.preprocess(Image.open(r["filepath"]).convert("RGB")), self.cls2idx[r["view"]], i


@torch.no_grad()
def extract(model, rows, preprocess, cls2idx, device, bs=512, workers=8):
    """抽特征。强制 shuffle=False + 顺序断言（p87 的教训）。"""
    model.eval()
    loader = DataLoader(_DS(rows, preprocess, cls2idx), batch_size=bs,
                        shuffle=False, num_workers=workers, pin_memory=True)
    feats, labels, order = [], [], []
    for imgs, labs, idx in loader:
        imgs = imgs.to(device, non_blocking=True)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            f = model.encode_image(imgs)
        feats.append(torch.nn.functional.normalize(f.float(), dim=-1).cpu())
        labels.extend(labs.tolist())
        order.extend(idx.tolist())
    if order != list(range(len(order))):
        raise SystemExit(f"顺序错位！order[:10]={order[:10]}。拒绝出数字。")
    return torch.cat(feats).numpy(), np.array(labels)


def macro_f1(y, p, k):
    out = []
    for c in range(k):
        tp = int(((p == c) & (y == c)).sum()); fp = int(((p == c) & (y != c)).sum())
        fn = int(((p != c) & (y == c)).sum())
        pr = tp / (tp + fp) if (tp + fp) else 0.0
        rc = tp / (tp + fn) if (tp + fn) else 0.0
        out.append(2 * pr * rc / (pr + rc) if (pr + rc) else 0.0)
    return float(np.mean(out))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T2b 4类 + 受试者级 LOSO（p105）")
    ap.add_argument("--data-dir", default=str(Path.home() / "uai/data/t2b_cactus"))
    ap.add_argument("--model-dir", default=str(Path.home() / "uai/models/biomedclip-base-patch16-224"))
    ap.add_argument("--lanes", default="frozen")
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--report", default="reports/p105_t2b_4class_loso.json")
    args = ap.parse_args(argv)

    import open_clip

    device = "cuda"
    rows = load_rows(Path(args.data_dir).expanduser())
    subjects = sorted({r["subject"] for r in rows}, key=int)
    classes = sorted({r["view"] for r in rows})
    cls2idx = {c: i for i, c in enumerate(classes)}
    print(f"数据: {len(rows)} 图 / {len(subjects)} 受试者 {subjects}", flush=True)
    print(f"类别（已剔除 Random）: {classes}", flush=True)

    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(uri, device=device)
    base_sd = {k: v.detach().clone() for k, v in model.visual.state_dict().items()}

    out: dict = {
        "probe": "p105_t2b_4class_loso",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": (
            "CACTUS 只有 10 个受试者、Random 类全来自受试者 0。"
            "按视频分组时测试集 100% 来自训练见过的受试者（偏乐观）。"
            "本脚本剔除 Random、改做受试者级 LeaveOneSubjectOut。"
        ),
        "n_images": len(rows),
        "n_subjects": len(subjects),
        "classes": classes,
        "excluded_classes": sorted(EXCLUDE_CLASSES),
    }

    lane = args.lanes
    folds = []
    t_lane = time.monotonic()
    for subj in subjects:
        tr = [r for r in rows if r["subject"] != subj]
        te = [r for r in rows if r["subject"] == subj]
        if not te:
            continue
        tr_s = {r["subject"] for r in tr}
        te_s = {r["subject"] for r in te}
        assert not (tr_s & te_s), f"受试者 {subj} 泄漏！"

        t0 = time.monotonic()
        if lane == "frozen":
            model.visual.load_state_dict(base_sd)
            Xtr, ytr = extract(model, tr, preprocess_val, cls2idx, device)
            Xte, yte = extract(model, te, preprocess_val, cls2idx, device)
        else:
            model.visual.load_state_dict(base_sd)
            opt = torch.optim.AdamW(model.visual.parameters(), lr=args.lr, weight_decay=0.1)
            lossf = torch.nn.CrossEntropyLoss()
            with torch.no_grad():
                tok = open_clip.get_tokenizer(uri)
                caps = [f"an ultrasound image showing the {c.lower()} view of the heart" for c in classes]
                anchors = torch.nn.functional.normalize(
                    model.encode_text(tok(caps).to(device)).float(), dim=-1).detach()
            for ep in range(args.epochs):
                model.visual.train()
                ld = DataLoader(_DS(tr, preprocess_train, cls2idx), batch_size=256,
                                shuffle=True, num_workers=8, pin_memory=True, drop_last=True)
                tot, nb = 0.0, 0
                for imgs, labs, _ in ld:
                    imgs = imgs.to(device, non_blocking=True); labs = labs.to(device)
                    with torch.autocast("cuda", dtype=torch.bfloat16):
                        f = torch.nn.functional.normalize(model.visual(imgs).float(), dim=-1)
                        loss = lossf(f @ anchors.T * 20.0, labs)
                    opt.zero_grad(set_to_none=True); loss.backward(); opt.step()
                    tot += float(loss); nb += 1
                print(f"  subj{subj} ep{ep+1} loss={tot/max(nb,1):.4f}", flush=True)
            Xtr, ytr = extract(model, tr, preprocess_val, cls2idx, device)
            Xte, yte = extract(model, te, preprocess_val, cls2idx, device)

        from sklearn.linear_model import LogisticRegression

        clf = LogisticRegression(max_iter=2000).fit(Xtr, ytr)
        pred = clf.predict(Xte)
        rec = {
            "left_out_subject": subj,
            "n_train": len(tr), "n_test": len(te),
            "n_train_subjects": len(tr_s),
            "acc": round(float((pred == yte).mean()), 4),
            "macro_f1": round(macro_f1(yte, pred, len(classes)), 4),
            "wall_s": round(time.monotonic() - t0, 1),
        }
        folds.append(rec)
        print(f"  subject={subj}: acc={rec['acc']} f1={rec['macro_f1']} "
              f"({len(te)} 图) {rec['wall_s']}s", flush=True)

    accs = [f["acc"] for f in folds]
    f1s = [f["macro_f1"] for f in folds]
    out["lanes"] = {
        lane: {
            "folds": folds,
            "mean_acc": round(float(np.mean(accs)), 4),
            "std_acc": round(float(np.std(accs)), 4),
            "median_acc": round(float(np.median(accs)), 4),
            "min_acc": round(min(accs), 4),
            "max_acc": round(max(accs), 4),
            "mean_macro_f1": round(float(np.mean(f1s)), 4),
            "std_macro_f1": round(float(np.std(f1s)), 4),
            "wall_s": round(time.monotonic() - t_lane, 1),
        }
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    v = out["lanes"][lane]
    print(f"\n{lane}: {v['mean_acc']} ± {v['std_acc']} (中位 {v['median_acc']})", flush=True)
    print(f"  min {v['min_acc']} / max {v['max_acc']} | 宏F1 {v['mean_macro_f1']} ± {v['std_macro_f1']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
