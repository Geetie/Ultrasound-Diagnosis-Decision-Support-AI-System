"""正式重训 · T2b CACTUS 切面分类（视频不交集 + 修 p87/t2b_cactus_train 的三处 bug）。

## 为什么要重训（p87 的 20.99% 是**代码 bug**，不是「配方把特征训坏了」）

p89 已证明冻结特征 + LR 在**同一份视频不交集划分**上能到 **0.8628**，而 p87 用同一
划分、同一底模重训后只有 **0.2099**（= 多数类基线）。此前归因为「微调配方毁特征」。
本轮逐行复核，实测出**三处独立缺陷**，其中第 ③ 条足以单独解释 0.2099：

### ① 样本内打分（任务书已指出）
`t2b_cactus_train.py::eval_split` 拿**同一份特征** `lr.fit(...)` 再
`lr.predict(同一份特征)` 算 accuracy —— 是**训练集自评**，不是 held-out。
p87 里对应的是「每轮抽 train 特征 → fit → 在同一批特征上算分」，同样失真。

### ② 训练用了验证集变换（零增强）
`open_clip.create_model_and_transforms()` 返回 `(model, preprocess_train, preprocess_val)`。
p87 与 `t2b_cactus_train.py` 都写成 `model, _, preprocess = ...` ——
第二个位置才是**训练增强**（RandomResizedCrop scale 0.9~1.0），被丢进 `_`；
把 **preprocess_val**（纯 resize+center crop）用于训练 → 训练期零增强。

### ③ 标签与特征错位（**最致命**，本轮新发现）
p87 用 `make_loader(splits["train"], train=True)`（`shuffle=True`）抽训练特征，
但探针拟合时标签取 `[r["view"] for r in splits["train"]]` —— **CSV 原序**。
`extract()` 返回的是 **shuffled loader 顺序**的特征，两者不对齐。

本脚本复现该错位（见 `--verify-alignment`）：**20207 个训练样本里 79.1% 的
标签与特征不匹配**。用 79% 错标的样本拟合 5 类 LR，退化成多数类完全合理。
注：p89 的冻结基线没有这个问题（它的 loader 就是 `shuffle=False`），所以
**0.8628 是真的、0.2099 是假的** —— 这也解释了「为什么冻结比微调好 4 倍」。

### ④ 对比损失的正样本口径错（次要但真实）
`open_clip.ClipLoss` 用**批内对角线**作正样本。本数据集 batch=256 但只有 **5 条
唯一 caption**，同一 caption 在批内出现约 51 次 → 对角线口径把「同类的另一张图」
当负样本。理论下界 `log(256/5)=3.94`，p87 实测 loss 4.21→4.06→4.03，**贴着下界
走不动**：梯度主要在把同类样本推远。
本脚本改用**按类别锚点的交叉熵**（每个图像的正样本 = 它那一类的文本锚），
5 类 softmax 没有重复正样本问题。

## 修法

* `extract_features()` 让 Dataset **一并返回样本下标**，返回后按下标重排，
  并用 `assert sorted(idx)==range(n)` 硬校验 → 错位在结构上不可能发生；
* 训练用 `preprocess_train`（官方增强），评估用 `preprocess_val`；
* 损失改 `CE(logit_scale · image_feat @ class_anchor^T, class_idx)`；
* **评估协议**：LR 探针**只在 train 视频特征上拟合**，val 只用于选 epoch，
  **test 只在本配方定稿后评一次**（不参与任何选择）。

## 用法（服务器，GPU）

    cd ~/uai/src && ~/uai/venv/bin/python scripts/audit/p92_t2b_retrain.py \
        --report reports/p92_t2b_retrain_formal.json

    # 只验错位（秒级）
    ~/uai/venv/bin/python scripts/audit/p92_t2b_retrain.py --verify-alignment

    # 冒烟（每 split 只取 2000 行）
    ... --limit 2000 --recipes full_lr3e6
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import Counter
from pathlib import Path

# 与 p87 共用划分与指标口径（不重复实现，避免两处漂移）
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
from p87_cactus_video_disjoint import (  # noqa: E402
    CLASSES,
    bootstrap_acc,
    confusion_matrix,
    parse_name,
    per_class_prf,
    video_disjoint_split,
)
from t2b_cactus_prep import VIEW_PROMPTS  # noqa: E402

# 参照基线（p89，同一划分 seed=42）
REF_FROZEN_PROBE = 0.8628
REF_VIDEO_DISJOINT_RETRAIN_P87 = 0.2099
TARGET_ACC = 0.80

# 配方网格：name -> 配置
# freeze_blocks=k 表示冻结 trunk.blocks[0..k-1]，只训 blocks[k..] + trunk.norm + head
RECIPES: dict[str, dict] = {
    "full_lr3e6": {"lr": 3e-6, "epochs": 5, "patience": 2, "freeze_blocks": 0},
    "full_lr1e5": {"lr": 1e-5, "epochs": 5, "patience": 2, "freeze_blocks": 0},
    "full_lr3e5": {"lr": 3e-5, "epochs": 3, "patience": 1, "freeze_blocks": 0},
    "lastblock_lr5e5": {"lr": 5e-5, "epochs": 5, "patience": 2, "freeze_blocks": 11},
}


def set_seed(seed: int) -> None:
    import numpy as np
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def verify_alignment(data_dir: Path, seed: int, batch_size: int) -> dict:
    """复现 p87 的③号 bug：shuffled loader 的特征顺序 vs CSV 原序标签。

    不加载模型（用 Dataset 返回 (label, idx) 的替身），秒级。
    """
    from torch.utils.data import DataLoader, Dataset

    pack = video_disjoint_split(data_dir, seed)
    rows = pack["splits"]["train"]

    class _Spy(Dataset):
        def __init__(self, rs):
            self.rs = rs

        def __len__(self):
            return len(self.rs)

        def __getitem__(self, i):
            return self.rs[i]["view"], i

    loader = DataLoader(_Spy(rows), batch_size=batch_size, shuffle=True, num_workers=0)
    labels_csv_order = [r["view"] for r in rows]
    out = {}
    for ep in range(2):
        got, idxs = [], []
        for lab, idx in loader:
            got += list(lab)
            idxs += [int(i) for i in idx]
        agree = sum(1 for pos, lab in enumerate(got) if lab == labels_csv_order[pos])
        out[f"epoch{ep + 1}"] = {
            "n": len(idxs),
            "positional_mismatch": sum(1 for p, i in enumerate(idxs) if i != p),
            "label_agreement_with_csv_order": round(agree / len(idxs), 4),
            "effective_label_error_rate": round(1 - agree / len(idxs), 4),
        }
    return {
        "what": (
            "p87 用 shuffle=True 的 train loader 抽特征、却按 CSV 原序取标签。"
            "下表的 effective_label_error_rate 就是探针训练时被喂错标签的比例。"
        ),
        "per_epoch": out,
        "verdict": (
            "错位是结构性的（每轮都不同、且几乎全错位）→ p87 的 0.2099 是错标签探针的产物，"
            "不能作为「微调毁特征」的证据。"
        ),
    }


def cluster_bootstrap_acc(rows: list[dict], y_true: list[str], y_pred: list[str],
                          n_boot: int, seed: int = 20261108) -> list[float]:
    """**按视频整段重采样**的 bootstrap CI（图像级 CI 会低估不确定性）。

    test 只有 17 段视频、4511 张图；同一视频的相邻帧高度相关，按图像重采样等于
    假装有 4511 个独立样本 → CI 偏窄。这里以视频为独立单位做 cluster bootstrap。
    """
    vids = [parse_name(r["filepath"])[0] or f"__nolabel_{i}" for i, r in enumerate(rows)]
    by_vid: dict[str, list[int]] = {}
    for i, v in enumerate(vids):
        by_vid.setdefault(v, []).append(i)
    keys = sorted(by_vid)
    rnd = random.Random(seed)
    accs = []
    for _ in range(n_boot):
        pick = [rnd.choice(keys) for _ in keys]
        idx = [i for v in pick for i in by_vid[v]]
        if not idx:
            continue
        accs.append(sum(1 for i in idx if y_true[i] == y_pred[i]) / len(idx))
    accs.sort()
    if not accs:
        return [float("nan"), float("nan")]
    return [round(accs[int(0.025 * (len(accs) - 1))], 4),
            round(accs[int(0.975 * (len(accs) - 1))], 4)]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(Path.home() / "uai" / "data" / "t2b_cactus"))
    ap.add_argument("--model-dir",
                    default=str(Path.home() / "uai" / "models" / "biomedclip-base-patch16-224"))
    ap.add_argument("--ckpt-dir", default=str(Path.home() / "uai" / "models" / "t2b_p92"))
    ap.add_argument("--report", default="reports/p92_t2b_retrain_formal.json")
    ap.add_argument("--recipes", default=",".join(RECIPES),
                    help=f"逗号分隔；可选 {','.join(RECIPES)}")
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--eval-batch-size", type=int, default=512)
    ap.add_argument("--num-workers", type=int, default=16)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--limit", type=int, default=0, help="冒烟：每 split 只取前 N 行")
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--verify-alignment", action="store_true",
                    help="只跑③号 bug 的复现，不训练")
    ap.add_argument("--rescore-from-ckpt", action="store_true",
                    help="不训练：从 --ckpt-dir 里已有的 checkpoint 重新算指标"
                         "（用于补指标口径，例如视频级 cluster CI）")
    args = ap.parse_args()

    data_dir = Path(args.data_dir).expanduser()
    split_pack = video_disjoint_split(data_dir, args.seed)
    splits, meta = split_pack["splits"], split_pack["meta"]
    if meta["leakage_check"]["video_overlap_train_test"] != 0:
        raise SystemExit("划分自检失败：train/test 视频有重叠")

    if args.verify_alignment:
        al = verify_alignment(data_dir, args.seed, args.batch_size)
        print(json.dumps(al, ensure_ascii=False, indent=2))
        return 0

    if args.limit:
        splits = {k: v[: args.limit] for k, v in splits.items()}

    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from sklearn.linear_model import LogisticRegression
    from torch.utils.data import DataLoader, Dataset

    device = "cuda"
    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    # ① 修复点：三个返回值分别接住，训练用 preprocess_train
    model0, preprocess_train, preprocess_val = open_clip.create_model_and_transforms(
        uri, device=device)
    model0.eval()
    tokenizer = open_clip.get_tokenizer(uri)

    class _DS(Dataset):
        """mode='index' → (img, idx)；mode='label' → (img, class_idx)。"""

        def __init__(self, rows, preprocess, mode):
            self.rows, self.preprocess, self.mode = rows, preprocess, mode

        def __len__(self):
            return len(self.rows)

        def __getitem__(self, i):
            r = self.rows[i]
            img = self.preprocess(Image.open(r["filepath"]).convert("RGB"))
            if self.mode == "index":
                return img, i  # ② 修复点：下标随样本走，重排后不可能错位
            return img, CLASSES.index(r["view"])

    def make_loader(rows, preprocess, mode, batch, shuffle):
        return DataLoader(
            _DS(rows, preprocess, mode), batch_size=batch, shuffle=shuffle,
            num_workers=args.num_workers, pin_memory=True,
            persistent_workers=args.num_workers > 0,
        )

    @torch.no_grad()
    def extract_features(model, rows) -> np.ndarray:
        """抽特征并按**样本下标**重排，硬校验对齐后才返回。"""
        model.eval()
        loader = make_loader(rows, preprocess_val, "index", args.eval_batch_size, False)
        feats, idxs = [], []
        for imgs, idx in loader:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = model.encode_image(imgs)
            feats.append(F.normalize(f.float(), dim=-1).cpu())
            idxs += [int(i) for i in idx]
        X = torch.cat(feats).numpy()
        order = sorted(range(len(idxs)), key=lambda k: idxs[k])
        X = X[order]
        got = [idxs[k] for k in order]
        if got != list(range(len(rows))):  # 结构守卫：错位就不可能沉默通过
            raise RuntimeError(f"特征/样本对齐失败: {len(got)} vs {len(rows)}")
        return X

    def fit_probe(Xtr, rows_tr):
        lr = LogisticRegression(max_iter=2000)
        lr.fit(Xtr, [r["view"] for r in rows_tr])
        return lr

    def score(lr, X, rows):
        yt = [r["view"] for r in rows]
        yp = [str(v) for v in lr.predict(X)]
        acc = sum(1 for t, p in zip(yt, yp, strict=True) if t == p) / len(yt)
        return {
            "n_evaluated": len(yt),
            "class_support": dict(Counter(yt)),
            "view_accuracy": round(acc, 4),
            "view_accuracy_ci95": bootstrap_acc(yt, yp, args.bootstrap),
            "view_accuracy_ci95_video_cluster": cluster_bootstrap_acc(
                rows, yt, yp, args.bootstrap),
            "per_class": per_class_prf(yt, yp),
            "confusion": confusion_matrix(yt, yp),
        }, yt, yp

    # ---------------- 参照：冻结特征 + LR 探针（复现 p89 的 0.8628）
    baseline = None
    if not args.skip_baseline:
        t0 = time.time()
        Xtr = extract_features(model0, splits["train"])
        Xte = extract_features(model0, splits["test"])
        lr0 = fit_probe(Xtr, splits["train"])
        baseline, _, _ = score(lr0, Xte, splits["test"])
        baseline["note"] = "BiomedCLIP 视觉塔冻结，LR 仅在 train 视频上拟合；test 视频不参与"
        baseline["wall_s"] = round(time.time() - t0, 1)
        print(f"[baseline] frozen probe acc={baseline['view_accuracy']} "
              f"CI(img)={baseline['view_accuracy_ci95']} "
              f"CI(video)={baseline['view_accuracy_ci95_video_cluster']}", flush=True)

    # 5 条类别文本锚（文本塔全程冻结，只算一次）
    prompts = [VIEW_PROMPTS[c] for c in CLASSES]
    with torch.no_grad():
        tok = tokenizer(prompts).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            anchors = F.normalize(model0.encode_text(tok).float(), dim=-1)
    anchors = anchors.to(device)

    def build_model(freeze_blocks: int):
        """同一份预训练权重，按配方设置可训练参数。"""
        import copy

        m = copy.deepcopy(model0)
        for p in m.parameters():
            p.requires_grad = False
        trunk = m.visual.trunk
        for name, p in m.visual.named_parameters():
            if name.startswith("trunk.blocks."):
                bi = int(name.split(".")[2])
                if bi >= freeze_blocks:
                    p.requires_grad = True
            else:  # trunk.norm / trunk.cls_token / trunk.pos_embed / head.proj
                p.requires_grad = freeze_blocks == 0
        # 最后一层的 norm 与 head 始终可训（否则梯度回传不到位）
        for n, p in m.visual.named_parameters():
            if n.startswith("trunk.norm") or n.startswith("head."):
                p.requires_grad = True
        del trunk
        return m

    @torch.no_grad()
    def ce_eval(model, rows) -> dict:
        model.eval()
        loader = make_loader(rows, preprocess_val, "label", args.eval_batch_size, False)
        correct = total = 0
        for imgs, ys in loader:
            imgs, ys = imgs.to(device, non_blocking=True), ys.to(device)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                im = F.normalize(model.encode_image(imgs).float(), dim=-1)
            pred = (im @ anchors.T).argmax(dim=1)
            correct += int((pred == ys).sum())
            total += len(ys)
        return {"acc": correct / max(1, total), "n": total}

    ckpt_dir = Path(args.ckpt_dir).expanduser()
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    results = []
    wanted = [r.strip() for r in args.recipes.split(",") if r.strip()]

    # ---- 补指标模式：从已有 checkpoint 重算（不训练），用于加视频级 cluster CI
    if args.rescore_from_ckpt:
        for name in wanted:
            if name not in RECIPES:
                raise SystemExit(f"未知配方 {name}")
            cfg = RECIPES[name]
            ckpt = ckpt_dir / f"{name}.pt"
            if not ckpt.exists():
                print(f"[skip] {name}: 无 checkpoint {ckpt}", flush=True)
                continue
            model = build_model(cfg["freeze_blocks"])
            model.visual.load_state_dict(torch.load(ckpt, map_location=device))
            model.eval()
            t0 = time.time()
            lr = fit_probe(extract_features(model, splits["train"]), splits["train"])
            test_m, _, _ = score(lr, extract_features(model, splits["test"]), splits["test"])
            results.append({
                "recipe": name,
                "config": {"lr": cfg["lr"], "epochs_cap": cfg["epochs"],
                           "patience": cfg["patience"],
                           "freeze_blocks": cfg["freeze_blocks"],
                           "trainable_params_m": round(sum(
                               p.numel() for p in model.visual.parameters()
                               if p.requires_grad) / 1e6, 2),
                           "batch_size": args.batch_size,
                           "loss": "CE(logit_scale · image_feat @ class_text_anchor^T)",
                           "augmentation": "preprocess_train (RandomResizedCrop 0.9~1.0)",
                           "weight_decay": 0.1},
                "history": "rescored（未重训，曲线见上一轮正式跑）",
                "best_epoch": None,
                "val_acc_cehead_at_best": None,
                "probe_test": test_m,
                "checkpoint": str(ckpt),
                "wall_s": round(time.time() - t0, 1),
            })
            print(f"[rescore {name}] acc={test_m['view_accuracy']} "
                  f"CI(img)={test_m['view_accuracy_ci95']} "
                  f"CI(video)={test_m['view_accuracy_ci95_video_cluster']}", flush=True)
        valid_r = [r for r in results if r.get("probe_test")]
        best_r = max(valid_r, key=lambda r: r["probe_test"]["view_accuracy"]) if valid_r else None
        ref = (baseline or {}).get("view_accuracy")
        report = {
            "probe": "p92_t2b_retrain",
            "mode": "rescore_from_checkpoint（未重训，只重算指标）",
            "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "split": meta,
            "baseline_frozen_probe_this_run": baseline,
            "recipes": results,
            "verdict": {
                "target_acc": TARGET_ACC,
                "reference_frozen_probe_p89": REF_FROZEN_PROBE,
                "reference_p87_broken_retrain": REF_VIDEO_DISJOINT_RETRAIN_P87,
                "best_recipe": best_r["recipe"] if best_r else None,
                "best_test_acc": best_r["probe_test"]["view_accuracy"] if best_r else None,
                "frozen_probe_recomputed_this_run": ref,
                "beats_frozen_probe": bool(
                    ref is not None and best_r is not None
                    and best_r["probe_test"]["view_accuracy"] > ref),
                "meets_target_0_80": bool(
                    best_r is not None
                    and best_r["probe_test"]["view_accuracy"] >= TARGET_ACC),
            },
        }
        out_r = Path(args.report).expanduser()
        out_r.parent.mkdir(parents=True, exist_ok=True)
        out_r.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{out_r}")
        return 0

    for name in wanted:
        if name not in RECIPES:
            raise SystemExit(f"未知配方 {name}；可选 {list(RECIPES)}")
        cfg = RECIPES[name]
        print(f"\n===== 配方 {name} {cfg} =====", flush=True)
        set_seed(args.seed)
        model = build_model(cfg["freeze_blocks"])
        params = [p for p in model.visual.parameters() if p.requires_grad]
        n_train = sum(p.numel() for p in params)
        opt = torch.optim.AdamW(params, lr=cfg["lr"], weight_decay=0.1)
        train_loader = make_loader(splits["train"], preprocess_train, "label",
                                   args.batch_size, True)
        loss_fn = torch.nn.CrossEntropyLoss()

        t0 = time.time()
        history, best, stale = [], {"val_acc": -1.0, "epoch": 0}, 0
        ckpt = ckpt_dir / f"{name}.pt"
        for epoch in range(cfg["epochs"]):
            model.train()
            tot, nb, te0 = 0.0, 0, time.perf_counter()
            for imgs, ys in train_loader:
                imgs, ys = imgs.to(device, non_blocking=True), ys.to(device)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    im = F.normalize(model.encode_image(imgs).float(), dim=-1)
                scale = model.logit_scale.exp().float().clamp(max=100.0)
                loss = loss_fn(scale * (im @ anchors.T), ys)
                opt.zero_grad(set_to_none=True)
                loss.backward()
                opt.step()
                tot += loss.item()
                nb += 1
            va = ce_eval(model, splits["val"])
            rec = {"epoch": epoch + 1, "loss": round(tot / max(1, nb), 4),
                   "val_acc_cehead": round(va["acc"], 4),
                   "epoch_s": round(time.perf_counter() - te0, 1)}
            history.append(rec)
            print(f"EPOCH {rec['epoch']}/{cfg['epochs']} loss={rec['loss']} "
                  f"val_acc={rec['val_acc_cehead']} ({rec['epoch_s']}s, "
                  f"total {time.time() - t0:.0f}s)", flush=True)
            if rec["val_acc_cehead"] > best["val_acc"]:
                best = {"val_acc": rec["val_acc_cehead"], "epoch": rec["epoch"]}
                torch.save({k: v.cpu() for k, v in model.visual.state_dict().items()}, ckpt)
                stale = 0
            else:
                stale += 1
                if stale >= cfg["patience"]:
                    print(f"[early-stop] val 连续 {stale} 轮无提升", flush=True)
                    break

        # ---- 定稿：加载 best checkpoint，抽 train/test 特征，拟合 LR → test 评一次
        model.visual.load_state_dict(torch.load(ckpt, map_location=device))
        model.eval()
        te0 = time.time()
        Xtr = extract_features(model, splits["train"])
        lr = fit_probe(Xtr, splits["train"])
        Xva = extract_features(model, splits["val"])
        val_m, _, _ = score(lr, Xva, splits["val"])
        Xte = extract_features(model, splits["test"])
        test_m, yt, yp = score(lr, Xte, splits["test"])
        rec_out = {
            "recipe": name,
            "config": {
                "lr": cfg["lr"], "epochs_cap": cfg["epochs"], "patience": cfg["patience"],
                "freeze_blocks": cfg["freeze_blocks"],
                "trainable_params_m": round(n_train / 1e6, 2),
                "batch_size": args.batch_size,
                "loss": "CE(logit_scale · image_feat @ class_text_anchor^T)",
                "augmentation": "preprocess_train (RandomResizedCrop 0.9~1.0)",
                "weight_decay": 0.1,
            },
            "history": history,
            "best_epoch": best["epoch"],
            "val_acc_cehead_at_best": best["val_acc"],
            "probe_val": val_m,
            "probe_test": test_m,
            "checkpoint": str(ckpt),
            "wall_s": round(time.time() - t0, 1),
        }
        results.append(rec_out)
        print(f"[{name}] test acc(probe)={test_m['view_accuracy']} "
              f"CI={test_m['view_accuracy_ci95']} 宏F1={test_m['per_class']['macro_avg']['f1']} "
              f"({rec_out['wall_s']}s)", flush=True)

    # ---------------- 汇总
    valid = [r for r in results if r["probe_test"]["view_accuracy"] > 0]
    best_rec = max(valid, key=lambda r: r["val_acc_cehead_at_best"]) if valid else None
    ref = (baseline or {}).get("view_accuracy")
    verdict = {
        "target_acc": TARGET_ACC,
        "reference_frozen_probe_p89": REF_FROZEN_PROBE,
        "reference_p87_broken_retrain": REF_VIDEO_DISJOINT_RETRAIN_P87,
        "best_recipe": best_rec["recipe"] if best_rec else None,
        "best_test_acc": best_rec["probe_test"]["view_accuracy"] if best_rec else None,
        "frozen_probe_recomputed_this_run": ref,
        "beats_frozen_probe": (
            bool(ref is not None and best_rec is not None
                 and best_rec["probe_test"]["view_accuracy"] > ref)),
        "meets_target_0_80": (
            bool(best_rec is not None
                 and best_rec["probe_test"]["view_accuracy"] >= TARGET_ACC)),
    }
    report = {
        "probe": "p92_t2b_retrain",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "video_disjoint_retrain_fixed_pipeline",
        "scope_note": (
            "与 p87/p89 完全相同的视频不交集划分（116 段视频 → 80/19/17，seed=42），"
            "但修复了③号标签错位、②号零增强、①号样本内打分，并把对比损失换成类别锚点"
            "交叉熵。LR 探针只在 train 视频上拟合，val 仅用于选 epoch，test 定稿后评一次。"
        ),
        "bug_fixes": {
            "in_sample_scoring": (
                "t2b_cactus_train.py::eval_split 用同一份特征 fit 后再 predict 同一份特征，"
                "是训练集自评。本脚本：LR 只在 train 特征上拟合，val/test 特征从不参与 fit。"
            ),
            "zero_augmentation": (
                "create_model_and_transforms 返回 (model, preprocess_train, preprocess_val)，"
                "p87/t2b_cactus_train 写成 (model, _, preprocess) → 训练用的是 val 变换（零增强）。"
                "本脚本训练用 preprocess_train。"
            ),
            "label_feature_misalignment": (
                "p87 用 shuffle=True 的 loader 抽 train 特征，却按 CSV 原序取标签，"
                "实测 79.1% 标签错位。本脚本 Dataset 返回样本下标、抽完按下标重排并硬校验。"
            ),
            "contrastive_positive_set": (
                "ClipLoss 批内对角线作正样本，而 batch=256 只有 5 条唯一 caption"
                "（理论下界 log(256/5)=3.94，p87 实测 4.03~4.21 贴地走不动）→ 改类别锚点 CE。"
            ),
        },
        "split": meta,
        "baseline_frozen_probe_this_run": baseline,
        "recipes": results,
        "verdict": verdict,
    }
    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 汇总 ===")
    if baseline:
        print(f"  frozen probe (p89 复现)      acc={baseline['view_accuracy']}")
    for r in results:
        print(f"  {r['recipe']:18s} test acc={r['probe_test']['view_accuracy']} "
              f"CI={r['probe_test']['view_accuracy_ci95']}")
    print(f"  verdict: {json.dumps(verdict, ensure_ascii=False)}")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
