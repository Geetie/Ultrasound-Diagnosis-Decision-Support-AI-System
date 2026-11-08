"""TN3K 官方 5 折交叉验证（p97）—— 把 0.7960 的单次点估计变成均值±标准差。

## 为什么要做这个（行动计划 1.2）

`p91_tn3k_seg_eval.py` 只在**官方 test（614 张）**上跑了一次，得 DSC 0.7960。
按 `ml-training-skill`（`references/biomedical.md:420`）：

> "Cross-validation: k-fold (k=5-10) is **mandatory** for small biomedical datasets;
> a single train/val/test split is **unreliable**"

单个 0.7960 **无法判断**它是"稳定水平"还是"这次划分运气好"。
本脚本用 TN3K 自带的 `tn3k-trainval-fold0~4.json` 做 5 折，报 **DSC 均值 ± 标准差**。

## 官方折文件的结构（实测，2026-11-08）

每份 16184 B，形如 `{"val": [...], "train": [...]}`，值是**整数下标**：

    fold0: val = [0, 5, 10, 15, ..., 2875]（576 个，步长 5）
           train = [1,2,3,4, 6,7,8,9, ...]（2303 个）

下标语义已与官方代码对齐（`haifangong/TRFE-Net-for-thyroid-nodule-segmentation`
的 `dataloaders/tn3k.py::make_dataset`）：

```python
img_names = os.listdir(root + '/' + name + '-image/')
img_names = sorted(img_names, key=lambda i: int(i.split(".")[0]))
for i in seed:            # seed 就是 json 里的整数列表
    img_name = img_names[i]
```

**即下标 = 按文件名数字升序排序后的位置**。本地实测该目录 0000.jpg…2878.jpg 共 2879 张，
fold 下标最大值 2878，完全吻合。

## ⚠️ 由此得出 1.3 的结论（重要）

5 份 fold 的 val 集合分别是 `下标 % 5 == 0/1/2/3/4`，**一个步长 5 的轮转切分**：
它按的是**排序后的文件名位置**，与任何患者信息无关（文件名是 0000..2878 的连续序号，
数据集里**没有患者 ID 字段**）。TN3K 官方描述是 3493 张图来自 **2421 个患者**
（≈1.44 张/人），所以同一患者的多张图**会**跨折 → **这是图像级划分，不是患者级**。

结论：所有基于该划分的 DSC 都带同患者泄漏风险，**偏乐观**，报告必须标注。

## 口径与纪律

- 训练配方**完全沿用** `scripts/tn3k_seg_unet.py`（MONAI UNet、25 epoch、AdamW lr1e-3
  + cosine、bs16、256px、翻转/旋转/缩放/仿射+亮度），只是把训练集从 2879 换成该折的 2303；
- **每折从零训练**（不复用 `unet_tn3k.pt` —— 那个权重见过全部 2879 张，用它评 trainval 折 = 泄漏）；
- 每折的 val **不参与**训练与选 epoch（与 p92/p96 同一纪律）；
- 泄漏自检：fold 的 train/val 图像交集必须 = 0、路径交集必须 = 0；
- 抽特征/加载顺序无 shuffle 参与评测，评测循环按 `list_pairs` 顺序 → 无错位风险
  （本脚本不经 DataLoader 抽特征，直接逐张 `load_pair`，故不涉及 p87 的 shuffle 坑）；
- DSC 两套口径都报（沿用 A.88）：`dice_mean`（乐观）/ `dice_mean_strict`（对外）。

## 用法（服务器，GPU）

    cd ~/uai/src && CUDA_VISIBLE_DEVICES=0 ~/uai/venv/bin/python \
        scripts/audit/p97_tn3k_official_5fold.py \
        --report reports/p97_tn3k_official_5fold.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import time
from pathlib import Path

FOLDS = range(5)


def _load_train_module():
    """按路径加载同目录的 tn3k_seg_unet.py（复用其 build_unet / load_pair / evaluate）。"""
    here = Path(__file__).resolve()
    for cand in (
        here.parents[2] / "scripts" / "tn3k_seg_unet.py",
        Path.home() / "uai" / "src" / "scripts" / "tn3k_seg_unet.py",
    ):
        if cand.exists():
            spec = importlib.util.spec_from_file_location("tn3k_seg_unet", cand)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod
    raise SystemExit("找不到 scripts/tn3k_seg_unet.py")


def read_fold(root: Path, fold: int) -> dict:
    p = root / f"tn3k-trainval-fold{fold}.json"
    if not p.is_file():
        raise SystemExit(f"缺少官方折文件：{p}")
    return json.loads(p.read_text(encoding="utf-8"))


def names_in_order(img_dir: Path) -> list[str]:
    """与官方 `make_dataset` 严格一致：按文件名数字升序排序。"""
    names = [p.name for p in img_dir.iterdir() if p.suffix.lower() == ".jpg"]
    return sorted(names, key=lambda i: int(i.split(".")[0]))


def pairs_for(img_dir: Path, mask_dir: Path, names: list[str], idx: list[int]) -> list:
    out = []
    missing = []
    for i in idx:
        nm = names[i]
        ip, mp = img_dir / nm, mask_dir / nm
        if not (ip.is_file() and mp.is_file()):
            missing.append(nm)
            continue
        out.append((ip, mp))
    if missing:
        print(f"  [warn] {len(missing)} 条下标指向缺失文件（已跳过，示例 {missing[:3]}）",
              flush=True)
    return out


def size_bucket(gt_area_ratio: float) -> str:
    """按金标准病灶占比分层（失败模式分析用）。"""
    if gt_area_ratio < 0.01:
        return "tiny(<1%)"
    if gt_area_ratio < 0.05:
        return "small(1-5%)"
    if gt_area_ratio < 0.20:
        return "medium(5-20%)"
    return "large(>=20%)"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TN3K 官方 5 折交叉验证（p97）")
    ap.add_argument("--tn3k-root", default=str(Path.home() / "uai/data/tn3k_dataset"))
    ap.add_argument("--report", default="reports/p97_tn3k_official_5fold.json")
    ap.add_argument("--epochs", type=int, default=25)
    ap.add_argument("--folds", default="0,1,2,3,4", help="要跑的折，逗号分隔（冒烟用 0）")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args(argv)

    import numpy as np
    import torch

    mod = _load_train_module()
    root = Path(args.tn3k_root).expanduser()
    for attr in ("ROOT", "DATA_ROOT", "TN3K_ROOT"):
        if hasattr(mod, attr):
            setattr(mod, attr, root)

    img_dir = root / "trainval-image"
    mask_dir = root / "trainval-mask"
    if not img_dir.is_dir():
        raise SystemExit(f"缺少目录：{img_dir}")

    names = names_in_order(img_dir)
    print(f"[data] trainval-image = {len(names)} 张（排序后 0000…{Path(names[-1]).stem}）",
          flush=True)

    device = torch.device(args.device if torch.cuda.is_available() else "cpu")
    SIZE, BS, LR = mod.SIZE, mod.BS, mod.LR
    print(f"[device] {device}  size={SIZE} bs={BS} lr={LR} epochs={args.epochs}", flush=True)

    want = [int(x) for x in args.folds.split(",") if x.strip()]
    fold_results = []
    t_all = time.monotonic()

    for fold in want:
        payload = read_fold(root, fold)
        tr_idx, va_idx = list(payload["train"]), list(payload["val"])

        # ---------------- 泄漏四格自检 ----------------
        tr_set, va_set = set(tr_idx), set(va_idx)
        ov_idx = tr_set & va_set
        tr_pairs = pairs_for(img_dir, mask_dir, names, tr_idx)
        va_pairs = pairs_for(img_dir, mask_dir, names, va_idx)
        tr_paths = {str(p[0]) for p in tr_pairs}
        va_paths = {str(p[0]) for p in va_pairs}
        ov_paths = tr_paths & va_paths
        if ov_idx or ov_paths:
            raise SystemExit(
                f"fold{fold} 泄漏自检失败：下标交集 {len(ov_idx)}、路径交集 {len(ov_paths)}")
        assert len(tr_pairs) == len(tr_idx), \
            f"fold{fold} train 条数不符 {len(tr_pairs)} vs {len(tr_idx)}"
        assert len(va_pairs) == len(va_idx), \
            f"fold{fold} val 条数不符 {len(va_pairs)} vs {len(va_idx)}"
        print(f"\n{'='*62}\n[fold {fold}] train={len(tr_pairs)} val={len(va_pairs)} "
              f"| 泄漏自检 下标交集={len(ov_idx)} 路径交集={len(ov_paths)} 视频/患者ID=不可得\n{'='*62}",
              flush=True)

        # 每折从零训练（绝不复用见过全量 trainval 的 unet_tn3k.pt）
        torch.manual_seed(42)
        np.random.seed(42)
        rng = np.random.default_rng(42)
        net = mod.build_unet().to(device)
        opt = torch.optim.AdamW(net.parameters(), lr=LR, weight_decay=0.01)
        sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=args.epochs)

        t0 = time.monotonic()
        hist = []
        for epoch in range(args.epochs):
            net.train()
            perm = rng.permutation(len(tr_pairs))
            losses = []
            for i in range(0, len(perm), BS):
                xs, ys = [], []
                for j in perm[i:i + BS]:
                    im = mod.load_pair(*tr_pairs[j])
                    if im is None:
                        continue
                    im = mod.augment(*im, rng)
                    xs.append(im[0])
                    ys.append(im[1])
                if not xs:
                    continue
                xs = torch.stack(xs).to(device)
                ys = torch.stack(ys).to(device)
                with torch.autocast("cuda", dtype=torch.bfloat16):
                    logits = net(xs)
                    loss = mod.dice_bce(logits, ys)
                opt.zero_grad()
                loss.backward()
                opt.step()
                losses.append(float(loss.detach()))
            sched.step()
            # 每 5 轮 + 末轮评一次（评的是本折 val，不参与训练）
            if (epoch + 1) % 5 == 0 or epoch == args.epochs - 1:
                d, io, _sd, _si, _st = mod.evaluate(net, va_pairs, device)
                rec = {"epoch": epoch + 1, "loss": round(float(np.mean(losses)), 4),
                       "val_dice": round(float(np.mean(d)), 4),
                       "val_iou": round(float(np.mean(io)), 4)}
                hist.append(rec)
                print(f"  ep{rec['epoch']:2d} loss={rec['loss']:.4f} "
                      f"val_DSC={rec['val_dice']:.4f} val_IoU={rec['val_iou']:.4f} "
                      f"({time.monotonic()-t0:.0f}s)", flush=True)

        dscs, ious, sdscs, sious, stats = mod.evaluate(net, va_pairs, device)
        summary = mod._summarize(dscs, ious, sdscs, sious, stats)

        # ---- 分层：按金标准病灶面积占比（失败模式） ----
        buckets: dict[str, list[tuple[float, float]]] = {}
        for (ip, mp), dv, io in zip(va_pairs, dscs, ious, strict=True):
            pair = mod.load_pair(ip, mp)
            if pair is None:
                continue
            gt = pair[1]
            ratio = float(gt.mean())
            buckets.setdefault(size_bucket(ratio), []).append((dv, io))
        by_size = {
            k: {
                "n": len(v),
                "dice_mean": round(float(np.mean([x[0] for x in v])), 4),
                "iou_mean": round(float(np.mean([x[1] for x in v])), 4),
                "dice_std": round(float(np.std([x[0] for x in v])), 4),
            }
            for k, v in sorted(buckets.items())
        }

        rec = {
            "fold": fold,
            "n_train": len(tr_pairs), "n_val": len(va_pairs),
            "leakage_check": {
                "index_overlap_train_val": len(ov_idx),
                "path_overlap_train_val": len(ov_paths),
                "patient_id_available": False,
            },
            **summary,
            "by_lesion_size": by_size,
            "history": hist,
            "wall_s": round(time.monotonic() - t0, 1),
        }
        fold_results.append(rec)
        print(f"  → fold{fold} DSC={rec['dice_mean']} strict={rec['dice_mean_strict']} "
              f"IoU={rec['iou_mean']} ({rec['wall_s']}s)", flush=True)

        del net, opt, sched
        torch.cuda.empty_cache()

    # ---------------- 汇总：均值 ± 标准差 ----------------
    def agg(key: str) -> dict:
        vals = [r[key] for r in fold_results if r.get(key) is not None]
        if not vals:
            return {"mean": None, "std": None, "n": 0}
        m = float(np.mean(vals))
        s = float(np.std(vals))
        return {
            "mean": round(m, 4), "std": round(s, 4),
            "min": round(float(min(vals)), 4), "max": round(float(max(vals)), 4),
            "n": len(vals),
            "values": vals,
        }

    agg_dice = agg("dice_mean")
    agg_strict = agg("dice_mean_strict")
    agg_iou = agg("iou_mean")

    # 池化（把 5 折的 val 预测拼起来）：需要每折 DSC 分布，这里按样本量加权近似
    report = {
        "probe": "p97_tn3k_official_5fold",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "why": ("单次划分（test 614 张跑一次得 0.7960）在生物医学小数据上不可靠："
                "ml-training-skill references/biomedical.md:420 明确要求 k-fold 5-10。"
                "本脚本用 TN3K 官方 tn3k-trainval-fold0~4.json 做 5 折交叉验证，"
                "报 DSC 均值 ± 标准差，替代单次点估计。"),
        "dataset": {
            "name": "TN3K", "root": str(root),
            "n_trainval_images": len(names),
            "fold_files": [f"tn3k-trainval-fold{f}.json" for f in want],
            "fold_index_semantics": (
                "json 里的整数 = 按文件名数字升序排序后的位置（与官方 "
                "haifangong/TRFE-Net dataloaders/tn3k.py::make_dataset 一致）"),
        },
        "protocol": {
            "architecture": "MONAI UNet(spatial_dims=2, channels=(32,64,128,256,320), "
                            "strides=(2,2,2,2), num_res_units=2, dropout=0.1)",
            "epochs": args.epochs, "lr": LR, "batch_size": BS, "input_size": SIZE,
            "optimizer": "AdamW(wd=0.01) + CosineAnnealingLR",
            "loss": "Dice+BCE (mod.dice_bce)",
            "augmentation": "flip h/v, rot90, scale 0.85-1.15, affine ±12°, brightness",
            "early_stop": "无（与 tn3k_seg_unet.py 一致：固定 25 epoch，末轮评测）",
            "note": ("每折**从零训练**，绝不复用 unet_tn3k.pt —— 那份权重已在全部 2879 张 "
                     "trainval 上训练过，用它评 trainval 的折 = 泄漏。"),
        },
        "folds": fold_results,
        "summary_5fold": {
            "dice_mean_of_folds": agg_dice,
            "dice_strict_mean_of_folds": agg_strict,
            "iou_mean_of_folds": agg_iou,
            "headline": (
                f"DSC = {agg_dice['mean']} ± {agg_dice['std']} "
                f"(n={agg_dice['n']} 折, min {agg_dice.get('min')} / max {agg_dice.get('max')})"
                if agg_dice["n"] else "无结果"),
        },
        "split_level_disclaimer": (
            "⚠️ **图像级划分，不是患者级**：5 份官方折 = 按排序后文件名下标的 "
            "`% 5 == k` 轮转切分，数据集无患者 ID 字段。TN3K 官方描述为 3493 张图来自 "
            "2421 个患者（≈1.44 张/人），同一患者的多张图会跨 train/val → "
            "本报告的 DSC **偏乐观**，不能当作注册申报级独立验证。"),
        "caliber_note": (
            "对外引用 **dice_strict_mean_of_folds**（剔除 empty_both 后的均值再跨折平均）。"
            "dice_mean 是乐观口径（空对空记 1.0），仅供与历史 0.7960 对照。"),
        "wall_s": round(time.monotonic() - t_all, 1),
    }

    rp = Path(args.report)
    if not rp.is_absolute():
        rp = Path.cwd() / rp
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n" + "=" * 62)
    print("=== TN3K 官方 5 折汇总 ===")
    for r in fold_results:
        print(f"  fold{r['fold']}: DSC={r['dice_mean']:.4f} (strict {r['dice_mean_strict']:.4f}) "
              f"IoU={r['iou_mean']:.4f}  n_val={r['n_val']}")
    print(f"\n  DSC  = {agg_dice['mean']} ± {agg_dice['std']} (n={agg_dice['n']})")
    print(f"  strict DSC = {agg_strict['mean']} ± {agg_strict['std']}")
    print(f"  IoU  = {agg_iou['mean']} ± {agg_iou['std']}")
    print(f"\n  对照：单次官方 test 614 张 = 0.7960（p91）")
    print(f"  口径警告：**图像级划分，非患者级** → 偏乐观")
    print(f"报告：{rp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
