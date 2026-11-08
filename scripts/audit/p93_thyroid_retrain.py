"""正式审计 · 甲状腺跨器官迁移验证（TN5000 重训 T2a 视觉塔）。

## 要回答的问题

TN5000（甲状腺病理金标准，n=5000）上当前部署的 T2a 分类器 **AUC 0.6303**
（接近随机）；同一套 T2a 在 AUL（肝脏）上 **AUC 0.8950**。
假设：视觉塔权重 `t2a_finetune.pt` 是在**肝脏**上微调的（`t2a_finetune.py` 的
PROMPTS 原文即 liver），跨器官迁移失败。

本脚本直接检验：用**甲状腺**数据重训视觉塔，TN5000 test 上 AUC 能否抬起来。

## 划分（必须写明的限制）

TN5000 **没有患者 ID**，只能按图像随机划分。因此：

* 划分单位 = **图像**，不是患者；
* 同一患者的不同图像/同一病灶的不同切面可能同时落在 train 与 test；
* 论文未提供患者级映射 → **无法**构造患者不交集划分；
* 因此本报告的 AUC 是**图像级泛化估计**，存在同患者泄漏风险，**偏乐观**，
  不能当作注册申报级独立验证。

比例：**80/20**，按标签分层（stratified），seed=42 固定。

## 对照组（同一份划分，同一套 test）

| 组 | 说明 |
|---|---|
| ① `production_t2a`   | 现网 T2a（`t2a_finetune.pt` + `t2a_probe_lr.joblib`），**零训练**。用于把 p90 的 0.6303 在同一 test 上复算，保证 apples-to-apples |
| ② `frozen_probe`     | 冻结 BiomedCLIP + LR（复现 p89 做法） |
| ③ `finetuned_visual` | 微调视觉塔（类别锚点 CE）+ LR 探针 |
| ④ `zero_shot`        | 不训练：图像特征 vs 良/恶性文本 prompt 的余弦差 |

## 用法（服务器，GPU）

    cd ~/uai/src && ~/uai/venv/bin/python scripts/audit/p93_thyroid_retrain.py \
        --report reports/p93_thyroid_retrain.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

CLASSES = ("Benign", "Malignant")
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
REF_P90_AUC = 0.6303  # 现网 T2a 在 TN5000 全量 5000 上的 AUC
REF_AUL_AUC = 0.8950  # 同一套 T2a 在 AUL（肝脏）上的 AUC
BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20261108


# --------------------------------------------------------------------- 指标
def auc_binary(scores: list[float], labels: list[int]) -> float:
    """二分类 AUC（Mann-Whitney U / 秩和口径，与 p85/p90 同实现）。"""
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


def bootstrap_auc(scores: list[float], labels: list[int], n_boot: int,
                  seed: int = BOOTSTRAP_SEED) -> list[float]:
    rnd = random.Random(seed)
    n = len(scores)
    out = []
    for _ in range(n_boot):
        idx = [rnd.randrange(n) for _ in range(n)]
        v = auc_binary([scores[i] for i in idx], [labels[i] for i in idx])
        if v == v:  # 非 nan
            out.append(v)
    out.sort()
    if not out:
        return [float("nan"), float("nan")]
    return [round(out[int(0.025 * (len(out) - 1))], 4),
            round(out[int(0.975 * (len(out) - 1))], 4)]


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

    cache_dir 非空时把图落到本地磁盘（后续多轮训练避免反复解 zip）。
    """
    z = zipfile.ZipFile(zip_path)
    names = z.namelist()
    xmls = {Path(n).stem: n for n in names if n.lower().endswith(".xml")}
    imgs = {Path(n).stem: n for n in names if Path(n).suffix.lower() in EXTS}

    rows: list[dict] = []
    unparsed = 0
    for stem in sorted(xmls):
        if stem not in imgs:
            continue
        try:
            node = ET.fromstring(z.read(xmls[stem]))
        except ET.ParseError:
            unparsed += 1
            continue
        label = None
        for obj in node.findall(".//object"):
            nm = (obj.findtext("name") or "").strip()
            if nm in ("0", "1"):
                label = int(nm)
                break
        if label is None:
            unparsed += 1
            continue
        rows.append({"stem": stem, "member": imgs[stem], "label": label})
    print(f"[tn5000] parsed={len(rows)} unparsed={unparsed} "
          f"labels={dict(Counter(r['label'] for r in rows))}", flush=True)

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
            r["filepath"] = None  # 由调用方按需从 zip 取
    z.close()
    return rows


def stratified_split(rows: list[dict], seed: int, train_ratio: float = 0.8
                     ) -> tuple[list[dict], list[dict]]:
    """**图像级**分层随机划分（TN5000 无患者 ID，见模块文档的限制说明）。"""
    by_lab: dict[int, list[dict]] = {}
    for r in rows:
        by_lab.setdefault(r["label"], []).append(r)
    rnd = random.Random(seed)
    tr, te = [], []
    for lab in sorted(by_lab):
        items = sorted(by_lab[lab], key=lambda r: r["stem"])
        rnd.shuffle(items)
        n_tr = int(len(items) * train_ratio)
        tr += items[:n_tr]
        te += items[n_tr:]
    return tr, te


# --------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--zip", default=str(Path.home() / "uai" / "data" / "tn5000" / "TN5000_forReview.zip"))
    ap.add_argument("--cache-dir", default=str(Path.home() / "uai" / "data" / "tn5000" / "images"))
    ap.add_argument("--model-dir",
                    default=str(Path.home() / "uai" / "models" / "biomedclip-base-patch16-224"))
    ap.add_argument("--prod-weights",
                    default=str(Path.home() / "uai" / "models" / "t2a_v2" / "t2a_finetune.pt"))
    ap.add_argument("--prod-probe",
                    default=str(Path.home() / "uai" / "models" / "t2a_v2" / "t2a_probe_lr.joblib"))
    ap.add_argument("--ckpt-dir", default=str(Path.home() / "uai" / "models" / "t2a_thyroid_p93"))
    ap.add_argument("--report", default="reports/p93_thyroid_retrain.json")
    ap.add_argument("--train-ratio", type=float, default=0.8)
    ap.add_argument("--batch-size", type=int, default=64)
    ap.add_argument("--eval-batch-size", type=int, default=256)
    ap.add_argument("--num-workers", type=int, default=16)
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=1e-5)
    ap.add_argument("--patience", type=int, default=2)
    ap.add_argument("--limit", type=int, default=0, help="冒烟：只取前 N 条")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from sklearn.linear_model import LogisticRegression
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
    train_rows, test_rows = stratified_split(rows, args.seed, args.train_ratio)
    print(f"[split] train={len(train_rows)} test={len(test_rows)} "
          f"train_lab={dict(Counter(r['label'] for r in train_rows))} "
          f"test_lab={dict(Counter(r['label'] for r in test_rows))}", flush=True)

    # 泄漏自检：图像级划分下 train/test 的 stem 必须不重叠（患者级不可查，见文档）
    overlap = {r["stem"] for r in train_rows} & {r["stem"] for r in test_rows}
    if overlap:
        raise SystemExit(f"划分自检失败：train/test 图像重叠 {len(overlap)}")

    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model0, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(
        uri, device=device)
    model0.eval()
    tokenizer = open_clip.get_tokenizer(uri)

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
        """抽特征并按样本下标重排（同一套防错位守卫，与 p92 一致）。"""
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
            raise RuntimeError("特征/样本对齐失败")
        return X

    results: dict[str, dict] = {}
    y_te = [r["label"] for r in test_rows]
    y_tr = [r["label"] for r in train_rows]

    def record(name: str, scores: list[float], note: str, extra: dict | None = None) -> None:
        auc = auc_binary(scores, y_te)
        entry = {
            "n_train": len(train_rows), "n_test": len(test_rows),
            "auc_malignant_vs_benign": round(auc, 4),
            "auc_ci95": bootstrap_auc(scores, y_te, BOOTSTRAP_N),
            "bootstrap_n": BOOTSTRAP_N,
            **binary_metrics(scores, y_te),
            "note": note,
            **(extra or {}),
        }
        results[name] = entry
        print(f"[{name}] AUC={entry['auc_malignant_vs_benign']} "
              f"CI={entry['auc_ci95']} sens={entry['recall_sensitivity']} "
              f"spec={entry['specificity']}", flush=True)

    # ---------------- ① 现网 T2a（零训练，apples-to-apples 复算 0.6303）
    prod_scores = None
    try:
        import copy

        import joblib

        sd = torch.load(Path(args.prod_weights).expanduser(), map_location="cpu",
                        weights_only=True)
        prod_visual = copy.deepcopy(model0.visual)
        prod_visual.load_state_dict(sd)
        prod_visual.to(device).eval()
        probe = joblib.load(Path(args.prod_probe).expanduser())
        classes = [int(c) for c in probe.classes_]
        names = ["Normal", "Benign", "Malignant"]
        mal_col = classes.index(names.index("Malignant"))
        Xte = extract(prod_visual, test_rows)
        prod_scores = [float(p) for p in probe.predict_proba(Xte)[:, mal_col]]
        record("production_t2a_zero_shot", prod_scores,
               "现网 T2a 视觉塔 + 探针，**本脚本零训练**。用于把 p90 的 0.6303 "
               "在本 test 子集上复算，保证与重训结果 apples-to-apples。")
        del prod_visual
        torch.cuda.empty_cache()
    except Exception as exc:  # noqa: BLE001 — 资产缺失不阻断其余对照
        results["production_t2a_zero_shot"] = {
            "status": "blocked", "reason": f"{type(exc).__name__}: {exc}"}
        print(f"[production_t2a] 失败（不阻断其余对照）: {exc}", flush=True)

    # ---------------- ② 冻结 BiomedCLIP + LR（复现 p89 做法）
    t0 = time.time()
    Xtr = extract(model0.visual, train_rows)
    Xte = extract(model0.visual, test_rows)
    lr_frozen = LogisticRegression(max_iter=2000)
    lr_frozen.fit(Xtr, y_tr)
    frozen_scores = [float(p) for p in lr_frozen.predict_proba(Xte)[:, 1]]
    record("frozen_linear_probe", frozen_scores,
           "BiomedCLIP 视觉塔**冻结**，LR 仅在 train 图像上拟合；test 不参与。"
           "与 p89 的 CACTUS 做法一致，用于判断「不改底模能到多少」。",
           {"wall_s": round(time.time() - t0, 1),
            "coef_norm": round(float(np.linalg.norm(lr_frozen.coef_)), 4)})

    # ---------------- ③ zero-shot（良/恶性文本对齐，不训练）
    prompts = {
        "Benign": "an ultrasound image of a benign thyroid nodule",
        "Malignant": "an ultrasound image of a malignant thyroid tumor",
    }
    with torch.no_grad():
        tok = tokenizer([prompts[c] for c in CLASSES]).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            txt = F.normalize(model0.encode_text(tok).float(), dim=-1).cpu().numpy()
    sims = Xte @ txt.T
    zs_scores = [float(s[1] - s[0]) for s in sims]
    # 余弦差是 logit 尺度，AUC 只看排序，不影响；阈值指标用分位数映射到概率再算
    zs_prob = 1 / (1 + np.exp(-np.array(zs_scores) * 20))
    results["zero_shot_text"] = {
        "n_test": len(y_te),
        "auc_malignant_vs_benign": round(auc_binary(list(zs_prob), y_te), 4),
        "auc_ci95": bootstrap_auc(list(zs_prob), y_te, BOOTSTRAP_N),
        "prompts": prompts,
        "note": "不拟合任何分类器：图像-文本余弦差（恶性 − 良性）作分数，"
                "经 sigmoid 映射到 0~1 后才算阈值指标；AUC 只看排序不受影响。",
    }
    print(f"[zero_shot] AUC={results['zero_shot_text']['auc_malignant_vs_benign']}", flush=True)

    # ---------------- ④ 微调视觉塔（类别锚点 CE）
    anchors = torch.from_numpy(txt).to(device)
    ckpt_dir = Path(args.ckpt_dir).expanduser()
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    ckpt = ckpt_dir / "visual.pt"

    import copy as _copy

    model = _copy.deepcopy(model0)
    for p in model.parameters():
        p.requires_grad = False
    for p in model.visual.parameters():
        p.requires_grad = True
    params = [p for p in model.visual.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr, weight_decay=0.1)
    loss_fn = torch.nn.CrossEntropyLoss()

    history, best, stale = [], {"val_auc": -1.0, "epoch": 0}, 0
    # val 从 train 里再切 15%（图像级），只用于早停；test 全程不碰
    tr_rows, va_rows = stratified_split(train_rows, args.seed + 1, 0.85)
    print(f"[finetune] inner train={len(tr_rows)} val={len(va_rows)}", flush=True)
    y_va = [r["label"] for r in va_rows]

    tf0 = time.time()
    for epoch in range(args.epochs):
        model.train()
        tot, nb, te0 = 0.0, 0, time.perf_counter()
        for imgs, ys in loader_for(tr_rows, preprocess_train, "label", args.batch_size, True):
            imgs, ys = imgs.to(device, non_blocking=True), ys.to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                im = F.normalize(model.visual(imgs).float(), p=2, dim=1)
            scale = model.logit_scale.exp().float().clamp(max=100.0)
            loss = loss_fn(scale * (im @ anchors.T), ys)
            opt.zero_grad(set_to_none=True)
            loss.backward()
            opt.step()
            tot += loss.item()
            nb += 1
        Xva = extract(model.visual, va_rows)
        lr_va = LogisticRegression(max_iter=2000).fit(Xva, y_va)
        va_auc = auc_binary([float(p) for p in lr_va.predict_proba(Xva)[:, 1]], y_va)
        rec = {"epoch": epoch + 1, "loss": round(tot / max(1, nb), 4),
               "inner_val_auc": round(va_auc, 4),
               "epoch_s": round(time.perf_counter() - te0, 1)}
        history.append(rec)
        print(f"EPOCH {rec['epoch']}/{args.epochs} loss={rec['loss']} "
              f"inner_val_auc={rec['inner_val_auc']} ({rec['epoch_s']}s)", flush=True)
        if va_auc > best["val_auc"]:
            best = {"val_auc": va_auc, "epoch": rec["epoch"]}
            torch.save({k: v.cpu() for k, v in model.visual.state_dict().items()}, ckpt)
            stale = 0
        else:
            stale += 1
            if stale >= args.patience:
                print(f"[early-stop] inner val 连续 {stale} 轮无提升", flush=True)
                break

    model.visual.load_state_dict(torch.load(ckpt, map_location=device))
    model.visual.eval()
    Xtr_ft = extract(model.visual, tr_rows)
    Xte_ft = extract(model.visual, test_rows)
    lr_ft = LogisticRegression(max_iter=2000).fit(Xtr_ft, [r["label"] for r in tr_rows])
    ft_scores = [float(p) for p in lr_ft.predict_proba(Xte_ft)[:, 1]]
    record("finetuned_visual_on_thyroid", ft_scores,
           "视觉塔在甲状腺 train 图像上微调（类别锚点 CE，lr=%g，最多 %d epoch，按内层 val AUC 早停），"
           "再用 LR 探针在 train 上拟合 → test。test 全程未参与训练与选模。" % (args.lr, args.epochs),
           {"history": history, "best_epoch": best["epoch"],
            "inner_val_auc_at_best": best["val_auc"],
            "checkpoint": str(ckpt), "wall_s": round(time.time() - tf0, 1)})

    # ---------------- 结论
    got_auc = results.get("frozen_linear_probe", {}).get("auc_malignant_vs_benign")
    ft_auc = results.get("finetuned_visual_on_thyroid", {}).get("auc_malignant_vs_benign")
    prod_auc = results.get("production_t2a_zero_shot", {}).get("auc_malignant_vs_benign")
    best_auc = max([a for a in (got_auc, ft_auc) if a is not None], default=None)
    hypo = {
        "hypothesis": (
            "T2a 视觉塔在 AUL（肝脏）上微调 → 跨器官到甲状腺失败，AUC 从肝 0.8950 掉到 0.6303。"
        ),
        "prediction_if_true": (
            "用甲状腺数据重训视觉塔后，TN5000 test AUC 应显著高于 0.6303（尤其应接近 AUL 的 0.8950 量级）。"
        ),
        "reference_auc_current_production": REF_P90_AUC,
        "reference_auc_on_liver_aul": REF_AUL_AUC,
        "recomputed_production_auc_same_test": prod_auc,
        "best_retrained_auc": best_auc,
        "supported": bool(best_auc is not None and best_auc > 0.75),
        "note": (
            "判定口径预先写死：重训后 AUC>0.75 视为「跨器官迁移失败」假设成立"
            "（即瓶颈在视觉塔域偏差，换域重训即可修复）。"
            "若重训后仍在 0.63 附近，则假设不成立，瓶颈在任务/标签本身。"
        ),
    }
    report = {
        "probe": "p93_thyroid_retrain",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "thyroid_retrain_image_level_split",
        "scope_note": (
            "TN5000（公开集，CC BY 4.0，标签来自 FNA 活检与病理）。**无患者 ID** → 只能做"
            "**图像级**分层随机划分（80/20，seed=42），同一患者的多张图可能跨 train/test，"
            "存在同患者泄漏风险 → 本 AUC **偏乐观**，不能当作注册申报级独立验证。"
            "恶性占比 71.5% 属人为富集，患病率与派生指标不可外推。"
        ),
        "split_limitation": {
            "unit": "image",
            "patient_id_available": False,
            "patient_level_split_possible": False,
            "risk": "同一患者/同一病灶的不同切面可能同时进入 train 与 test → 指标偏乐观",
            "train_ratio": args.train_ratio,
            "seed": args.seed,
            "n_train": len(train_rows),
            "n_test": len(test_rows),
            "label_support": {
                "train": {str(k): v for k, v in sorted(Counter(r["label"] for r in train_rows).items())},
                "test": {str(k): v for k, v in sorted(Counter(r["label"] for r in test_rows).items())},
            },
            "image_overlap_train_test": len(overlap),
        },
        "lanes": {
            "①production_t2a_zero_training": "现网 T2a，零训练，与 p90 的 0.6303 同口径复算",
            "②frozen_linear_probe": "冻结 BiomedCLIP + LR（复现 p89 在 CACTUS 上的做法）",
            "③zero_shot_text": "不训练：良/恶性文本 prompt 余弦差",
            "④finetuned_visual": "微调视觉塔（类别锚点 CE）+ LR 探针",
        },
        "results": results,
        "hypothesis_verdict": hypo,
    }
    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 甲状腺四组对照（同一 test）===")
    for k in ("production_t2a_zero_shot", "frozen_linear_probe", "zero_shot_text",
              "finetuned_visual_on_thyroid"):
        v = results.get(k, {})
        if "auc_malignant_vs_benign" in v:
            print(f"  {k:32s} AUC={v['auc_malignant_vs_benign']} CI={v['auc_ci95']}")
        else:
            print(f"  {k:32s} {v.get('status', 'n/a')}: {v.get('reason', '')}")
    print(f"  hypothesis supported={hypo['supported']} (best={hypo['best_retrained_auc']})")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
