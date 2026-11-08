"""T2b 视频级 GroupKFold(5) 交叉验证（p96）。

## 为什么要做这个

用户质疑「单次划分的数字可信吗？测试集够吗？」——**质疑成立**。

`ml-training-skill`（`references/biomedical.md:420`）明确规定：
> "Cross-validation: k-fold (k=5-10) is **mandatory** for small biomedical datasets;
> a single train/val/test split is **unreliable**"

我们此前（p92）是**单次 80/19/17 划分**，test 只有 17 个视频 → 正好是它说"不可靠"的情形。
p92 自己也报了视频级 CI [0.8278, 0.9959]，宽得几乎无法结论。

## 本脚本做什么

用 `sklearn.model_selection.GroupKFold(n_splits=5)`，**groups = 视频号**（同一视频整段进同一折）。
每一折：在 4/5 的视频上训练视觉塔 + LR 探针，在剩余 1/5 上评测。
最后报 **5 折的均值 ± 标准差**，而不是单次点估计。

同时跑两条 lane 做对照：
1. **frozen**：冻结 BiomedCLIP 特征 + LR（快，作为下界）
2. **finetune**：微调视觉塔（慢，作为目标）

## 纪律

- `shuffle=False` 抽特征 + `assert order == range(n)`（p87 的教训）
- 每折的 test **完全不参与**该折的训练与选 epoch
- 报图像级与**视频级 cluster** 双 CI
- 每折结果单独记录，便于看方差

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p96_t2b_groupkfold.py \
        --lanes frozen --report reports/p96_t2b_groupkfold.json
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

SEED = 42


def parse_video(filepath: str) -> str | None:
    """从文件名取视频号。

    **不能用 `stem.split("_")[0]`**（我第一版就错在这）：
    CACTUS 的文件名有 `1_D17_frame_79_v2` 和 `0_V4frame_4965` 两种形态，
    简单取第一段会把 `0`、`0_V2`、`0_V4`、`0_V5` 全并成一个 → 117 个视频塌成 10 个，
    GroupKFold 的 fold 就退化成"1~3 个视频"，结果毫无意义。

    改为复用 p87 已验证的解析（它产出的 117 个视频与官方统计一致）。
    """
    try:
        import importlib.util

        from pathlib import Path as _P

        spec = importlib.util.spec_from_file_location(
            "p87", _P(__file__).resolve().parent / "p87_cactus_video_disjoint.py")
        p87 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(p87)
        v, _ = p87.parse_name(filepath)
        return v
    except Exception:  # silent-ok: 解析失败返回 None，由调用方跳过该行
        return None


def load_all(data_dir: Path) -> list[dict]:
    import csv

    rows: list[dict] = []
    for name in ("train", "val", "test"):
        p = data_dir / f"{name}.csv"
        if not p.exists():
            continue
        with p.open(encoding="utf-8") as f:
            for r in csv.DictReader(f):
                fp = (r.get("filepath") or "").strip()
                vid = parse_video(fp)
                if not fp or vid is None:
                    continue
                rows.append({
                    "filepath": fp,
                    "video": vid,
                    "subject": parse_subject(fp),
                    "caption": (r.get("view") or "").strip(),
                })
    return rows


def parse_subject(filepath: str) -> str | None:
    """从文件名取**受试者**编号（比视频号粗一级）。

    ⚠️ **这是 2026-11-08 发现的关键事实**：CACTUS 文件名形如
    `1_D17_frame_79_v2` / `0_V4frame_4965`，**前缀数字是受试者，不是视频**。

    实测：30k 图只来自 **10 个受试者**（0~9）、116 个视频。
    也就是说「按视频分组」**不等于**「按患者分组」——
    同一受试者的多个视频会被分到不同折。

    实测后果：按视频 GroupKFold(5) 时，**5 折的测试集 100% 都来自
    「训练时见过的受试者」**（28805/28805）。所以 p96 早期报的
    0.9483 / 0.9185 都偏乐观，必须改用受试者级分组。
    """
    import re

    stem = Path(filepath).stem
    m = re.match(r"^(\d+)", stem)
    return m.group(1) if m else None


class _DS(Dataset):
    def __init__(self, rows, preprocess, cap2idx):
        self.rows = rows
        self.preprocess = preprocess
        self.cap2idx = cap2idx

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, i):
        from PIL import Image

        r = self.rows[i]
        img = Image.open(r["filepath"]).convert("RGB")
        return self.preprocess(img), self.cap2idx[r["caption"]], i


@torch.no_grad()
def extract(model, rows, preprocess, cap2idx, device, bs=512, workers=8):
    """抽特征。**强制 shuffle=False** 并断言顺序（p87 的教训）。"""
    model.eval()
    loader = DataLoader(_DS(rows, preprocess, cap2idx), batch_size=bs,
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


def prf_for_class(y, p, c):
    """单类 P/R/F1（多分类下的 one-vs-rest）。"""
    tp = int(((p == c) & (y == c)).sum())
    fp = int(((p == c) & (y != c)).sum())
    fn = int(((p != c) & (y == c)).sum())
    pr = tp / (tp + fp) if (tp + fp) else 0.0
    rc = tp / (tp + fn) if (tp + fn) else 0.0
    return {
        "precision": round(pr, 4), "recall": round(rc, 4),
        "f1": round(2 * pr * rc / (pr + rc) if (pr + rc) else 0.0, 4),
        "support": tp + fn,
    }


def stratify_by_view(pred, y, views, names):
    """按**切面**分层（1.5 要求）。

    两种口径都给，避免只看一种被误导：
      * ``per_class_full``：全量 test 上每个切面类的 one-vs-rest P/R/F1
      * ``per_view_subset``：只在「真实切面 = v」的子集内算准确率与子集宏 F1
        （回答"这个切面到底认不认得"，子集内 accuracy 就是该类 recall）
    """
    y = np.asarray(y)
    p = np.asarray(pred)
    views = np.asarray(views)
    per_class_full = {nm: prf_for_class(y, p, i) for i, nm in enumerate(names)}
    per_view_subset = {}
    for i, nm in enumerate(names):
        m = views == nm
        n = int(m.sum())
        if n == 0:
            per_view_subset[nm] = {"n": 0}
            continue
        ys, ps = y[m], p[m]
        per_view_subset[nm] = {
            "n": n,
            "accuracy": round(float((ys == ps).mean()), 4),
            "macro_f1": round(macro_f1(ys, ps, len(names)), 4),
            "confusion_top": _top_confusion(ys, ps, names),
        }
    return {"per_class_full": per_class_full, "per_view_subset": per_view_subset}


def _top_confusion(ys, ps, names, k=2):
    """子集内最高频的错分去向（看模型把该切面认成什么）。"""
    from collections import Counter

    wrong = Counter(names[int(b)] for a, b in zip(ys, ps) if a != b)
    return [{"predicted_as": w, "n": c} for w, c in wrong.most_common(k)]


def video_cluster_ci(pred, y, vids, n_boot=500, seed=SEED):
    """按视频重采样的 cluster bootstrap CI。"""
    rng = random.Random(seed)
    uniq = sorted(set(vids))
    idx_by_vid = {v: [i for i, x in enumerate(vids) if x == v] for v in uniq}
    accs = []
    for _ in range(n_boot):
        picked = [rng.choice(uniq) for _ in uniq]
        idx = [i for v in picked for i in idx_by_vid[v]]
        if idx:
            accs.append(float((pred[idx] == y[idx]).mean()))
    accs.sort()
    if not accs:
        return [None, None]
    return [round(accs[int(0.025 * len(accs))], 4), round(accs[int(0.975 * len(accs))], 4)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="T2b GroupKFold(5) 交叉验证（p96）")
    ap.add_argument("--data-dir", default=str(Path.home() / "uai/data/t2b_cactus"))
    ap.add_argument("--model-dir", default=str(Path.home() / "uai/models/biomedclip-base-patch16-224"))
    ap.add_argument("--lanes", default="frozen", help="frozen / finetune / both")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--lr", type=float, default=3e-5)
    ap.add_argument("--report", default="reports/p96_t2b_groupkfold.json")
    ap.add_argument(
        "--group-by", choices=["subject", "video"], default="subject",
        help=("分组键。**默认 subject**（受试者级，正确口径）：CACTUS 只有 10 个受试者、"
              "116 个视频，按视频分组时 100%% 的测试图来自训练见过的受试者 → 偏乐观。"
              "video 仅用于复现历史数字。"),
    )
    args = ap.parse_args(argv)

    from sklearn.model_selection import GroupKFold
    import open_clip

    device = "cuda"
    data_dir = Path(args.data_dir).expanduser()
    rows = load_all(data_dir)
    print(f"全部数据: {len(rows)} 图 / {len({r['video'] for r in rows})} 视频", flush=True)

    cap2idx: dict[str, int] = {}
    for r in rows:
        if r["caption"] not in cap2idx:
            cap2idx[r["caption"]] = len(cap2idx)
    print(f"类别: {len(cap2idx)}", flush=True)

    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(uri, device=device)
    base_sd = {k: v.detach().clone() for k, v in model.visual.state_dict().items()}

    gkey = "subject" if args.group_by == "subject" else "video"
    groups = np.array([r[gkey] for r in rows])
    n_groups = len(set(groups))
    if args.n_splits > n_groups:
        print(f"⚠️ n_splits={args.n_splits} > 组数 {n_groups}，自动降为 {n_groups}", flush=True)
        args.n_splits = n_groups
    print(f"分组键 = {gkey}，共 {n_groups} 个组", flush=True)
    gkf = GroupKFold(n_splits=args.n_splits)

    lanes = ["frozen", "finetune"] if args.lanes == "both" else [args.lanes]
    out: dict = {
        "probe": "p96_t2b_groupkfold",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("单次划分在生物医学小数据上不可靠（ml-training-skill "
                "references/biomedical.md:420 明确要求 k-fold 5-10）。"
                "本脚本用 GroupKFold(5) 按**视频**分组做交叉验证。"),
        "n_splits": args.n_splits,
        "group_by": gkey,
        "n_groups": n_groups,
        "n_images": len(rows),
        "n_videos": len({r["video"] for r in rows}),
        "lanes": {},
    }

    for lane in lanes:
        print(f"\n{'='*60}\n=== lane: {lane} ===\n{'='*60}", flush=True)
        folds = []
        oof_p, oof_y, oof_v = [], [], []  # 预测 / 真值 / 真实切面
        t_lane = time.monotonic()
        for fi, (tr_idx, te_idx) in enumerate(gkf.split(rows, groups=groups)):
            tr = [rows[i] for i in tr_idx]
            te = [rows[i] for i in te_idx]
            tr_v = {r[gkey] for r in tr}
            te_v = {r[gkey] for r in te}
            assert not (tr_v & te_v), f"fold{fi} {gkey} 重叠！"
            # 受试者级泄漏硬校验：按视频分组时必须显式确认没有受试者跨折
            if args.group_by == "video":
                tr_s = {r["subject"] for r in tr}
                te_s = {r["subject"] for r in te}
                assert tr_s & te_s, "video 分组下受试者必然重叠（这是已知偏乐观口径）"

            t0 = time.monotonic()
            if lane == "frozen":
                # 冻结：直接用基座特征
                model.visual.load_state_dict(base_sd)
                Xtr, ytr = extract(model, tr, preprocess_val, cap2idx, device)
                Xte, yte = extract(model, te, preprocess_val, cap2idx, device)
            else:
                # 微调：每折从基座重置
                model.visual.load_state_dict(base_sd)
                model.visual.train()
                opt = torch.optim.AdamW(model.visual.parameters(), lr=args.lr, weight_decay=0.1)
                lossf = torch.nn.CrossEntropyLoss()
                # 用类别锚点：文本塔编码 5 个 caption
                with torch.no_grad():
                    tok = open_clip.get_tokenizer(uri)
                    caps = list(cap2idx.keys())
                    anchors = torch.nn.functional.normalize(
                        model.encode_text(tok(caps).to(device)).float(), dim=-1)
                means = torch.nn.functional.normalize(anchors, dim=-1).detach()
                for ep in range(args.epochs):
                    model.visual.train()
                    ld = DataLoader(_DS(tr, preprocess_train, cap2idx), batch_size=256,
                                    shuffle=True, num_workers=8, pin_memory=True, drop_last=True)
                    tot, nb = 0.0, 0
                    for imgs, labs, _ in ld:
                        imgs = imgs.to(device, non_blocking=True)
                        labs = labs.to(device)
                        with torch.autocast("cuda", dtype=torch.bfloat16):
                            f = torch.nn.functional.normalize(model.visual(imgs).float(), dim=-1)
                            logits = f @ means.T * 20.0
                        loss = lossf(logits, labs)
                        opt.zero_grad(set_to_none=True)
                        loss.backward()
                        opt.step()
                        tot += float(loss); nb += 1
                    print(f"  fold{fi} ep{ep+1} loss={tot/max(nb,1):.4f}", flush=True)
                Xtr, ytr = extract(model, tr, preprocess_val, cap2idx, device)
                Xte, yte = extract(model, te, preprocess_val, cap2idx, device)

            from sklearn.linear_model import LogisticRegression

            clf = LogisticRegression(max_iter=2000).fit(Xtr, ytr)
            pred = clf.predict(Xte)
            acc = float((pred == yte).mean())
            vids = [r["video"] for r in te]
            views = [r["caption"] for r in te]   # 切面（= 标签；T2b 就是切面分类）
            rec = {
                "fold": fi,
                "n_train": len(tr), "n_test": len(te),
                "n_videos_train": len(tr_v), "n_videos_test": len(te_v),
                "acc": round(acc, 4),
                "macro_f1": round(macro_f1(yte, pred, len(cap2idx)), 4),
                "video_cluster_ci95": video_cluster_ci(pred, yte, vids),
                "wall_s": round(time.monotonic() - t0, 1),
            }
            folds.append(rec)
            oof_p.extend(pred.tolist())
            oof_y.extend(np.asarray(yte).tolist())
            oof_v.extend(views)
            print(f"  fold{fi}: acc={acc:.4f} f1={rec['macro_f1']} "
                  f"({len(te)} 图/{len(te_v)} 视频) {rec['wall_s']}s", flush=True)

        accs = [f["acc"] for f in folds]
        f1s = [f["macro_f1"] for f in folds]
        names = list(cap2idx.keys())
        pooled_acc = float((np.asarray(oof_p) == np.asarray(oof_y)).mean())
        out["lanes"][lane] = {
            "folds": folds,
            "oof_pooled": {
                "n": len(oof_y),
                "accuracy": round(pooled_acc, 4),
                "macro_f1": round(macro_f1(np.asarray(oof_y), np.asarray(oof_p),
                                           len(names)), 4),
                "note": ("把 5 折的 out-of-fold 预测拼起来算一个池化口径。"
                         "它与「5 折均值的均值」略有差别（各折 test 大小几乎相同，差别很小）。"),
                "stratified_by_view": stratify_by_view(oof_p, oof_y, oof_v, names),
            },
            "mean_acc": round(float(np.mean(accs)), 4),
            "std_acc": round(float(np.std(accs)), 4),
            "mean_macro_f1": round(float(np.mean(f1s)), 4),
            "std_macro_f1": round(float(np.std(f1s)), 4),
            "min_acc": round(min(accs), 4),
            "max_acc": round(max(accs), 4),
            "wall_s": round(time.monotonic() - t_lane, 1),
        }
        print(f"\n  {lane} 汇总: {np.mean(accs):.4f} ± {np.std(accs):.4f} "
              f"(min {min(accs):.4f} / max {max(accs):.4f})", flush=True)

    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    if rp.is_file():
        try:
            old = json.loads(rp.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            old = {}
        # 只覆盖本次跑到的 lane，其它 lane 原样保留（避免"跑 finetune 冲掉 frozen"）
        merged = old.get("lanes", {})
        merged.update(out["lanes"])
        out["lanes"] = merged
        out["lanes_present"] = sorted(merged)
        out["lanes_merged_from_existing"] = rp.name
    rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n" + json.dumps({k: v for k, v in out.items() if k != "lanes"},
                            ensure_ascii=False, indent=2))
    for lane, v in out["lanes"].items():
        print(f"{lane}: {v['mean_acc']} ± {v['std_acc']} (n={len(v['folds'])} folds)")
    print("\n=== 分层（按切面，池化 OOF）===")
    for lane, v in out["lanes"].items():
        st = v.get("oof_pooled", {}).get("stratified_by_view", {})
        if not st:
            continue
        print(f"[{lane}]")
        for nm, m in st.get("per_view_subset", {}).items():
            if m.get("n"):
                print(f"  {nm:7s} n={m['n']:5d} acc={m['accuracy']:.4f} "
                      f"subset_macroF1={m['macro_f1']:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
