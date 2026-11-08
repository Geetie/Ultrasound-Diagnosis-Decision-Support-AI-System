"""正式评测 · CAMUS 心脏分割（LV 腔）—— p29 之外的第二块。

分两块，口径必须分清
------------------
① **可视化口径的分割 Dice（主）**：复现 `scripts/camus_seg_baseline.py` 的训练协议与
   **按患者 80/20 划分**（seed=42），对 `~/uai/models/cardiac_seg/unet_camus.pt` 在
   **同一 test 患者集**上重算 Dice/IoU，并补上历史报告缺失的 **bootstrap 95%CI**。
   历史数字：LV DSC 0.8395 / IoU 0.7280（`reports/camus_seg_baseline.json`），
   本脚本要给出「一致 / 不一致」的实证。

② **LVEF：做不了真评测，只能做误差传播**（如实标注）
   CAMUS 官方 EF 在 `patient_info_*.xlsx` 里，**本机没有这份文件**
   （已全盘 `find` 确认：无 `patient_info*` / `*lvef*` / `*.cfg`），
   因此 **MAE / RMSE / 相关系数无法计算** —— 没有真值就是没有真值。
   能做的、且有意义的：用单平面面积-长度法（modified Simpson）
        EDV = (8/3π)·A²/L   →   EF = (EDV−ESV)/EDV
   分别在 **GT 掩膜** 与 **模型预测掩膜** 上各算一遍 EF，
   报告两者的 **平均偏差 / 绝对偏差 / 相关系数** —— 这是「分割误差传到 EF 有多大」
   的误差传播测量，**不是**临床 EF 准确性验证。报告里 `scope` 固定写明。

标签口径（实测澄清）
------------------
CAMUS 标注 `labels_map: 1=心肌(MYO) / 2=左心室腔(LV) / 3=左心房(LA)`。
`~/uai/models/cardiac_seg/unet_camus.pt` 的 checkpoint 元数据写明
`task: CAMUS 左心室腔二值分割（label=2）`，`organ_segmentation.py` 注册表亦写 LV。
但仓库里的 `scripts/seg_gt_iou.py` 注释声称「unet_camus.pt 分割目标其实是心肌(MYO)」。
两者矛盾 → 本脚本**同时对 label 1 和 label 2 算 Dice**，用数据裁决（不靠文档吵架）。

用法（服务器，GPU 空闲）
----------------------
    cd ~/uai/src && PYTHONPATH=~/uai/src ~/uai/venv/bin/python \
        scripts/audit/p83_camus_seg_formal.py --report reports/p83_camus_seg_formal.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

SIZE = 256
FRAMES_PER_ITEM = 3
LV_LABEL = 2
MYO_LABEL = 1


# ---------------------------------------------------------------- 数据（复刻训练脚本）
def patient_of(path: Path) -> str:
    return path.stem.split("_")[0]


def list_pairs(root: Path) -> list[tuple[Path, Path, int]]:
    """与 camus_seg_baseline.list_pairs 同口径：每 half_sequence 取中间 3 帧。"""
    import nibabel as nib

    img_dir, mask_dir = root / "Images", root / "Masks"
    items: list[tuple[Path, Path, int]] = []
    for p in sorted(img_dir.glob("*.nii.gz")):
        stem = p.name.replace("_half_sequence.nii.gz", "")
        m = mask_dir / f"{stem}_half_sequence_gt.nii.gz"
        if not m.exists():
            continue
        mk = np.asarray(nib.load(str(m)).dataobj)
        n_frames = mk.shape[2] if mk.ndim == 3 else 1
        mid = n_frames // 2
        offsets = [max(0, mid + d) for d in range(-(FRAMES_PER_ITEM // 2), FRAMES_PER_ITEM // 2 + 1)]
        for fi in offsets:
            if (np.asarray(mk[:, :, fi]) == LV_LABEL).sum() >= 50:
                items.append((p, m, fi))
    return items


def split_by_patient(pairs: list[tuple[Path, Path, int]], seed: int = 42) -> tuple[list[int], list[int], list[str], set[str]]:
    """与训练脚本逐字相同的切分：rng=np.random.default_rng(42)；pats=sorted → shuffle。"""
    rng = np.random.default_rng(seed)
    by_pat: dict[str, list[int]] = defaultdict(list)
    for idx, p in enumerate(pairs):
        by_pat[patient_of(p[0])].append(idx)
    pats = sorted(by_pat)
    rng.shuffle(pats)
    n_test = max(1, int(len(pats) * 0.2))
    test_pats = set(pats[:n_test])
    train_idx = [i for i, p in enumerate(pairs) if patient_of(p[0]) not in test_pats]
    test_idx = [i for i, p in enumerate(pairs) if patient_of(p[0]) in test_pats]
    return train_idx, test_idx, pats, test_pats


def load_volume(root: Path, pairs: list[tuple[Path, Path, int]], label: int) -> tuple[np.ndarray, np.ndarray]:
    """把目标帧读进内存（与训练脚本 preload 同一套预处理：方形 256）。"""
    import nibabel as nib
    from PIL import Image

    n = len(pairs)
    imgs = np.zeros((n, SIZE, SIZE), dtype=np.uint8)
    masks = np.zeros((n, SIZE, SIZE), dtype=np.uint8)
    for i, (ip, mp, fi) in enumerate(pairs):
        arr = np.asarray(nib.load(str(ip)).dataobj)[:, :, fi]
        if arr.max() > 1.5:
            arr = arr / 255.0
        imgs[i] = np.asarray(
            Image.fromarray(np.clip(arr * 255.0, 0, 255).astype(np.uint8)).resize(
                (SIZE, SIZE), Image.BILINEAR
            )
        )
        mk = np.asarray(nib.load(str(mp)).dataobj)[:, :, fi]
        masks[i] = np.asarray(
            Image.fromarray((mk == label).astype(np.uint8)).resize((SIZE, SIZE), Image.NEAREST)
        )
    return imgs, masks


# ---------------------------------------------------------------- 指标
def dice_iou(pred: np.ndarray, gt: np.ndarray) -> tuple[float, float]:
    inter = float((pred & gt).sum())
    card = float(pred.sum() + gt.sum())
    union = card - inter
    d = (2 * inter / card) if card > 0 else 1.0
    i = (inter / union) if union > 0 else 1.0
    return d, i


def bootstrap_mean(vals: list[float], n_boot: int, seed: int = 20261013) -> list[float]:
    if not vals:
        return [None, None]
    rnd = random.Random(seed)
    means = []
    for _ in range(n_boot):
        pick = [vals[rnd.randrange(len(vals))] for _ in range(len(vals))]
        means.append(sum(pick) / len(pick))
    means.sort()
    return [round(means[int(0.025 * (len(means) - 1))], 4),
            round(means[int(0.975 * (len(means) - 1))], 4)]


# ---------------------------------------------------------------- 推理
def build_net(ckpt_path: Path, device: str):
    import torch
    from monai.networks.nets import UNet

    net = UNet(spatial_dims=2, in_channels=3, out_channels=1,
               channels=(32, 64, 128, 256, 320), strides=(2, 2, 2, 2),
               num_res_units=2, dropout=0.1).to(device)
    ck = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    net.load_state_dict(ck["state_dict"], strict=True)
    net.eval()
    return net, {k: v for k, v in ck.items() if k != "state_dict"}


def predict_probs(net, imgs: np.ndarray, device: str, bs: int = 16) -> np.ndarray:
    import torch

    out = []
    with torch.no_grad():
        for i in range(0, len(imgs), bs):
            x = torch.from_numpy(np.stack(imgs[i:i + bs], axis=0).copy())
            x = x.unsqueeze(1).repeat(1, 3, 1, 1).float().to(device) / 255.0
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = net(x)
            out.append(torch.sigmoid(logits.float()).cpu().numpy()[:, 0])
    return np.concatenate(out, axis=0)


# ---------------------------------------------------------------- 面积-长度法 EF
def area_length_volumes(masks: np.ndarray) -> np.ndarray:
    """单平面面积-长度法（modified Simpson）逐帧估算容积。

    A = 掩膜像素数；L = 掩膜 bbox 的较长边（近似长轴）。
    V = (8/(3π))·A²/L。EF 由患者级 EDV=max(V) / ESV=min(V) 在外部算
    （CAMUS half_sequence 覆盖整个心动周期）。

    同一公式同时施加于 GT 与预测掩膜，因此只有两者的**相对偏差**有意义 ——
    绝对容积/EF 数值不保证与 CAMUS 官方口径一致（官方用真值轮廓 + 专门公式）。
    """
    vols = np.full(len(masks), np.nan, dtype=np.float32)
    for i, m in enumerate(masks):
        a = float(m.sum())
        if a <= 0:
            continue
        ys, xs = np.nonzero(m)
        h = float(ys.max() - ys.min() + 1)
        w = float(xs.max() - xs.min() + 1)
        vols[i] = (8.0 / (3.0 * np.pi)) * (a * a) / max(1e-6, max(h, w))
    return vols


def ef_per_patient(vols: np.ndarray, pairs: list[tuple[Path, Path, int]]) -> dict[str, float]:
    """每患者 EDV=max(vol) ESV=min(vol)（CAMUS half_sequence 覆盖整个心动周期），EF=(EDV−ESV)/EDV。"""
    by_pat: dict[str, list[int]] = defaultdict(list)
    for i, p in enumerate(pairs):
        by_pat[patient_of(p[0])].append(i)
    out: dict[str, float] = {}
    for pat, idxs in by_pat.items():
        v = np.array([vols[i] for i in idxs], dtype=np.float64)
        v = v[np.isfinite(v)]
        if len(v) < 2:
            continue
        edv, esv = float(v.max()), float(v.min())
        if edv <= 0:
            continue
        out[pat] = (edv - esv) / edv
    return out


# ---------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--camus-dir", default=str(Path.home() / "uai" / "data" / "camus"))
    ap.add_argument("--ckpt", default=str(Path.home() / "uai" / "models" / "cardiac_seg" / "unet_camus.pt"))
    ap.add_argument("--report", default="reports/p83_camus_seg_formal.json")
    ap.add_argument("--baseline-json", default=str(Path.home() / "uai" / "reports" / "camus_seg_baseline.json"))
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import torch

    device = "cuda" if torch.cuda.is_available() else "cpu"
    root = Path(args.camus_dir).expanduser()
    t0 = time.time()

    pairs = list_pairs(root)
    if not pairs:
        print("CAMUS 数据没找到（Images/Masks 需为 *_half_sequence(.nii.gz) 配对）")
        return 2
    train_idx, test_idx, pats, test_pats = split_by_patient(pairs, args.seed)
    print(f"pairs={len(pairs)} patients={len(pats)} "
          f"train={len(train_idx)}/{len(pats)-len(test_pats)}pat test={len(test_idx)}/{len(test_pats)}pat")

    imgs, gt_lv = load_volume(root, pairs, LV_LABEL)
    _, gt_myo = load_volume(root, pairs, MYO_LABEL)
    print(f"preloaded imgs={imgs.shape}（{time.time()-t0:.0f}s）")

    net, ck_meta = build_net(Path(args.ckpt).expanduser(), device)
    probs = predict_probs(net, imgs, device)
    pred = (probs > 0.5).astype(np.uint8)

    # ---- ① 分割（label 2 = LV 腔，模型声明目标）
    lv_dices, lv_ious = [], []
    myo_dices = []
    for i in test_idx:
        d, iou = dice_iou(pred[i].astype(bool), gt_lv[i].astype(bool))
        lv_dices.append(d)
        lv_ious.append(iou)
        dm, _ = dice_iou(pred[i].astype(bool), gt_myo[i].astype(bool))
        myo_dices.append(dm)

    lv_d_mean = float(np.mean(lv_dices)) if lv_dices else float("nan")
    lv_i_mean = float(np.mean(lv_ious)) if lv_ious else float("nan")
    myo_d_mean = float(np.mean(myo_dices)) if myo_dices else float("nan")
    verdict = ("模型目标确为 LV 腔（label=2）" if lv_d_mean > myo_d_mean
               else "模型目标更像心肌 MYO（label=1）")

    # 逐患者均值（报告分布，避免"单个均值"掩盖失败病例）
    by_pat: dict[str, list[float]] = defaultdict(list)
    for i, d in zip(test_idx, lv_dices, strict=True):
        by_pat[patient_of(pairs[i][0])].append(d)
    per_patient = {p: round(float(np.mean(v)), 4) for p, v in sorted(by_pat.items())}
    worst = sorted(per_patient.items(), key=lambda kv: kv[1])[:5]

    # ---- ② EF 误差传播（无真值 → 只比 GT 掩膜 vs 预测掩膜）
    gt_vols = area_length_volumes(gt_lv)
    pr_vols = area_length_volumes(pred)
    ef_gt = ef_per_patient(gt_vols, pairs)
    ef_pr = ef_per_patient(pr_vols, pairs)
    common = sorted(set(ef_gt) & set(ef_pr))
    if common:
        a = np.array([ef_gt[p] for p in common])
        b = np.array([ef_pr[p] for p in common])
        diff = b - a
        corr = float(np.corrcoef(a, b)[0, 1]) if len(common) > 2 else float("nan")
        ef_block = {
            "n_patients": len(common),
            "mae": round(float(np.mean(np.abs(diff))), 4),
            "rmse": round(float(np.sqrt(np.mean(diff ** 2))), 4),
            "mean_bias": round(float(np.mean(diff)), 4),
            "pearson_r": round(corr, 4) if corr == corr else None,
            "ef_gt_mean": round(float(np.mean(a)), 4),
            "ef_pred_mean": round(float(np.mean(b)), 4),
            "mae_ci95": _boot_mae(a, b, args.bootstrap),
            "scope": "error_propagation_only（GT 掩膜 vs 预测掩膜，**不是**临床 EF 验证）",
        }
    else:
        ef_block = {"available": False, "reason": "患者级 EDV/ESV 不足，无法成对计算"}

    # ---- 与历史数字对比
    baseline = None
    bp = Path(args.baseline_json).expanduser()
    if bp.is_file():
        old = json.loads(bp.read_text(encoding="utf-8"))
        baseline = {
            "path": str(bp),
            "historical_lv_dice": old.get("lv_dice_mean"),
            "historical_lv_iou": old.get("lv_iou_mean"),
            "historical_images_test": old.get("images_test"),
            "historical_patients_test": old.get("patients_test"),
            "delta_dice": round(lv_d_mean - float(old.get("lv_dice_mean") or 0), 4)
            if lv_d_mean == lv_d_mean else None,
            "images_test_now": len(test_idx),
            "patients_test_now": len(test_pats),
        }

    report = {
        "probe": "p83_camus_seg_formal",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "patient_disjoint_holdout + ef_error_propagation",
        "scope_note": (
            "CAMUS (CC BY-NC-SA 4.0，**非商用**) 按患者 80/20 划分复跑训练脚本的同一测试集。"
            "LVEF 无官方真值文件（patient_info_*.xlsx 本机不存在）→ **不做** EF 准确性评测，"
            "只做「GT 掩膜 vs 预测掩膜」的 EF 误差传播。"
        ),
        "dataset": {
            "name": "CAMUS (Camus Dataset, 1000 half_sequences)",
            "license": "CC BY-NC-SA 4.0（非商用，仅评测）",
            "dir": str(root),
            "images_total": len(pairs),
            "patients_total": len(pats),
            "images_test": len(test_idx),
            "patients_test": len(test_pats),
            "frames_per_sequence": FRAMES_PER_ITEM,
            "eval_resolution": f"{SIZE}x{SIZE}（与训练协议一致）",
        },
        "checkpoint": {"path": args.ckpt, "meta": ck_meta},
        "segmentation": {
            "label_definition": "CAMUS labels_map: 1=MYO心肌, 2=LV左心室腔, 3=LA左心房",
            "lv_dice_mean": round(lv_d_mean, 4),
            "lv_dice_std": round(float(np.std(lv_dices)), 4),
            "lv_dice_ci95": bootstrap_mean(lv_dices, args.bootstrap),
            "lv_iou_mean": round(lv_i_mean, 4),
            "lv_iou_ci95": bootstrap_mean(lv_ious, args.bootstrap),
            "myo_dice_mean_if_model_is_lv": round(myo_d_mean, 4),
            "label_verdict": verdict,
            "per_patient_dice": per_patient,
            "worst5_patients": worst,
            "n_frames": len(lv_dices),
        },
        "lvef": ef_block,
        "lvef_blocked_reason": (
            "CAMUS 官方 EF 真值在 patient_info_*.xlsx，本机 **没有**（已 find 全盘确认）；"
            "没有真值就无法计算 MAE/RMSE/相关系数。上方 lvef 块是**误差传播**："
            "用同一面积-长度公式分别作用于 GT 掩膜与预测掩膜，衡量分割误差传到 EF 有多大。"
        ),
        "comparison_with_history": baseline,
        "wall_s": round(time.time() - t0, 1),
    }

    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    s = report["segmentation"]
    print(f"\n样本：{len(test_idx)} 帧 / {len(test_pats)} 患者（test 侧）")
    print(f"LV 腔 DSC = {s['lv_dice_mean']} ± {s['lv_dice_std']}  "
          f"95%CI {s['lv_dice_ci95']}  IoU = {s['lv_iou_mean']} 95%CI {s['lv_iou_ci95']}")
    print(f"口径裁决：{verdict}（LV DSC {s['lv_dice_mean']} vs MYO DSC {s['myo_dice_mean_if_model_is_lv']}）")
    if baseline:
        print(f"历史 DSC {baseline['historical_lv_dice']} → 本次 {s['lv_dice_mean']}"
              f"（Δ {baseline['delta_dice']}）")
    if "mae" in ef_block:
        print(f"LVEF 误差传播（n={ef_block['n_patients']} 患者）：MAE={ef_block['mae']} "
              f"RMSE={ef_block['rmse']} bias={ef_block['mean_bias']} r={ef_block['pearson_r']}")
    print("LVEF 准确性：**无法评测**（缺官方 EF 真值文件）")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


def _boot_mae(a: np.ndarray, b: np.ndarray, n_boot: int, seed: int = 20261013) -> list[float]:
    rnd = random.Random(seed)
    vals = []
    n = len(a)
    for _ in range(n_boot):
        idx = [rnd.randrange(n) for _ in range(n)]
        vals.append(float(np.mean(np.abs(b[idx] - a[idx]))))
    vals.sort()
    return [round(vals[int(0.025 * (len(vals) - 1))], 4),
            round(vals[int(0.975 * (len(vals) - 1))], 4)]


if __name__ == "__main__":
    sys.exit(main())
