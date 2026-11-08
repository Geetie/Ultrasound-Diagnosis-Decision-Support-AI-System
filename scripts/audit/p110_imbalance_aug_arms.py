"""行动计划第 2 组 · 2.1 类别不平衡 + 2.2 医疗增强安全性 的 5 折 A/B（p101）。

## 为什么要两个一起做

2.1 与 2.2 改的是**同一个训练循环的两个点**（loss 与 transform）。若分两脚本跑，
基线要跑两遍且超参可能漂移 → 不可比。故本脚本一个训练核 + N 个 arm，
**除被消融的那一项外全部显式相同**（同 seed、同折、同早停、同探针）。

## arm 清单

### A. 类别不平衡（2.1）—— 全部沿用当前增强 `RandomResizedCrop(0.9,1.0)`

| arm | 训练循环里的改动 | skill 依据 |
|---|---|---|
| `base`    | 无（`CrossEntropyLoss()`） | 现状（= p98 finetuned lane） |
| `cw_ce`   | `CrossEntropyLoss(weight=反频率归一)` | Strategy 1: Weighted loss |
| `focal`   | `alpha*(1-p_t)^gamma*ce`，gamma=2.0 alpha=0.25 | Strategy 2: Focal loss |
| `sampler` | `WeightedRandomSampler(1/class_count)` | Strategy 3: Oversampling |

### B. 医疗增强安全性（2.2）—— 全部无加权 CE

| arm | 训练 transform | 安全表结论 |
|---|---|---|
| `aug_det`   | 确定性 resize+centercrop（去掉全部随机项） | 对照：无增强能到多少 |
| `aug_int`   | + 强度缩放 `ColorJitter(0.2,0.2)` | "Intensity scaling: **Yes**" |
| `aug_hflip` | + `RandomHorizontalFlip(0.5)` | "Horizontal flip: **Depends**" ← 实验回答 |

### C. 事后操作点校正（**不用重训**，每个 arm 的每折都自动算，故不作为独立 arm）

| 做法 | 在哪算 |
|---|---|
| 阈值改由**内层 val 的平衡准确率**选出 | `folds[*].metrics_thr_from_val` |
| logit 减训练先验 logit 差（贝叶斯先验校正） | `folds[*].calibration.*prior_shift*` |

C 组回答一个关键问题：**特异度低究竟是"排序差"还是"阈值/先验不对"**。
每个 arm 的 `metrics_at_0.5` 与 `metrics_thr_from_val` 并排给出，就是为了把这两件事分开。

## 纪律（血泪教训）

- 抽特征 `shuffle=False` + `assert order == range(n)`（p87：shuffle=True 曾致 79.77% 标签错位）
- 每折 test 不参与训练、不参与选 epoch；阈值只允许在**内层 val** 上选
- 泄漏四格：stem 交集=0 / 路径交集=0 / 视频交集=0（TN5000 无视频号 → N/A）/ 患者交集=0（无患者 ID → N/A）
- 温度 T 只在**内层 val** 上拟合，只在 test 上评 ECE
- 不编数字：跑不出来写 blocked

## 用法（服务器，GPU）

    cd ~/uai/src && CUDA_VISIBLE_DEVICES=0 PYTHONPATH=~/uai/src ~/uai/venv/bin/python \
        scripts/audit/p110_imbalance_aug_arms.py --arms base,cw_ce --folds 0 \
        --report reports/p101_smoke.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import time
from collections import Counter
from pathlib import Path

CLIP_MEAN = [0.48145466, 0.4578275, 0.40821073]
CLIP_STD = [0.26862954, 0.26130258, 0.27577711]

ARM_DEFS = {
    "base":         ("crop",   "ce",        "CrossEntropyLoss 无加权（现状基线）"),
    "cw_ce":        ("crop",   "cw_ce",     "CrossEntropyLoss(weight=类别反频率归一)（skill Strategy 1）"),
    "focal":        ("crop",   "focal",     "FocalLoss(gamma=2.0, alpha=0.25)（skill Strategy 2）"),
    "sampler":      ("crop",   "sampler",   "WeightedRandomSampler(1/class_count)（skill Strategy 3）"),
    "aug_det":      ("det",    "ce",        "确定性 resize+centercrop（去随机增强）"),
    "aug_int":      ("int",    "ce",        "aug_det + 强度缩放 ColorJitter(0.2,0.2)"),
    "aug_hflip":    ("hflip",  "ce",        "aug_int + RandomHorizontalFlip(0.5)"),
}


# ------------------------------------------------------------------ 指标
def auc_binary(scores, labels):
    pairs = sorted(zip(scores, labels), key=lambda x: x[0])
    pos = sum(1 for _, y in pairs if y == 1)
    neg = len(pairs) - pos
    if not pos or not neg:
        return float("nan")
    ranks, i = {}, 0
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


def binary_metrics(scores, labels, thr=0.5):
    tp = sum(1 for s, y in zip(scores, labels) if s >= thr and y == 1)
    fp = sum(1 for s, y in zip(scores, labels) if s >= thr and y == 0)
    fn = sum(1 for s, y in zip(scores, labels) if s < thr and y == 1)
    tn = sum(1 for s, y in zip(scores, labels) if s < thr and y == 0)
    prec = tp / (tp + fp) if (tp + fp) else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0
    spec = tn / (tn + fp) if (tn + fp) else 0.0
    f1_mal = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    p_b = tn / (tn + fn) if (tn + fn) else 0.0
    r_b = tn / (tn + fp) if (tn + fp) else 0.0
    f1_ben = 2 * p_b * r_b / (p_b + r_b) if (p_b + r_b) else 0.0
    n = len(labels)
    return {"threshold": round(thr, 4), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision_malignant": round(prec, 4), "sensitivity": round(rec, 4),
            "specificity": round(spec, 4), "f1_malignant": round(f1_mal, 4),
            "precision_benign": round(p_b, 4), "recall_benign": round(r_b, 4),
            "f1_benign": round(f1_ben, 4),
            "macro_f1": round((f1_mal + f1_ben) / 2, 4),
            "balanced_accuracy": round((rec + spec) / 2, 4),
            "accuracy": round((tp + tn) / n, 4) if n else 0.0}


def pick_threshold_balanced_accuracy(scores, labels):
    """在内层 val 上选平衡准确率最优阈值（**只在 val 上用**，绝不看 test）。"""
    best_t, best_v = 0.5, -1.0
    for thr in sorted(set([0.02, 0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.98]
                          + [i / 100 for i in range(5, 100)])):
        ba = binary_metrics(scores, labels, thr)["balanced_accuracy"]
        if ba > best_v:
            best_t, best_v = thr, ba
    return round(best_t, 4), round(best_v, 4)


def ece_bins(scores, labels, n_bins=10):
    bins, tot, ece = [], len(scores), 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [i for i, s in enumerate(scores)
               if (lo <= s < hi) or (b == n_bins - 1 and s >= hi - 1e-12)]
        if not idx:
            bins.append({"bin": b, "lo": round(lo, 2), "hi": round(hi, 2), "n": 0,
                         "mean_confidence": None, "fraction_positive": None, "gap": None})
            continue
        mc = sum(scores[i] for i in idx) / len(idx)
        fpos = sum(labels[i] for i in idx) / len(idx)
        bins.append({"bin": b, "lo": round(lo, 2), "hi": round(hi, 2), "n": len(idx),
                     "mean_confidence": round(mc, 4), "fraction_positive": round(fpos, 4),
                     "gap": round(mc - fpos, 4)})
        ece += (len(idx) / tot) * abs(mc - fpos)
    return {"ece_10bin": round(ece, 4), "n": tot, "bins": bins}


def nll(scores, labels, eps=1e-9):
    s = 0.0
    for p, y in zip(scores, labels):
        p = min(max(p, eps), 1 - eps)
        s += -(math.log(p) if y == 1 else math.log(1 - p))
    return s / max(1, len(scores))


def brier(scores, labels):
    return sum((p - y) ** 2 for p, y in zip(scores, labels)) / max(1, len(scores))


def _logit(p, eps=1e-6):
    p = min(max(p, eps), 1 - eps)
    return math.log(p / (1 - p))


def _sigmoid(z):
    if z >= 0:
        return 1 / (1 + math.exp(-z))
    e = math.exp(z)
    return e / (1 + e)


def fit_temperature(scores, labels, lo=0.05, hi=20.0):
    """按 NLL 最优化温度 T（logits/T 再过 sigmoid）。网格 + 三分细化，纯 Python 可复现。"""
    zs = [_logit(p) for p in scores]
    best_T, best_v = 1.0, None
    for i in range(81):
        T = lo * (hi / lo) ** (i / 80)
        v = nll([_sigmoid(z / T) for z in zs], labels)
        if best_v is None or v < best_v:
            best_T, best_v = T, v
    a, b = best_T / 1.3, best_T * 1.3
    for _ in range(40):
        m1, m2 = a + (b - a) / 3, b - (b - a) / 3
        if nll([_sigmoid(z / m1) for z in zs], labels) < nll([_sigmoid(z / m2) for z in zs], labels):
            b = m2
        else:
            a = m1
    T = (a + b) / 2
    return round(T, 4), round(nll([_sigmoid(z / T) for z in zs], labels), 6)


def apply_temperature(scores, T):
    return [_sigmoid(_logit(p) / T) for p in scores]


def apply_prior_shift(scores, prior):
    """把 p(mal|image) 折算成「平衡先验」下的分数：logit 减 log(prior/(1-prior))。"""
    off = math.log(prior / (1 - prior))
    return [_sigmoid(_logit(p) - off) for p in scores]


# ------------------------------------------------------------------ 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="2.1+2.2 五折 A/B（p101）")
    ap.add_argument("--arms", default="base")
    ap.add_argument("--folds", default="0,1,2,3,4")
    ap.add_argument("--data-script", default="")
    ap.add_argument("--zip", default=str(Path.home() / "uai/data/tn5000/TN5000_forReview.zip"))
    ap.add_argument("--cache-dir", default=str(Path.home() / "uai/data/tn5000/images"))
    ap.add_argument("--model-dir", default=str(Path.home() / "uai/models/biomedclip-base-patch16-224"))
    ap.add_argument("--report", default="reports/p101_arms.json")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--eval-batch-size", type=int, default=256)
    ap.add_argument("--num-workers", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--gamma", type=float, default=2.0)
    ap.add_argument("--alpha", type=float, default=0.25)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args(argv)

    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    import torchvision.transforms as T
    from PIL import Image
    from sklearn.linear_model import LogisticRegression
    from sklearn.model_selection import StratifiedKFold
    from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler

    # 复用 p98 的 TN5000 解析（不复制粘贴，防两处漂移）
    p98_path = Path(args.data_script) if args.data_script else (
        Path(__file__).resolve().parent / "p98_thyroid_stratified_5fold.py")
    spec = importlib.util.spec_from_file_location("_p98", p98_path)
    p98 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p98)

    def set_seed(s):
        random.seed(s)
        np.random.seed(s)
        torch.manual_seed(s)
        torch.cuda.manual_seed_all(s)

    device = "cuda"
    set_seed(args.seed)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    folds_want = [int(x) for x in args.folds.split(",") if x.strip() != ""]
    for a in arms:
        if a not in ARM_DEFS:
            print(f"[FATAL] 未知 arm：{a}；可选 {sorted(ARM_DEFS)}")
            return 2

    rows = p98.load_tn5000(Path(args.zip).expanduser(), Path(args.cache_dir).expanduser())
    if args.limit:
        rows = rows[: args.limit]
        print(f"⚠️ 冒烟模式：只取前 {len(rows)} 条")
    y_all = [r["label"] for r in rows]
    n_pos = sum(y_all)
    prior = n_pos / len(rows)
    print(f"[data] n={len(rows)} labels={dict(Counter(y_all))} 恶性占比={prior:.4f}", flush=True)

    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model0, _, preprocess_val = open_clip.create_model_and_transforms(uri, device=device)
    model0.eval()
    base_sd = {k: v.detach().clone() for k, v in model0.visual.state_dict().items()}

    norm = T.Normalize(mean=CLIP_MEAN, std=CLIP_STD)
    tf = {
        # 现状：与 open_clip 的 preprocess_train 逐项一致（已在服务器打印核对）
        "crop": T.Compose([
            T.RandomResizedCrop(224, scale=(0.9, 1.0), ratio=(0.75, 1.3333),
                                interpolation=T.InterpolationMode.BICUBIC, antialias=True),
            T.ToTensor(), norm]),
        "det": T.Compose([
            T.Resize(224, interpolation=T.InterpolationMode.BICUBIC, antialias=True),
            T.CenterCrop(224), T.ToTensor(), norm]),
        "int": T.Compose([
            T.Resize(224, interpolation=T.InterpolationMode.BICUBIC, antialias=True),
            T.CenterCrop(224), T.ColorJitter(brightness=0.2, contrast=0.2),
            T.ToTensor(), norm]),
        "hflip": T.Compose([
            T.Resize(224, interpolation=T.InterpolationMode.BICUBIC, antialias=True),
            T.CenterCrop(224), T.ColorJitter(brightness=0.2, contrast=0.2),
            T.RandomHorizontalFlip(p=0.5), T.ToTensor(), norm]),
    }

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

    def loader_for(rs, preprocess, mode, bs, shuffle, sampler=None):
        ds = _DS(rs, preprocess, mode)
        kw = dict(batch_size=bs, num_workers=args.num_workers, pin_memory=True,
                  persistent_workers=args.num_workers > 0)
        if sampler is not None:
            return DataLoader(ds, sampler=sampler, **kw)
        return DataLoader(ds, shuffle=shuffle, **kw)

    @torch.no_grad()
    def extract(visual, rs):
        """抽特征 + 顺序守卫（shuffle=False，断言下标 == 0..n-1）。"""
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

    prompts = {"Benign": "an ultrasound image of a benign thyroid nodule",
               "Malignant": "an ultrasound image of a malignant thyroid tumor"}
    with torch.no_grad():
        tok = open_clip.get_tokenizer(uri)(
            [prompts["Benign"], prompts["Malignant"]]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            anchors = F.normalize(model0.encode_text(tok).float(), dim=-1).detach()

    def focal_loss(logits, targets, gamma, alpha):
        ce = F.cross_entropy(logits, targets, reduction="none")
        pt = torch.exp(-ce)
        return (alpha * (1 - pt) ** gamma * ce).mean()

    skf = StratifiedKFold(n_splits=args.n_splits, shuffle=True, random_state=args.seed)
    split_iter = list(skf.split(np.arange(len(rows)), y_all))

    out = {
        "probe": "p110_imbalance_aug_arms",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("行动计划第 2 组。2.1 按 biomedical.md 'Class imbalance' 三种策略逐个试；"
                "2.2 按 'Medical image augmentation safety' 表逐条审当前 preprocess_train 并做 A/B。"),
        "skill_refs": {
            "class_imbalance": "ml-training/references/biomedical.md › Class imbalance",
            "aug_safety": "ml-training/references/biomedical.md › Medical image augmentation safety",
            "regulatory": "ml-training/references/biomedical.md › Regulatory considerations"},
        "dataset": {"name": "TN5000", "n_images": len(rows),
                    "class_support": {str(k): v for k, v in sorted(Counter(y_all).items())},
                    "malignant_rate": round(prior, 4)},
        "arm_definitions": {k: v[2] for k, v in ARM_DEFS.items()},
        "recipe": {"n_splits": args.n_splits, "epochs": args.epochs, "lr": args.lr,
                   "patience": args.patience, "batch_size": args.batch_size, "seed": args.seed,
                   "probe": "LogisticRegression(max_iter=2000)，**所有 arm 都不加权**（保证唯一变量是训练循环）",
                   "note": "除被消融项外与 p98 finetuned lane 完全一致（类别锚点 CE + LR 探针 + 内层 val 早停）"},
        "split_level_disclaimer": (
            "⚠️ 图像级 StratifiedKFold：TN5000 无患者 ID、无视频号 → 同患者多图会跨折，"
            "AUC 偏乐观。恶性占比 71.5% 属人为富集，患病率与派生指标不可外推。"),
        "arms": {},
    }
    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)

    def save():
        rp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    for arm in arms:
        tf_key, loss_key, arm_desc = ARM_DEFS[arm]
        print(f"\n{'#'*68}\n### arm = {arm}  ({arm_desc})\n"
              f"### transform={tf_key}  loss={loss_key}\n{'#'*68}", flush=True)
        t_arm = time.monotonic()
        fold_recs, oof = [], {}
        for fi, (tr_idx, te_idx) in enumerate(split_iter):
            if fi not in folds_want:
                continue
            set_seed(args.seed + fi)
            tr_rows = [rows[i] for i in tr_idx]
            te_rows = [rows[i] for i in te_idx]
            y_tr = [r["label"] for r in tr_rows]
            y_te = [r["label"] for r in te_rows]

            ov = {r["stem"] for r in tr_rows} & {r["stem"] for r in te_rows}
            ov_path = {r["filepath"] for r in tr_rows} & {r["filepath"] for r in te_rows}
            if ov or ov_path:
                raise SystemExit(f"fold{fi} 泄漏自检失败 stem={len(ov)} path={len(ov_path)}")
            if len(tr_rows) + len(te_rows) != len(rows):
                raise SystemExit("折之间条数不守恒")
            print(f"\n[{arm} fold{fi}] train={len(tr_rows)} test={len(te_rows)} "
                  f"泄漏四格 stem=0 path=0 video=N/A patient=N/A", flush=True)
            t0 = time.monotonic()

            import copy
            visual = copy.deepcopy(model0.visual)
            visual.load_state_dict(base_sd)
            for prm in visual.parameters():
                prm.requires_grad = True
            opt = torch.optim.AdamW(visual.parameters(), lr=args.lr, weight_decay=0.1)

            loss_fn, sampler_kind = None, None
            if loss_key == "ce":
                loss_fn = torch.nn.CrossEntropyLoss()
            elif loss_key == "cw_ce":
                cnt = torch.tensor([y_all.count(0), y_all.count(1)], dtype=torch.float32)
                w = 1.0 / cnt
                w = w / w.sum() * len(w)
                print(f"  [cw_ce] counts={cnt.tolist()} weights={[round(x,4) for x in w.tolist()]}",
                      flush=True)
                # autocast 下 logits 是 bf16；CE 的 weight 必须与之同 dtype，
                # 否则 RuntimeError: expected scalar type BFloat16 but found Float
                loss_fn = torch.nn.CrossEntropyLoss(weight=w.to(device, dtype=torch.bfloat16))
            elif loss_key == "sampler":
                loss_fn = torch.nn.CrossEntropyLoss()
                sampler_kind = "sampler"
            elif loss_key == "focal":
                pass

            inner = StratifiedKFold(n_splits=7, shuffle=True, random_state=args.seed + 1)
            sub_tr_i, sub_va_i = next(iter(inner.split(np.arange(len(tr_rows)), y_tr)))
            sub_tr = [tr_rows[i] for i in sub_tr_i]
            sub_va = [tr_rows[i] for i in sub_va_i]
            y_sub_tr = [r["label"] for r in sub_tr]
            y_va = [r["label"] for r in sub_va]

            sampler = None
            if sampler_kind == "sampler":
                cnt_s = Counter(y_sub_tr)
                w_per_cls = {c: 1.0 / cnt_s[c] for c in cnt_s}
                sw = [w_per_cls[y] for y in y_sub_tr]
                sampler = WeightedRandomSampler(sw, num_samples=len(sw), replacement=True)
                print(f"  [sampler] class_counts={dict(cnt_s)} → 每 epoch {len(sw)} 样本", flush=True)

            best = {"auc": -1.0, "epoch": 0, "sd": None, "va_scores": None}
            stale, hist = 0, []
            for ep in range(args.epochs):
                visual.train()
                tot, nb = 0.0, 0
                for imgs, ys in loader_for(sub_tr, tf[tf_key], "label",
                                           args.batch_size, True, sampler=sampler):
                    imgs, ys = imgs.to(device, non_blocking=True), ys.to(device)
                    with torch.autocast("cuda", dtype=torch.bfloat16):
                        im = F.normalize(visual(imgs).float(), p=2, dim=1)
                        logits = im @ anchors.T * 20.0
                    loss = (focal_loss(logits, ys, args.gamma, args.alpha)
                            if loss_key == "focal" else loss_fn(logits, ys))
                    opt.zero_grad(set_to_none=True)
                    loss.backward()
                    opt.step()
                    tot += float(loss)
                    nb += 1
                Xs = extract(visual, sub_tr)
                Xv = extract(visual, sub_va)
                lr_v = LogisticRegression(max_iter=2000).fit(Xs, y_sub_tr)
                va_sc = [float(p) for p in lr_v.predict_proba(Xv)[:, 1]]
                va_auc = auc_binary(va_sc, y_va)
                hist.append({"epoch": ep + 1, "loss": round(tot / max(1, nb), 4),
                             "inner_val_auc": round(va_auc, 4)})
                print(f"  ep{ep+1} loss={hist[-1]['loss']:.4f} inner_val_auc={va_auc:.4f}",
                      flush=True)
                if va_auc > best["auc"]:
                    best = {"auc": va_auc, "epoch": ep + 1, "va_scores": va_sc,
                            "sd": {k: v.detach().cpu().clone()
                                   for k, v in visual.state_dict().items()}}
                    stale = 0
                else:
                    stale += 1
                    if stale >= args.patience:
                        print(f"  [early-stop] 连续 {stale} 轮无提升", flush=True)
                        break

            visual.load_state_dict({k: v.to(device) for k, v in best["sd"].items()})
            visual.eval()
            Xtr_ft = extract(visual, sub_tr)
            Xte_ft = extract(visual, te_rows)
            lr_ft = LogisticRegression(max_iter=2000).fit(Xtr_ft, y_sub_tr)
            sc_t = [float(p) for p in lr_ft.predict_proba(Xte_ft)[:, 1]]
            sc_v = best["va_scores"]

            # ---- 温度缩放（T 只在内部 val 拟合）----
            T_fit, nll_val_T = fit_temperature(sc_v, y_va)
            sc_T = apply_temperature(sc_t, T_fit)
            # ---- 先验校正 & val 选阈值（都是事后操作点，不改模型）----
            sc_prior = apply_prior_shift(sc_t, prior)
            sc_prior_v = apply_prior_shift(sc_v, prior)
            thr_val, ba_val = pick_threshold_balanced_accuracy(sc_prior_v, y_va)

            e_raw = ece_bins(sc_t, y_te)
            e_T = ece_bins(sc_T, y_te)
            e_pri = ece_bins(sc_prior, y_te)
            rec = {
                "fold": fi, "n_train": len(tr_rows), "n_test": len(te_rows),
                "label_support_test": {str(k): v for k, v in sorted(Counter(y_te).items())},
                "leakage_check": {"stem_overlap": 0, "path_overlap": 0,
                                  "video_overlap": "N/A(TN5000 无视频号)",
                                  "patient_overlap": "N/A(TN5000 无患者 ID)"},
                "best_epoch": best["epoch"], "inner_val_auc": round(best["auc"], 4),
                "history": hist,
                "auc": round(auc_binary(sc_t, y_te), 4),
                "metrics_at_0.5": binary_metrics(sc_t, y_te, 0.5),
                "metrics_thr_from_val": {**binary_metrics(sc_prior, y_te, thr_val),
                                         "thr_selected_on_inner_val": thr_val,
                                         "inner_val_balanced_acc_at_thr": ba_val},
                "calibration": {
                    "T_fitted_on_inner_val": T_fit,
                    "nll_inner_val_at_T": nll_val_T,
                    "ece_before": e_raw["ece_10bin"], "ece_after_T": e_T["ece_10bin"],
                    "ece_after_prior_shift": e_pri["ece_10bin"],
                    "nll_before": round(nll(sc_t, y_te), 6),
                    "nll_after_T": round(nll(sc_T, y_te), 6),
                    "brier_before": round(brier(sc_t, y_te), 6),
                    "brier_after_T": round(brier(sc_T, y_te), 6),
                    "auc_before": round(auc_binary(sc_t, y_te), 4),
                    "auc_after_T": round(auc_binary(sc_T, y_te), 4),
                    "auc_after_prior_shift": round(auc_binary(sc_prior, y_te), 4),
                    "reliability_before": e_raw["bins"],
                    "reliability_after_T": e_T["bins"],
                    "metrics_at_0.5_after_T": binary_metrics(sc_T, y_te, 0.5),
                },
                "wall_s": round(time.monotonic() - t0, 1),
            }
            fold_recs.append(rec)
            sc_T_prior = apply_temperature(sc_prior, T_fit)
            for j, s in zip(te_idx, sc_t, strict=True):
                oof[int(j)] = {"score": float(s), "label": int(rows[int(j)]["label"]),
                               "stem": rows[int(j)]["stem"], "fold": fi,
                               "score_T": float(apply_temperature([s], T_fit)[0]),
                               "score_prior": float(apply_prior_shift([s], prior)[0]),
                               "score_prior_T": float(sc_T_prior[0])}
            m = rec["metrics_at_0.5"]
            print(f"  {arm} fold{fi}: AUC={rec['auc']:.4f} sens={m['sensitivity']:.4f} "
                  f"spec={m['specificity']:.4f} F1ben={m['f1_benign']:.4f} "
                  f"macroF1={m['macro_f1']:.4f} | T={T_fit} "
                  f"ECE {e_raw['ece_10bin']:.4f}→{e_T['ece_10bin']:.4f} "
                  f"| thr_val={thr_val} → spec={rec['metrics_thr_from_val']['specificity']:.4f} "
                  f"({rec['wall_s']}s)", flush=True)
            del visual, opt
            torch.cuda.empty_cache()

        def ms(key):
            v = [f[key] for f in fold_recs]
            return {"mean": round(float(np.mean(v)), 4), "std": round(float(np.std(v)), 4),
                    "n": len(v), "values": [round(float(x), 4) for x in v]}

        def mm(sec, key):
            v = [f[sec][key] for f in fold_recs]
            return {"mean": round(float(np.mean(v)), 4), "std": round(float(np.std(v)), 4),
                    "n": len(v), "values": [round(float(x), 4) for x in v]}

        keys = sorted(oof)
        ps = [oof[k]["score"] for k in keys]
        py = [oof[k]["label"] for k in keys]
        psT = [oof[k]["score_T"] for k in keys]
        psP = [oof[k]["score_prior"] for k in keys]
        pooled = {"n": len(keys), "auc": round(auc_binary(ps, py), 4),
                  "auc_after_T": round(auc_binary(psT, py), 4),
                  "auc_after_prior_shift": round(auc_binary(psP, py), 4),
                  "ece_before": ece_bins(ps, py)["ece_10bin"],
                  "ece_after_T": ece_bins(psT, py)["ece_10bin"],
                  "ece_after_prior_shift": ece_bins(psP, py)["ece_10bin"],
                  "at_0.5": binary_metrics(ps, py, 0.5)}

        out["arms"][arm] = {
            "arm": arm, "definition": arm_desc, "transform": tf_key, "loss": loss_key,
            "folds": fold_recs,
            "summary": {
                "auc": ms("auc"),
                "specificity_at_0.5": mm("metrics_at_0.5", "specificity"),
                "sensitivity_at_0.5": mm("metrics_at_0.5", "sensitivity"),
                "f1_benign_at_0.5": mm("metrics_at_0.5", "f1_benign"),
                "f1_malignant_at_0.5": mm("metrics_at_0.5", "f1_malignant"),
                "macro_f1_at_0.5": mm("metrics_at_0.5", "macro_f1"),
                "balanced_accuracy_at_0.5": mm("metrics_at_0.5", "balanced_accuracy"),
                "specificity_thr_from_val": mm("metrics_thr_from_val", "specificity"),
                "sensitivity_thr_from_val": mm("metrics_thr_from_val", "sensitivity"),
                "f1_benign_thr_from_val": mm("metrics_thr_from_val", "f1_benign"),
                "macro_f1_thr_from_val": mm("metrics_thr_from_val", "macro_f1"),
                "ece_before": mm("calibration", "ece_before"),
                "ece_after_T": mm("calibration", "ece_after_T"),
                "ece_after_prior_shift": mm("calibration", "ece_after_prior_shift"),
                "temperature_T": mm("calibration", "T_fitted_on_inner_val"),
                "nll_before": mm("calibration", "nll_before"),
                "nll_after_T": mm("calibration", "nll_after_T"),
                "brier_before": mm("calibration", "brier_before"),
                "brier_after_T": mm("calibration", "brier_after_T"),
            },
            "pooled_oof": pooled,
            "oof_records": oof,
            "wall_s": round(time.monotonic() - t_arm, 1),
        }
        s = out["arms"][arm]["summary"]
        print(f"\n=== {arm} 汇总（{len(fold_recs)} 折）===")
        print(f"  AUC        = {s['auc']['mean']} ± {s['auc']['std']}")
        print(f"  sens@0.5   = {s['sensitivity_at_0.5']['mean']} ± {s['sensitivity_at_0.5']['std']}")
        print(f"  spec@0.5   = {s['specificity_at_0.5']['mean']} ± {s['specificity_at_0.5']['std']}")
        print(f"  F1ben@0.5  = {s['f1_benign_at_0.5']['mean']} ± {s['f1_benign_at_0.5']['std']} ✓")
        print(f"  macroF1    = {s['macro_f1_at_0.5']['mean']} ± {s['macro_f1_at_0.5']['std']} ✓")
        print(f"  spec@thrval= {s['specificity_thr_from_val']['mean']} ± {s['specificity_thr_from_val']['std']}")
        print(f"  ECE        = {s['ece_before']['mean']} → {s['ece_after_T']['mean']} (T={s['temperature_T']['mean']}) ✓")
        print(f"  wall = {out['arms'][arm]['wall_s']}s", flush=True)
        save()
        print(f"  [saved] {rp}", flush=True)

    print("\n" + json.dumps({a: {"auc": v["summary"]["auc"]["mean"],
                                 "spec@0.5": v["summary"]["specificity_at_0.5"]["mean"],
                                 "f1ben@0.5": v["summary"]["f1_benign_at_0.5"]["mean"],
                                 "macroF1": v["summary"]["macro_f1_at_0.5"]["mean"],
                                 "spec@thrval": v["summary"]["specificity_thr_from_val"]["mean"],
                                 "ECE→": [v["summary"]["ece_before"]["mean"],
                                          v["summary"]["ece_after_T"]["mean"]],
                                 "T": v["summary"]["temperature_T"]["mean"]}
                            for a, v in out["arms"].items()}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
