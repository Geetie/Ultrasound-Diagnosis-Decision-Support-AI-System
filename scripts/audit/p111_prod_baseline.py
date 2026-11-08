"""现网 T2a 在 TN5000 上的 5 折口径基线 + 校准诊断（p102）。

## 为什么必须有这一条

行动计划 2.1 要解决的是「TN5000 恶性 71.5% → 模型退化成全报恶性，
敏感 0.9374 / 特异 0.1598」。但**必须先分清这 0.16 是谁的**：

- `p90` / `p93` 测的是**现网 T2a**（`models/t2a_v2/t2a_finetune.pt` + `t2a_probe_lr.joblib`，
  用 US-365K 三分类训出来的旧权重），零训练直接推 TN5000 → AUC 0.6303 / 特异 0.1599
- `p98` 用**同一批 TN5000 数据重训**（BiomedCLIP 基座 + 类别锚点 CE + LR 探针）
  → AUC 0.9037 / 特异 ≈0.70

两者**不是同一个模型**。若混着说，会得出"加个 class weight 就把特异度从 0.16 救到 0.5"
这种错误因果。本脚本把现网模型放到**与 p98/p101 完全相同的 5 折划分**上算指标，
让两条基线在同一坐标系里可比。

## 同时做的三件事

1. **同折基线**：现网模型在 p98 的 5 折 test 上的 AUC / 敏感 / 特异 / 良性F1 / 宏F1
2. **校准诊断**（2.3 的前置）：ECE(10 bin) 前后、温度 T、reliability diagram 分箱
   温度 T 在 **4/5 训练部分**上拟合、在 **1/5** 上评 → 无泄漏
3. **操作点拆解**：把"特异度低"拆成「排序差(AUC)」vs「阈值/先验不对」两部分
   —— 用先验校正 + 平衡准确率选阈值，看单纯改操作点能到多少

## 纪律

- 抽特征 `shuffle=False` + `assert order == range(n)`
- 温度只在训练部分拟合，只在 test 部分评
- 不训练任何东西（这是"现网到底是什么水平"的测量，不是改进实验）
- 泄漏四格显式记录；TN5000 无患者 ID / 无视频号 → 两格 N/A

## 用法（服务器）

    cd ~/uai/src && CUDA_VISIBLE_DEVICES=0 PYTHONPATH=~/uai/src ~/uai/venv/bin/python \
        scripts/audit/p111_prod_baseline.py --report reports/p111_prod_baseline.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import time
from collections import Counter
from pathlib import Path


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
    return {"threshold": round(thr, 4), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision_malignant": round(prec, 4), "sensitivity": round(rec, 4),
            "specificity": round(spec, 4), "f1_malignant": round(f1_mal, 4),
            "f1_benign": round(f1_ben, 4),
            "macro_f1": round((f1_mal + f1_ben) / 2, 4),
            "balanced_accuracy": round((rec + spec) / 2, 4),
            "accuracy": round((tp + tn) / max(1, len(labels)), 4),
            "predicted_positive_rate": round((tp + fp) / max(1, len(labels)), 4)}


def ece_bins(scores, labels, n_bins=10):
    bins, tot, ece = [], len(scores), 0.0
    for b in range(n_bins):
        lo, hi = b / n_bins, (b + 1) / n_bins
        idx = [i for i, s in enumerate(scores)
               if (lo <= s < hi) or (b == n_bins - 1 and s >= hi - 1e-12)]
        if not idx:
            bins.append({"bin": b, "range": f"[{lo:.1f},{hi:.1f})", "n": 0,
                         "mean_confidence": None, "fraction_positive": None,
                         "gap_conf_minus_acc": None})
            continue
        mc = sum(scores[i] for i in idx) / len(idx)
        fpos = sum(labels[i] for i in idx) / len(idx)
        bins.append({"bin": b, "range": f"[{lo:.1f},{hi:.1f})", "n": len(idx),
                     "mean_confidence": round(mc, 4), "fraction_positive": round(fpos, 4),
                     "gap_conf_minus_acc": round(mc - fpos, 4)})
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
    off = math.log(prior / (1 - prior))
    return [_sigmoid(_logit(p) - off) for p in scores]


def pick_threshold_ba(scores, labels):
    best_t, best_v = 0.5, -1.0
    grid = [i / 100 for i in range(2, 99)]
    for thr in grid:
        ba = binary_metrics(scores, labels, thr)["balanced_accuracy"]
        if ba > best_v:
            best_t, best_v = thr, ba
    return round(best_t, 4), round(best_v, 4)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="现网 T2a 的 TN5000 同折基线 + 校准诊断（p102）")
    ap.add_argument("--zip", default=str(Path.home() / "uai/data/tn5000/TN5000_forReview.zip"))
    ap.add_argument("--cache-dir", default=str(Path.home() / "uai/data/tn5000/images"))
    ap.add_argument("--base-model", default=str(Path.home() / "uai/models/biomedclip-base-patch16-224"))
    ap.add_argument("--prod-weights", default=str(Path.home() / "uai/models/t2a_v2/t2a_finetune.pt"))
    ap.add_argument("--prod-probe", default=str(Path.home() / "uai/models/t2a_v2/t2a_probe_lr.joblib"))
    ap.add_argument("--report", default="reports/p111_prod_baseline.json")
    ap.add_argument("--n-splits", type=int, default=5)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--num-workers", type=int, default=12)
    args = ap.parse_args(argv)

    import joblib
    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from sklearn.model_selection import StratifiedKFold
    from torch.utils.data import DataLoader, Dataset

    p98_path = Path(__file__).resolve().parent / "p98_thyroid_stratified_5fold.py"
    spec = importlib.util.spec_from_file_location("_p98", p98_path)
    p98 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(p98)

    device = "cuda"
    t_all = time.monotonic()
    rows = p98.load_tn5000(Path(args.zip).expanduser(), Path(args.cache_dir).expanduser())
    y_all = [r["label"] for r in rows]
    prior = sum(y_all) / len(y_all)
    print(f"[data] n={len(rows)} support={dict(Counter(y_all))} 恶性占比={prior:.4f}", flush=True)

    uri = "local-dir:" + str(Path(args.base_model).expanduser())
    model, _, preprocess_val = open_clip.create_model_and_transforms(uri, device=device)
    model.eval()
    sd = torch.load(Path(args.prod_weights).expanduser(), map_location=device, weights_only=True)
    import copy
    visual = copy.deepcopy(model.visual)
    visual.load_state_dict(sd)
    visual.to(device).eval()
    probe = joblib.load(Path(args.prod_probe).expanduser())
    print(f"[prod] probe.classes_={getattr(probe, 'classes_', None)}", flush=True)

    class _DS(Dataset):
        def __init__(self, rs):
            self.rs = rs

        def __len__(self):
            return len(self.rs)

        def __getitem__(self, i):
            img = preprocess_val(Image.open(self.rs[i]["filepath"]).convert("RGB"))
            return img, i

    @torch.no_grad()
    def extract(rs):
        ld = DataLoader(_DS(rs), batch_size=args.batch_size, shuffle=False,
                        num_workers=args.num_workers, pin_memory=True)
        feats, idxs = [], []
        for imgs, idx in ld:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = torch.nn.functional.normalize(visual(imgs).float(), p=2, dim=1)
            feats.append(f.float().cpu())
            idxs += [int(i) for i in idx]
        if idxs != list(range(len(rs))):
            raise RuntimeError("特征/样本对齐失败（顺序错位）→ 拒绝出数字")
        return torch.cat(feats).numpy()

    X = extract(rows)
    probs = probe.predict_proba(X)
    t2a_classes = ["Normal", "Benign", "Malignant"]
    class_order = [t2a_classes[int(c)] for c in probe.classes_]
    mal_idx = class_order.index("Malignant")
    sc_all = [float(p[mal_idx]) for p in probs]
    pred_all = [class_order[int(i)] for i in probs.argmax(axis=1)]
    print(f"[prod] 全量 5000：AUC={auc_binary(sc_all, y_all):.4f} "
          f"pred_dist={dict(Counter(pred_all))}", flush=True)

    skf = StratifiedKFold(n_splits=args.n_splits, shuffle=True, random_state=args.seed)
    folds = []
    for fi, (tr, te) in enumerate(skf.split(np.arange(len(rows)), y_all)):
        tr = [int(i) for i in tr]
        te = [int(i) for i in te]
        ov_stem = {rows[i]["stem"] for i in tr} & {rows[i]["stem"] for i in te}
        ov_path = {rows[i]["filepath"] for i in tr} & {rows[i]["filepath"] for i in te}
        if ov_stem or ov_path:
            raise SystemExit(f"fold{fi} 泄漏自检失败")
        s_tr = [sc_all[i] for i in tr]
        y_tr = [y_all[i] for i in tr]
        s_te = [sc_all[i] for i in te]
        y_te = [y_all[i] for i in te]

        T, nll_tr = fit_temperature(s_tr, y_tr)      # T 只在训练部分拟合
        s_T = apply_temperature(s_te, T)
        s_prior = apply_prior_shift(s_te, prior)
        s_prior_T = apply_temperature(s_prior, T)
        thr_ba, ba_tr = pick_threshold_ba(
            apply_prior_shift(s_tr, prior), y_tr)    # 阈值只在训练部分选

        e_raw = ece_bins(s_te, y_te)
        e_T = ece_bins(s_T, y_te)
        e_pri = ece_bins(s_prior, y_te)
        rec = {
            "fold": fi, "n_train": len(tr), "n_test": len(te),
            "label_support_test": {str(k): v for k, v in sorted(Counter(y_te).items())},
            "leakage_check": {"stem_overlap": 0, "path_overlap": 0,
                              "video_overlap": "N/A(TN5000 无视频号)",
                              "patient_overlap": "N/A(TN5000 无患者 ID)"},
            "auc": round(auc_binary(s_te, y_te), 4),
            "at_0.5": binary_metrics(s_te, y_te, 0.5),
            "calibration": {
                "T_fitted_on_train_part": T, "nll_train_at_T": nll_tr,
                "ece_before": e_raw["ece_10bin"], "ece_after_T": e_T["ece_10bin"],
                "ece_after_prior_shift": e_pri["ece_10bin"],
                "nll_before": round(nll(s_te, y_te), 6),
                "nll_after_T": round(nll(s_T, y_te), 6),
                "brier_before": round(brier(s_te, y_te), 6),
                "brier_after_T": round(brier(s_T, y_te), 6),
                "auc_before": round(auc_binary(s_te, y_te), 4),
                "auc_after_T": round(auc_binary(s_T, y_te), 4),
                "auc_after_prior_shift": round(auc_binary(s_prior, y_te), 4),
                "reliability_before": e_raw["bins"],
                "reliability_after_T": e_T["bins"],
                "at_0.5_after_T": binary_metrics(s_T, y_te, 0.5),
                "at_0.5_after_prior_shift": binary_metrics(s_prior, y_te, 0.5),
            },
            "at_thr_from_train": {**binary_metrics(s_prior, y_te, thr_ba),
                                  "thr_selected_on_train_part": thr_ba,
                                  "train_balanced_acc_at_thr": ba_tr},
        }
        m = rec["at_0.5"]
        print(f"[prod fold{fi}] AUC={rec['auc']:.4f} sens={m['sensitivity']:.4f} "
              f"spec={m['specificity']:.4f} F1ben={m['f1_benign']:.4f} "
              f"PPR={m['predicted_positive_rate']:.4f} | T={T} "
              f"ECE {e_raw['ece_10bin']:.4f}→{e_T['ece_10bin']:.4f} "
              f"| 先验校正后 spec@0.5={rec['calibration']['at_0.5_after_prior_shift']['specificity']:.4f}",
              flush=True)
        folds.append(rec)

    def agg(getter):
        v = [getter(f) for f in folds]
        return {"mean": round(float(np.mean(v)), 4), "std": round(float(np.std(v)), 4),
                "min": round(float(min(v)), 4), "max": round(float(max(v)), 4),
                "n": len(v), "values": [round(float(x), 4) for x in v]}

    summary = {
        "auc": agg(lambda f: f["auc"]),
        "sensitivity_at_0.5": agg(lambda f: f["at_0.5"]["sensitivity"]),
        "specificity_at_0.5": agg(lambda f: f["at_0.5"]["specificity"]),
        "f1_benign_at_0.5": agg(lambda f: f["at_0.5"]["f1_benign"]),
        "macro_f1_at_0.5": agg(lambda f: f["at_0.5"]["macro_f1"]),
        "predicted_positive_rate_at_0.5": agg(lambda f: f["at_0.5"]["predicted_positive_rate"]),
        "ece_before": agg(lambda f: f["calibration"]["ece_before"]),
        "ece_after_T": agg(lambda f: f["calibration"]["ece_after_T"]),
        "ece_after_prior_shift": agg(lambda f: f["calibration"]["ece_after_prior_shift"]),
        "temperature_T": agg(lambda f: f["calibration"]["T_fitted_on_train_part"]),
        "specificity_at_0.5_after_prior_shift":
            agg(lambda f: f["calibration"]["at_0.5_after_prior_shift"]["specificity"]),
        "specificity_thr_from_train": agg(lambda f: f["at_thr_from_train"]["specificity"]),
        "sensitivity_thr_from_train": agg(lambda f: f["at_thr_from_train"]["sensitivity"]),
        "f1_benign_thr_from_train": agg(lambda f: f["at_thr_from_train"]["f1_benign"]),
        "nll_before": agg(lambda f: f["calibration"]["nll_before"]),
        "nll_after_T": agg(lambda f: f["calibration"]["nll_after_T"]),
        "brier_before": agg(lambda f: f["calibration"]["brier_before"]),
        "brier_after_T": agg(lambda f: f["calibration"]["brier_after_T"]),
    }

    report = {
        "probe": "p111_prod_baseline",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("2.1 的「特异 0.1598」来自**现网 T2a**（US-365K 三分类权重），"
                "与 p98/p101 重训配方不是同一模型。本脚本把现网模型放在同一 5 折划分上，"
                "让两条基线同坐标系可比；同时做 2.3 的校准诊断。"),
        "model": {"weights": str(Path(args.prod_weights).expanduser()),
                  "probe": str(Path(args.prod_probe).expanduser()),
                  "probe_classes_in_order": class_order,
                  "trained_on": "US-365K 三分类（Normal/Benign/Malignant）—— **不是** TN5000",
                  "note": "零训练推理；TN5000 未参与该权重训练"},
        "dataset": {"name": "TN5000", "n_images": len(rows),
                    "class_support": {str(k): v for k, v in sorted(Counter(y_all).items())},
                    "malignant_rate": round(prior, 4)},
        "split_level_disclaimer": (
            "⚠️ 图像级 StratifiedKFold（TN5000 无患者 ID/视频号）→ 偏乐观；"
            "恶性占比 71.5% 属人为富集，患病率与派生指标不可外推。"),
        "full_set_metrics_at_0.5": {"n": len(rows), "auc": round(auc_binary(sc_all, y_all), 4),
                                    **binary_metrics(sc_all, y_all, 0.5),
                                    "pred_distribution_3class": dict(Counter(pred_all))},
        "folds": folds,
        "summary_5fold": summary,
        "wall_s": round(time.monotonic() - t_all, 1),
    }
    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== 现网 T2a 在 TN5000 的 5 折基线 ===")
    for k in ("auc", "sensitivity_at_0.5", "specificity_at_0.5", "f1_benign_at_0.5",
              "macro_f1_at_0.5", "predicted_positive_rate_at_0.5"):
        v = summary[k]
        print(f"  {k:34s} {v['mean']} ± {v['std']}")
    print(f"  ECE {summary['ece_before']['mean']} → {summary['ece_after_T']['mean']} "
          f"(T={summary['temperature_T']['mean']})")
    print(f"  先验校正后 spec@0.5 = {summary['specificity_at_0.5_after_prior_shift']['mean']}")
    print(f"  报告：{rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
