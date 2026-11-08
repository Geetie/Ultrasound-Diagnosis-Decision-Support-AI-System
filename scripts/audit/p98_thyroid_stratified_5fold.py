"""T2a 甲状腺 StratifiedKFold(5) 交叉验证（p98）—— 把 0.9122 的单次点估计变成均值±标准差。

## 为什么要做这个（行动计划 1.4）

`p93_thyroid_retrain.py` 用的是**单次 80/20 图像级划分**（seed=42），
得到 `finetuned_visual_on_thyroid` AUC **0.9122**。按
`ml-training-skill`（`references/biomedical.md:420`）：

> "Cross-validation: k-fold (k=5-10) is **mandatory** for small biomedical datasets;
> a single train/val/test split is **unreliable**"

单次 0.9122 无法判断是稳定水平还是这次划分运气好 → 改 StratifiedKFold(5)。

## 为什么是 StratifiedKFold 而不是 GroupKFold

TN5000 **没有患者 ID**。已实测（2026-11-08）每份 XML 只有
`folder / filename / source / owner / size / segmented / object` 这些字段，
**没有任何患者标识**。没有分组变量，`GroupKFold` 就无从谈起 —— 只能按图像分层。

→ 因此本脚本的 AUC 是**图像级划分的乐观估计**，同一患者的多张图（若有）会跨折。
    报告里 `split_level_disclaimer` 字段固定写明这一点，任何引用都必须带上。

## 设计（与 p93 同配方，保证 apples-to-apples）

每折：
1. 用该折的 train（4/5）微调 BiomedCLIP 视觉塔 —— 类别锚点 CE、lr=1e-5、
   按**内层 val**（从该折 train 再切 15%）早停 —— 与 p93 的超参完全一致；
2. LR 探针只在该折 train 上拟合；
3. 在该折 test（1/5）上算 AUC。

对照同时跑：
- `frozen_probe`：冻结 BiomedCLIP + LR（每折同样重拟合），作为下界
- `zero_shot`：良/恶性文本 prompt 余弦差，不训练

## 分层报告（1.5 要求）

`per_image_group` 按**来源子目录**（`folder` 字段）分层 —— TN5000 的图按采集批次/
文件夹组织，这是唯一可得的"分组"信息。若 folder 只有一个值则不产生分层。

## 纪律

- 抽特征 `shuffle=False` + 硬校验顺序（p87 的教训：shuffle=True 导致 79% 标签错位）
- test 全程不参与训练与选 epoch
- 泄漏自检：train/test 的 stem 交集必须 = 0
- 恶性占比（71.5%）在不同折间由 StratifiedKFold 保证一致
- DSC/AUC 两套口径都报；不编数字，跑不出来就 blocked

## 用法（服务器，GPU）

    cd ~/uai/src && CUDA_VISIBLE_DEVICES=0 PYTHONPATH=~/uai/src ~/uai/venv/bin/python \
        scripts/audit/p98_thyroid_stratified_5fold.py \
        --report reports/p98_thyroid_stratified_5fold.json
"""
from __future__ import annotations

import argparse
import json
import random
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

CLASSES = ("Benign", "Malignant")
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
REF_P93_SINGLE_SPLIT_AUC = 0.9122  # p93 finetuned_visual_on_thyroid（单次 80/20）
REF_P90_PRODUCTION_AUC = 0.6303    # 现网 T2a 在 TN5000 全量
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20261108


# --------------------------------------------------------------------- 指标
def auc_binary(scores: list[float], labels: list[int]) -> float:
    """二分类 AUC（Mann-Whitney U / 秩和口径，与 p85/p90/p93 同实现）。"""
    pairs = sorted(zip(scores, labels), key=lambda x: x[0])
    pos = sum(1 for _, y in pairs if y == 1)
    neg = len(pairs) - pos
    if not pos or not neg:
        return float("nan")
    ranks: dict[int, float] = {}
    i = 0
    while i < len(pairs):
        j = i
        while j + 1 < len(pairs) and pairs[j + 1][0] == pairs[i][0]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    r_sum = sum(ranks[i] for i, (_, y) in enumerate(pairs) if y == 1)
    return (r_sum - pos * (pos + 1) / 2) / (pos * neg)


def bootstrap_auc(scores, labels, n_boot: int = BOOTSTRAP_N,
                  seed: int = BOOTSTRAP_SEED) -> list[float]:
    rnd = random.Random(seed)
    n = len(scores)
    out = []
    for _ in range(n_boot):
        idx = [rnd.randrange(n) for _ in range(n)]
        v = auc_binary([scores[i] for i in idx], [labels[i] for i in idx])
        if v == v:
            out.append(v)
    out.sort()
    if not out:
        return [float("nan"), float("nan")]
    return [round(out[int(0.025 * (len(out) - 1))], 4),
            round(out[int(0.975 * (len(out) - 1))], 4)]


def best_f1_threshold(scores: list[float], labels: list[int]) -> tuple[float, dict]:
    """在该折 test 上取 F1 最优阈值（仅作参考，模型选择不用它）。"""
    cands = sorted(set(scores))
    best = (0.0, {"threshold": 0.5, "f1": 0.0})
    for thr in cands:
        m = binary_metrics(scores, labels, thr)
        if m["f1"] > best[1]["f1"]:
            best = (thr, m)
    return best


def binary_metrics(scores: list[float], labels: list[int], thr: float = 0.5) -> dict:
    tp = sum(1 for s, y in zip(scores, labels) if s >= thr and y == 1)
    fp = sum(1 for s, y in zip(scores, labels) if s >= thr and y == 0)
    fn = sum(1 for s, y in zip(scores, labels) if s < thr and y == 1)
    tn = sum(1 for s, y in zip(scores, labels) if s < thr and y == 0)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    n = len(labels)
    return {
        "threshold": thr, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": round(prec, 4), "recall_sensitivity": round(rec, 4),
        "specificity": round(spec, 4), "f1": round(f1, 4),
        "accuracy": round((tp + tn) / n, 4) if n else 0.0,
    }


# --------------------------------------------------------------------- 数据
def load_tn5000(zip_path: Path, cache_dir: Path | None) -> list[dict]:
    """解析 TN5000（Pascal VOC：`<object><name>0|1</name>`，0=良性 1=恶性）。

    同时读 `folder` 字段（分层报告用）。实测该 XML **无任何患者标识**。
    """
    z = zipfile.ZipFile(zip_path)
    names = z.namelist()
    xmls = {Path(n).stem: n for n in names if n.lower().endswith(".xml")}
    imgs = {Path(n).stem: n for n in names if Path(n).suffix.lower() in EXTS}

    rows: list[dict] = []
    unparsed = 0
    folders_seen: set[str] = set()
    for stem in sorted(xmls):
        if stem not in imgs:
            continue
        try:
            node = ET.fromstring(z.read(xmls[stem]))
        except ET.ParseError:
            unparsed += 1
            continue
        folder = (node.findtext("folder") or "").strip()
        folders_seen.add(folder)
        label = None
        for obj in node.findall(".//object"):
            nm = (obj.findtext("name") or "").strip()
            if nm in ("0", "1"):
                label = int(nm)
                break
        if label is None:
            unparsed += 1
            continue
        rows.append({"stem": stem, "member": imgs[stem], "label": label,
                     "folder": folder})
    print(f"[tn5000] parsed={len(rows)} unparsed={unparsed} "
          f"labels={dict(Counter(r['label'] for r in rows))} "
          f"folders={len(folders_seen)} {sorted(folders_seen)[:6]}", flush=True)

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        for r in rows:
            dst = cache_dir / f"{r['stem']}{Path(r['member']).suffix}"
            if not dst.exists():
                dst.write_bytes(z.read(r["member"]))
            r["filepath"] = str(dst)
        print(f"[tn5000] cached → {cache_dir}", flush=True)
    else:
        for r in rows:
            r["filepath"] = None
    z.close()
    return rows


# --------------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T2a 甲状腺 StratifiedKFold(5)（p98）")
    ap.add_argument("--zip", default=str(Path.home() / "uai" / "data" / "tn5000" / "TN5000_forReview.zip"))
    ap.add_argument("--cache-dir", default=str(Path.home() / "uai" / "data" / "tn5000" / "images"))
    ap.add_argument("--model-dir",
                    default=str(Path.home() / "uai" / "models" / "biomedclip-base-patch16-224"))
    ap.add_argument("--report", default="reports/p98_thyroid_stratified_5fold.json")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--train-ratio", type=float, default=0.8, help="每折内再切内层 val 的比例基数")
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--eval-batch-size", type=int, default=256)
    ap.add_argument("--num-workers", type=int, default=16)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=0, help="冒烟：只取前 N 条")
    args = ap.parse_args(argv)

    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from torch.utils.data import DataLoader, Dataset

    def set_seed(seed: int) -> None:
        random.seed(seed)
        np.random.seed(seed)
        torch.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)

    device = "cuda"
    set_seed(args.seed)

    rows = load_tn5000(Path(args.zip).expanduser(), Path(args.cache_dir).expanduser())
    if args.limit:
        rows = rows[: args.limit]
    y_all = [r["label"] for r in rows]
    print(f"[data] n={len(rows)} labels={dict(Counter(y_all))}", flush=True)

    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model0, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(
        uri, device=device)
    model0.eval()
    tokenizer = open_clip.get_tokenizer(uri)
    base_sd = {k: v.detach().clone() for k, v in model0.visual.state_dict().items()}

    class _DS(Dataset):
        def __init__(self, rs, preprocess, mode):
            self.rs, self.preprocess, self.mode = rs, preprocess, mode

        def __len__(self):
            return len(self.rs)

        def __getitem__(self, i):
            r = self.rs[i]
            img = self.preprocess(Image.open(r["filepath"]).convert("RGB"))
            if self.mode == "index":
                return img, i
            return img, r["label"]

    def loader_for(rs, preprocess, mode, bs, shuffle):
        return DataLoader(_DS(rs, preprocess, mode), batch_size=bs, shuffle=shuffle,
                          num_workers=args.num_workers, pin_memory=True,
                          persistent_workers=args.num_workers > 0)

    @torch.no_grad()
    def extract(visual, rs) -> np.ndarray:
        """抽特征并按样本下标重排（防错位守卫，与 p92/p93 一致）。"""
        visual.eval()
        ld = loader_for(rs, preprocess_val, "index", args.eval_batch_size, False)
        feats, idxs = [], []
        for imgs, idx in ld:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = visual(imgs)
            feats.append(F.normalize(f.float(), p=2, dim=1).cpu())
            idxs += [int(i) for i in idx]
        X = torch.cat(feats).numpy()
        order = sorted(range(len(idxs)), key=lambda k: idxs[k])
        X = X[order]
        if [idxs[k] for k in order] != list(range(len(rs))):
            raise RuntimeError("特征/样本对齐失败（顺序错位）→ 拒绝出数字")
        return X

    # zero-shot 文本锚点（只算一次）
    prompts = {
        "Benign": "an ultrasound image of a benign thyroid nodule",
        "Malignant": "an ultrasound image of a malignant thyroid tumor",
    }
    with torch.no_grad():
        tok = tokenizer([prompts[c] for c in CLASSES]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            txt_np = F.normalize(model0.encode_text(tok).float(), dim=-1).cpu().numpy()
    anchors = torch.from_numpy(txt_np).to(device)

    skf = StratifiedKFold(n_splits=args.n_splits, shuffle=True, random_state=args.seed)
    idx_all = np.arange(len(rows))

    lanes = {"frozen_probe": [], "zero_shot": [], "finetuned_visual": []}
    fold_recs = []
    oof = {"frozen_probe": {}, "zero_shot": {}, "finetuned_visual": {}}
    t_all = time.monotonic()

    for fi, (tr_idx, te_idx) in enumerate(skf.split(idx_all, y_all)):
        tr_rows = [rows[i] for i in tr_idx]
        te_rows = [rows[i] for i in te_idx]
        y_tr = [r["label"] for r in tr_rows]
        y_te = [r["label"] for r in te_rows]

        # ---------------- 泄漏自检 ----------------
        ov = {r["stem"] for r in tr_rows} & {r["stem"] for r in te_rows}
        ov_path = {r["filepath"] for r in tr_rows} & {r["filepath"] for r in te_rows}
        if ov or ov_path:
            raise SystemExit(f"fold{fi} 泄漏自检失败：stem 交集 {len(ov)} 路径交集 {len(ov_path)}")
        assert len(tr_rows) + len(te_rows) == len(rows), "折之间条数不守恒"

        print(f"\n{'='*62}\n[fold {fi}] train={len(tr_rows)} test={len(te_rows)} "
              f"| 泄漏自检 stem交集=0 路径交集=0 | 分层比例 "
              f"train_mal={sum(y_tr)/len(y_tr):.4f} test_mal={sum(y_te)/len(y_te):.4f}\n{'='*62}",
              flush=True)
        t0 = time.monotonic()

        # ① frozen probe
        model0.visual.load_state_dict(base_sd)
        Xtr = extract(model0.visual, tr_rows)
        Xte = extract(model0.visual, te_rows)
        lr_f = LogisticRegression(max_iter=2000).fit(Xtr, y_tr)
        sc_f = [float(p) for p in lr_f.predict_proba(Xte)[:, 1]]

        # ② zero-shot（不训练）
        sims = Xte @ txt_np.T
        diff = [float(s[1] - s[0]) for s in sims]
        sc_z = 1 / (1 + np.exp(-np.array(diff) * 20))

        # ③ finetune 视觉塔（每折从基座重置）
        import copy as _copy

        visual = _copy.deepcopy(model0.visual)
        visual.load_state_dict(base_sd)
        for prm in visual.parameters():
            prm.requires_grad = True
        opt = torch.optim.AdamW(visual.parameters(), lr=args.lr, weight_decay=0.1)
        loss_fn = torch.nn.CrossEntropyLoss()

        # 内层 val：从该折 train 里再切 15%（分层），只用于早停
        inner = StratifiedKFold(n_splits=7, shuffle=True, random_state=args.seed + 1)
        sub_tr_i, sub_va_i = next(iter(inner.split(np.arange(len(tr_rows)), y_tr)))
        sub_tr = [tr_rows[i] for i in sub_tr_i]
        sub_va = [tr_rows[i] for i in sub_va_i]
        y_va = [r["label"] for r in sub_va]
        print(f"  inner train={len(sub_tr)} val={len(sub_va)}", flush=True)

        y_sub_tr = [r["label"] for r in sub_tr]
        best = {"auc": -1.0, "epoch": 0, "sd": None}
        stale = 0
        hist = []
        for ep in range(args.epochs):
            visual.train()
            tot, nb = 0.0, 0
            for imgs, ys in loader_for(sub_tr, preprocess_train, "label",
                                       args.batch_size, True):
                imgs, ys = imgs.to(device, non_blocking=True), ys.to(device)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    im = F.normalize(visual(imgs).float(), p=2, dim=1)
                loss = loss_fn(im @ anchors.T * 20.0, ys)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                opt.step()
                tot += float(loss)
                nb += 1
            # 内层 val AUC（留出法）：LR 只在内层 train 上拟合，在内层 val 上评
            Xs = extract(visual, sub_tr)
            Xv = extract(visual, sub_va)
            lr_v = LogisticRegression(max_iter=2000).fit(Xs, y_sub_tr)
            va_auc = auc_binary([float(p) for p in lr_v.predict_proba(Xv)[:, 1]], y_va)
            hist.append({"epoch": ep + 1, "loss": round(tot / max(1, nb), 4),
                         "inner_val_auc": round(va_auc, 4)})
            print(f"  ep{ep+1} loss={hist[-1]['loss']:.4f} inner_val_auc={va_auc:.4f}",
                  flush=True)
            if va_auc > best["auc"]:
                best = {"auc": va_auc, "epoch": ep + 1,
                        "sd": {k: v.detach().cpu().clone()
                               for k, v in visual.state_dict().items()}}
                stale = 0
            else:
                stale += 1
                if stale >= args.patience:
                    print(f"  [early-stop] 内层 val 连续 {stale} 轮无提升", flush=True)
                    break

        visual.load_state_dict({k: v.to(device) for k, v in best["sd"].items()})
        visual.eval()
        Xtr_ft = extract(visual, sub_tr)
        Xte_ft = extract(visual, te_rows)
        lr_ft = LogisticRegression(max_iter=2000).fit(Xtr_ft, y_sub_tr)
        sc_t = [float(p) for p in lr_ft.predict_proba(Xte_ft)[:, 1]]

        # ---------------- 本折指标 ----------------
        rec = {"fold": fi, "n_train": len(tr_rows), "n_test": len(te_rows),
               "label_support_test": {str(k): v for k, v in sorted(Counter(y_te).items())},
               "leakage_check": {"stem_overlap": len(ov), "path_overlap": len(ov_path),
                                 "patient_id_available": False},
               "best_epoch": best["epoch"], "inner_val_auc": round(best["auc"], 4),
               "history": hist, "wall_s": round(time.monotonic() - t0, 1)}

        for name, sc in (("frozen_probe", sc_f), ("zero_shot", list(sc_z)),
                         ("finetuned_visual", sc_t)):
            auc = auc_binary(sc, y_te)
            lanes[name].append(round(auc, 4))
            rec[name] = {"auc": round(auc, 4), **binary_metrics(sc, y_te)}
            # 存 OOF 供池化与分层
            for j, s in zip(te_idx, sc, strict=True):
                oof[name][int(j)] = float(s)
            print(f"  {name:17s} AUC={auc:.4f}", flush=True)

        fold_recs.append(rec)
        del visual, opt
        torch.cuda.empty_cache()

    # ---------------- 汇总 ----------------
    def agg(vals):
        if not vals:
            return {"mean": None, "std": None, "n": 0}
        return {"mean": round(float(np.mean(vals)), 4),
                "std": round(float(np.std(vals)), 4),
                "min": round(float(min(vals)), 4),
                "max": round(float(max(vals)), 4),
                "n": len(vals), "values": vals}

    summary = {name: agg(v) for name, v in lanes.items()}

    # 池化 OOF AUC（每折 test 拼起来）
    def pooled(name):
        keys = sorted(oof[name])
        sc = [oof[name][k] for k in keys]
        ys = [rows[k]["label"] for k in keys]
        return {"n": len(keys), "auc": round(auc_binary(sc, ys), 4),
                "auc_ci95": bootstrap_auc(sc, ys)}

    pooled_metrics = {name: pooled(name) for name in lanes}

    # ---------------- 分层报告（按 folder 来源） ----------------
    folder_counts = Counter(r["folder"] for r in rows)
    stratify: dict = {}
    if len(folder_counts) > 1:
        for name in lanes:
            per_folder: dict[str, dict] = defaultdict(lambda: {"n": 0, "pos": 0, "sc": [], "y": []})
            for k, s in oof[name].items():
                f = rows[k]["folder"] or "(empty)"
                per_folder[f]["n"] += 1
                per_folder[f]["y"].append(rows[k]["label"])
                per_folder[f]["sc"].append(s)
            stratify[name] = {}
            for f, d in sorted(per_folder.items()):
                if d["n"] < 30:
                    stratify[name][f] = {"n": d["n"], "status": "n<30 不报 AUC"}
                    continue
                stratify[name][f] = {
                    "n": d["n"],
                    "malignant_rate": round(float(np.mean(d["y"])), 4),
                    "auc": round(auc_binary(d["sc"], d["y"]), 4),
                    **binary_metrics(d["sc"], d["y"]),
                }
    else:
        stratify = {"note": f"TN5000 的 XML `folder` 只有 1 个取值（{list(folder_counts)[0]!r}），"
                            f"无法按来源分层；TN5000 亦无器官以外的分层字段。"
                            f"按器官分层在此不适用（TN5000 全是甲状腺）。"}

    report = {
        "probe": "p98_thyroid_stratified_5fold",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("单次 80/20 划分（p93: AUC 0.9122）在生物医学小数据上不可靠："
                "ml-training-skill references/biomedical.md:420 要求 k-fold 5-10。"
                "TN5000 无患者 ID → 只能用 StratifiedKFold(5)，故本数字是"
                "**图像级划分的乐观估计**。"),
        "dataset": {"name": "TN5000", "zip": str(Path(args.zip).expanduser()),
                    "n_images": len(rows),
                    "class_support": {str(k): v for k, v in sorted(Counter(y_all).items())},
                    "folders": dict(folder_counts)},
        "split_level": {
            "unit": "image",
            "patient_id_available": False,
            "patient_level_split_possible": False,
            "reason": ("TN5000 每份 XML 仅有 folder/filename/source/owner/size/segmented/object，"
                       "无任何患者标识（2026-11-08 实测）→ GroupKFold 无分组变量可用"),
            "risk": "同一患者/同一病灶的不同切面可能同时进 train 与 test → 指标偏乐观",
        },
        "protocol": {
            "n_splits": args.n_splits, "shuffle": True, "random_state": args.seed,
            "epochs": args.epochs, "lr": args.lr, "patience": args.patience,
            "note": "配方与 p93 一致（类别锚点 CE + LR 探针 + 内层 val 早停），只是把单次划分换成 5 折",
        },
        "folds": fold_recs,
        "summary_5fold": summary,
        "pooled_oof": pooled_metrics,
        "stratified_by_folder": stratify,
        "comparison": {
            "p93_single_split_80_20_auc": REF_P93_SINGLE_SPLIT_AUC,
            "p90_production_t2a_tn5000_auc": REF_P90_PRODUCTION_AUC,
            "note": ("p93 的单次 0.9122 与本脚本 5 折均值的关系见 summary_5fold；"
                     "若 5 折均值明显低且 std 大，说明单次结果偏乐观/不稳定。"),
        },
        "split_level_disclaimer": (
            "⚠️ **图像级划分（StratifiedKFold），不是患者级**：TN5000 无患者 ID，"
            "同一患者的多张图会跨折 → 本 AUC **偏乐观**，不能当作注册申报级独立验证。"
            "恶性占比 71.5% 属人为富集，患病率与派生指标不可外推。"),
        "wall_s": round(time.monotonic() - t_all, 1),
    }

    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print("=== T2a 甲状腺 StratifiedKFold(5) 汇总 ===")
    for name, v in summary.items():
        print(f"  {name:17s} AUC = {v['mean']} ± {v['std']} "
              f"(min {v.get('min')} / max {v.get('max')})")
    print("\n  池化 OOF：")
    for name, v in pooled_metrics.items():
        print(f"  {name:17s} AUC = {v['auc']}  CI95={v['auc_ci95']}  n={v['n']}")
    print(f"\n  对照：p93 单次 80/20 = {REF_P93_SINGLE_SPLIT_AUC}；"
          f"现网 T2a = {REF_P90_PRODUCTION_AUC}")
    print("  ⚠️ 图像级划分（TN5000 无患者 ID）→ 乐观估计")
    print(f"报告：{rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
