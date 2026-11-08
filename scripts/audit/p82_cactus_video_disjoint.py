"""正式评测 · CACTUS 切面分类（**视频不交集**协议）—— p81 的干净数字。

为什么必须有这个脚本（p81 的 0.9786 不能当真）
--------------------------------------------
p81 用 `~/uai/data/t2b_cactus/test.csv` 跑 8003 得到切面准确率 0.9786。但那个 test 集
是 `t2b_cactus_prep.py` **按图像**随机的（seed=42），而 CACTUS 的 29,963 张图只来自
**117 段视频**（文件名形如 `1_D14_frame_765_v2.jpg`）。实测（本脚本 `--audit-only`）：

  * 官方 test 4494 张里，**4494 张（100%）**所属视频在 train 中出现过；
  * 其中 **4215 张（93.8%）**与 train 中同视频某帧的**帧号相差 ≤5 帧**——同一视频
    30fps 连续帧肉眼几乎无差别；
  * 训练用的 visual tower 恰恰在 train 上微调过 → 「见过的视频里相邻的帧」被当成
    held-out，指标必然虚高。

因此本脚本按 **视频**划分训练/测试（同一视频的帧绝不跨侧），从头复现同一配方
（open_clip 官方管线、冻结文本塔、仅微调 visual、LR 切面探针），给出可辩护的数字。

产物与 p81 的区别
----------------
* p81 = 「部署中的 8003 在官方 test.csv 上的分数」= **受污染上界**；
* p82 = 「同配方在一组**从未参与训练的视频**上的分数」= **泛化下界估计**。

用法（服务器，GPU 空闲时；约 20~30 分钟）
----------------------------------------
    cd ~/uai/src && ~/uai/venv/bin/python scripts/audit/p82_cactus_video_disjoint.py \
        --report reports/p82_cactus_video_disjoint.json
    # 只跑污染审计（秒级，不训练）：
    ~/uai/venv/bin/python scripts/audit/p82_cactus_video_disjoint.py --audit-only
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

CLASSES = ("A4C", "PL", "PSMV", "Random", "SC")
FRAME_RX = re.compile(r"^(?P<vid>.*?)_?frame_(?P<fi>\d+)", re.IGNORECASE)
SPLIT_RATIO = {"train": 0.70, "val": 0.15, "test": 0.15}


# ------------------------------------------------------------------ 元数据
def parse_name(path: str) -> tuple[str | None, int | None]:
    m = FRAME_RX.match(Path(path).name)
    if not m:
        return None, None
    return m.group("vid"), int(m.group("fi"))


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ 污染审计
def contamination_audit(data_dir: Path) -> dict:
    """量化「官方 test 集与 train 集有多近」——p81 数字不可信的实证。"""
    tr = load_csv(data_dir / "train.csv")
    te = load_csv(data_dir / "test.csv")
    by_vid: dict[str, list[int]] = defaultdict(list)
    for r in tr:
        v, fi = parse_name(r["filepath"])
        if v is not None:
            by_vid[v].append(fi)
    for v in by_vid:
        by_vid[v].sort()

    import bisect

    def nearest(v: str, fi: int) -> int | None:
        lst = by_vid.get(v)
        if not lst:
            return None
        i = bisect.bisect_left(lst, fi)
        cand = [abs(lst[j] - fi) for j in (i - 1, i) if 0 <= j < len(lst)]
        return min(cand) if cand else None

    seen_video = 0
    tiers: Counter = Counter()
    for r in te:
        v, fi = parse_name(r["filepath"])
        if v is None:
            tiers["unparsed_name"] += 1
            continue
        if v in by_vid:
            seen_video += 1
        d = nearest(v, fi)
        if d is None:
            tiers[">200 frames (still same video unseen)"] += 1
        elif d <= 5:
            tiers["<=5 frames (near-identical)"] += 1
        elif d <= 30:
            tiers["6-30 frames (near-duplicate)"] += 1
        elif d <= 200:
            tiers["31-200 frames"] += 1
        else:
            tiers[">200 frames"] += 1
    n = len(te)
    train_vids = set(by_vid)
    test_vids = {v for v, _ in (parse_name(r["filepath"]) for r in te) if v}
    return {
        "official_test_n": n,
        "official_train_n": len(tr),
        "distinct_videos_train": len(train_vids),
        "distinct_videos_test": len(test_vids),
        "videos_in_all_csv": len(train_vids | test_vids),
        "test_images_from_train_videos": seen_video,
        "test_images_from_train_videos_pct": round(seen_video / max(1, n), 4),
        "videos_in_test_not_in_train": len(test_vids - train_vids),
        "frame_distance_tiers": dict(tiers),
        "verdict": (
            "官方 test 集与 train 集共享视频；且 %.1f%% 的测试图与训练帧相距 ≤5 帧。"
            "部署的 visual tower 在 train 上微调过 → p81 的准确率是**受污染上界**，"
            "不得当作泛化能力。"
            % (100.0 * tiers.get("<=5 frames (near-identical)", 0) / max(1, n))
        ),
    }


# ------------------------------------------------------------------ 视频划分
def video_disjoint_split(data_dir: Path, seed: int = 42) -> dict[str, list[dict]]:
    """按**视频**贪心划分（整段视频只进一侧），保证各 split 图像数与视图分布均衡。"""
    rows = load_csv(data_dir / "train.csv") + load_csv(data_dir / "test.csv") + load_csv(
        data_dir / "val.csv"
    )
    by_vid: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        v, _ = parse_name(r["filepath"])
        if v is None:
            continue  # 无帧号命名的图（约 175 张）不参与，避免脏键
        by_vid[v].append(r)

    total_per_view = Counter(r["view"] for rs in by_vid.values() for r in rs)
    vids = sorted(by_vid)
    random.Random(seed).shuffle(vids)

    assigned: dict[str, list[str]] = {"train": [], "val": [], "test": []}
    have_view = {s: Counter() for s in assigned}
    have_total = {s: 0 for s in assigned}
    grand_total = sum(total_per_view.values())

    for v in vids:
        vc = Counter(r["view"] for r in by_vid[v])
        best_s, best_gain = "train", -1e9
        for s in ("train", "val", "test"):
            target_img = SPLIT_RATIO[s] * grand_total
            img_deficit = target_img - have_total[s]
            # 视图均衡：只对「本 split 还缺得多」的视图加分
            view_gain = sum(
                c * (SPLIT_RATIO[s] * total_per_view[cls] - have_view[s][cls])
                / max(1, total_per_view[cls])
                for cls, c in vc.items()
            )
            gain = img_deficit / max(1, grand_total) * 3 + view_gain
            if gain > best_gain:
                best_gain, best_s = gain, s
        assigned[best_s].append(v)
        have_total[best_s] += sum(vc.values())
        have_view[best_s].update(vc)

    splits = {s: [r for v in vs for r in by_vid[v]] for s, vs in assigned.items()}
    meta = {
        "seed": seed,
        "n_videos": len(vids),
        "videos": {s: len(vs) for s, vs in assigned.items()},
        "images": {s: len(rs) for s, rs in splits.items()},
        "views": {s: dict(Counter(r["view"] for r in rs)) for s, rs in splits.items()},
        "leakage_check": {
            "video_overlap_train_test": len(set(assigned["train"]) & set(assigned["test"])),
            "video_overlap_train_val": len(set(assigned["train"]) & set(assigned["val"])),
        },
    }
    return {"splits": splits, "meta": meta}


# ------------------------------------------------------------------ 指标
def per_class_prf(y_true: list[str], y_pred: list[str], classes=CLASSES) -> dict:
    out: dict[str, dict] = {}
    f1s, weights = [], []
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p != c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[c] = {"precision": round(prec, 4), "recall": round(rec, 4),
                  "f1": round(f1, 4), "support": tp + fn}
        f1s.append(f1)
        weights.append(tp + fn)
    n = max(1, len(y_true))
    out["macro_avg"] = {
        "precision": round(sum(out[c]["precision"] for c in classes) / len(classes), 4),
        "recall": round(sum(out[c]["recall"] for c in classes) / len(classes), 4),
        "f1": round(sum(f1s) / len(f1s), 4), "support": n,
    }
    out["weighted_avg"] = {
        "precision": round(sum(out[c]["precision"] * w for c, w in zip(classes, weights, strict=True)) / n, 4),
        "recall": round(sum(out[c]["recall"] * w for c, w in zip(classes, weights, strict=True)) / n, 4),
        "f1": round(sum(f * w for f, w in zip(f1s, weights, strict=True)) / n, 4),
        "support": n,
    }
    return out


def confusion_matrix(y_true: list[str], y_pred: list[str], classes=CLASSES) -> dict:
    labels = (*classes, "Other")
    mat = {t: dict.fromkeys(labels, 0) for t in classes}
    for t, p in zip(y_true, y_pred, strict=True):
        mat.setdefault(t, dict.fromkeys(labels, 0))
        if p not in mat[t]:
            p = "Other"
        mat[t][p] += 1
    return mat


def bootstrap_acc(y_true: list[str], y_pred: list[str], n_boot: int, seed: int = 20261013) -> list:
    rnd = random.Random(seed)
    idx = range(len(y_true))
    accs = []
    for _ in range(n_boot):
        pick = [rnd.choice(list(idx)) for _ in idx]
        accs.append(sum(1 for i in pick if y_true[i] == y_pred[i]) / len(pick))
    accs.sort()
    return [round(accs[int(0.025 * (len(accs) - 1))], 4),
            round(accs[int(0.975 * (len(accs) - 1))], 4)]


# ------------------------------------------------------------------ 训练
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(Path.home() / "uai" / "data" / "t2b_cactus"))
    ap.add_argument("--model-dir", default=str(Path.home() / "uai" / "models" / "biomedclip-base-patch16-224"))
    ap.add_argument("--out-ckpt", default=str(Path.home() / "uai" / "models" / "t2b_videodisjoint" / "visual.pt"))
    ap.add_argument("--report", default="reports/p82_cactus_video_disjoint.json")
    ap.add_argument("--epochs", type=int, default=3)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--lr", type=float, default=3e-6)
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--audit-only", action="store_true", help="只跑污染审计，不训练")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    audit = contamination_audit(data_dir)
    print("[audit]", json.dumps(audit, ensure_ascii=False, indent=2))
    if args.audit_only:
        out = Path(args.report).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps({"probe": "p82_cactus_video_disjoint",
                                   "mode": "audit_only", "contamination": audit},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"-> {out}")
        return 0

    # ---- 延迟导入：只有真训练时才需要 torch/open_clip
    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score
    from torch.utils.data import DataLoader, Dataset

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from t2b_cactus_train import set_seed  # 复用同一随机化口径

    set_seed(args.seed)

    split_pack = video_disjoint_split(data_dir, args.seed)
    splits, meta = split_pack["splits"], split_pack["meta"]
    print("[split]", json.dumps(meta, ensure_ascii=False))
    if meta["leakage_check"]["video_overlap_train_test"] != 0:
        raise SystemExit("划分自检失败：train/test 视频有重叠")

    class _DS(Dataset):
        def __init__(self, rows, preprocess):
            self.rows, self.preprocess = rows, preprocess

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            from PIL import Image

            r = self.rows[i]
            return self.preprocess(Image.open(r["filepath"]).convert("RGB")), r["caption"]

    device = "cuda"
    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model, _, preprocess = open_clip.create_model_and_transforms(uri, device=device)
    tokenizer = open_clip.get_tokenizer(uri)

    def make_loader(rows, train: bool):
        return DataLoader(
            _DS(rows, preprocess), batch_size=args.batch_size, shuffle=train,
            num_workers=args.num_workers, pin_memory=True,
            persistent_workers=args.num_workers > 0,
        )

    loaders = {s: make_loader(splits[s], s == "train") for s in ("train", "val", "test")}

    @torch.no_grad()
    def extract(rows, loader):
        model.eval()
        feats = []
        for imgs, _caps in loader:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = model.encode_image(imgs)
            feats.append(F.normalize(f.float(), dim=-1).cpu())
        return torch.cat(feats).numpy()

    prompts = sorted({r["caption"] for rs in splits.values() for r in rs})
    with torch.no_grad():
        tok = tokenizer(prompts).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            anchors = F.normalize(model.encode_text(tok).float(), dim=-1)
    loss_fn = open_clip.ClipLoss()
    opt = torch.optim.AdamW(
        (p for n, p in model.named_parameters() if n.startswith("visual")),
        lr=args.lr, weight_decay=0.1,
    )

    t0 = time.time()
    history = []
    best = {"val_top1": -1.0}
    for epoch in range(args.epochs):
        model.train()
        tot, nb, te0 = 0.0, 0, time.perf_counter()
        for imgs, captions in loaders["train"]:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                im_f = F.normalize(model.encode_image(imgs).float(), dim=-1)
                tx_f = anchors[[prompts.index(c) for c in captions]]
                loss = loss_fn(im_f, tx_f, model.logit_scale.exp(), None)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            nb += 1
        rec = {"epoch": epoch + 1, "loss": round(tot / max(1, nb), 4),
               "epoch_s": round(time.perf_counter() - te0, 1)}
        for s in ("val", "test"):
            feats = extract(splits[s], loaders[s])
            ys = [r["view"] for r in splits[s]]
            lr = LogisticRegression(max_iter=2000)
            lr.fit(extract(splits["train"], loaders["train"]), [r["view"] for r in splits["train"]])
            rec[f"{s}_top1_clean"] = round(float(accuracy_score(ys, lr.predict(feats))), 4)
        history.append(rec)
        print(f"EPOCH {rec['epoch']}/{args.epochs} loss={rec['loss']} "
              f"val={rec['val_top1_clean']} test={rec['test_top1_clean']} "
              f"({rec['epoch_s']}s, total {time.time()-t0:.0f}s)", flush=True)
        if rec["val_top1_clean"] >= best["val_top1"]:
            best = dict(rec)
            Path(args.out_ckpt).parent.mkdir(parents=True, exist_ok=True)
            torch.save({k: v.cpu() for k, v in model.visual.state_dict().items()}, args.out_ckpt)

    # ---- 最终评估：train 拟合探针 → test（**视频不交集**）评估
    train_feats = extract(splits["train"], loaders["train"])
    lr = LogisticRegression(max_iter=2000)
    lr.fit(train_feats, [r["view"] for r in splits["train"]])
    test_feats = extract(splits["test"], loaders["test"])
    y_true = [r["view"] for r in splits["test"]]
    y_pred = [str(v) for v in lr.predict(test_feats)]
    acc = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p) / len(y_true)
    prf = per_class_prf(y_true, y_pred)
    cm = confusion_matrix(y_true, y_pred)
    ci = bootstrap_acc(y_true, y_pred, args.bootstrap)

    # 同时给：同一套权重/探针在**官方 test.csv** 上的分数（用于与 p81 对照，
    # 说明「同一模型换个切分方式差多少」）
    official = load_csv(data_dir / "test.csv")
    off_rows = [r for r in official if Path(r["filepath"]).is_file()]
    off_loader = make_loader(off_rows, False)
    off_pred = [str(v) for v in lr.predict(extract(off_rows, off_loader))]
    off_true = [r["view"] for r in off_rows]
    off_acc = sum(1 for t, p in zip(off_true, off_pred, strict=True) if t == p) / max(1, len(off_true))

    report = {
        "probe": "p82_cactus_video_disjoint",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "video_disjoint_retrain",
        "scope_note": (
            "按**视频**划分（117 段视频整段进单侧），同一配方从头重训 visual + LR 探针。"
            "这是本数据集上唯一可辩护的泛化数字：官方 test.csv 与 train 共享视频、"
            "93.8% 测试图与训练帧相距 ≤5 帧，不能当 held-out。"
        ),
        "contamination_audit": audit,
        "split": meta,
        "recipe": {
            "model": "biomedclip-base-patch16-224 (open_clip)",
            "trainable": "visual tower only（文本塔冻结）",
            "loss": "open_clip ClipLoss（切面 prompt 为锚）",
            "epochs": args.epochs, "batch_size": args.batch_size, "lr": args.lr,
            "probe": "LogisticRegression(max_iter=2000)，仅在 train 视频上拟合",
            "note": "与 t2b_cactus_train.py 同配方，仅把划分方式换成视频不交集",
        },
        "metrics_video_disjoint": {
            "n_evaluated": len(y_true),
            "class_support": dict(Counter(y_true)),
            "view_accuracy": round(acc, 4),
            "view_accuracy_ci95": ci,
            "per_class": prf,
            "confusion": cm,
        },
        "metrics_official_test_csv_same_model": {
            "n_evaluated": len(off_rows),
            "view_accuracy": round(off_acc, 4),
            "note": "同一套「视频不交集聚权重+探针」在官方 test.csv 上的分数；与 p81 "
                    "（部署模型）数值接近即说明差异主要来自**切分含泄漏**而非模型配方",
        },
        "history": history,
        "checkpoint": args.out_ckpt,
        "wall_s": round(time.time() - t0, 1),
    }
    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n[视频不交集] n={len(y_true)} 准确率={round(acc,4)} 95%CI {ci}")
    for c in CLASSES:
        s = prf[c]
        print(f"  {c:8s} P={s['precision']:.4f} R={s['recall']:.4f} F1={s['f1']:.4f} n={s['support']}")
    print(f"  宏F1={prf['macro_avg']['f1']}  加权F1={prf['weighted_avg']['f1']}")
    print(f"[同模型·官方test.csv] n={len(off_rows)} 准确率={round(off_acc,4)}")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
