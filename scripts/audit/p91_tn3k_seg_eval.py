"""TN3K 分割独立评测（p91）—— 只评测、不训练。

## 为什么需要

`scripts/tn3k_seg_unet.py` 已含完整评测逻辑（`evaluate()`，含 A.88 的
**乐观/保守双口径**），但它**只在校验训练时顺手跑一次**，从没产出过独立的评测报告
（`reports/tn3k_seg_unet.json` 不存在）。

而 TN3K 是甲状腺（主线科室）唯一的分割公开集。没有独立报告 =
**主线科室的分割能力没有可辩护数字**。

本脚本**复用** `tn3k_seg_unet.py` 里的 `list_pairs / load_pair / build_unet /
evaluate / _summarize`（不复制逻辑、不重复造轮子），只补：

1. **不训练**，直接加载已训好的权重；
2. **bootstrap 置信区间**（2000 次、固定种子），让 DSC 有误差棒；
3. **per-image 分布**（P10/P50/P90），避免只看均值被极端值掩盖；
4. 写进 `reports/p91_tn3k_seg_eval.json`。

## 口径纪律（沿用 A.88，不放松）

- `dice_mean`：原口径（两边都空记 1.0，**乐观**）
- `dice_mean_strict`：剔除「两边都空」后的均值（**保守/可辩护**）
- 两个都报，且报告里写明哪个该用于对外

## 用法

    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p91_tn3k_seg_eval.py \
        --tn3k-root ~/uai/data/tn3k_dataset \
        --weights ~/uai/models/nodule_seg/unet_tn3k.pt \
        --report reports/p91_tn3k_seg_eval.json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import random
import sys
import time
from pathlib import Path

BOOTSTRAP_N = 2000
BOOTSTRAP_SEED = 20261108


def _load_train_module():
    """按路径加载同目录的 tn3k_seg_unet.py（复用其评测逻辑）。"""
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


def bootstrap_ci(values: list[float]) -> tuple[float, float]:
    rng = random.Random(BOOTSTRAP_SEED)
    if not values:
        return (float("nan"), float("nan"))
    means = []
    n = len(values)
    for _ in range(BOOTSTRAP_N):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return (round(means[int(0.025 * len(means))], 4), round(means[int(0.975 * len(means))], 4))


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    return round(s[min(len(s) - 1, max(0, int(q * (len(s) - 1))))], 4)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="TN3K 分割独立评测（p91）")
    ap.add_argument("--tn3k-root", default=str(Path.home() / "uai/data/tn3k_dataset"))
    ap.add_argument("--weights", default=str(Path.home() / "uai/models/nodule_seg/unet_tn3k.pt"))
    ap.add_argument("--report", default="reports/p91_tn3k_seg_eval.json")
    ap.add_argument("--device", default="cuda")
    args = ap.parse_args(argv)

    root = Path(args.tn3k_root).expanduser()
    wpath = Path(args.weights).expanduser()
    if not wpath.exists():
        print(json.dumps({
            "probe": "p91_tn3k_seg_eval", "status": "blocked",
            "reason": f"权重不存在: {wpath}",
            "unblock_condition": "先跑 scripts/tn3k_seg_unet.py 训练并保存 unet_tn3k.pt",
        }, ensure_ascii=False, indent=2))
        return 1

    mod = _load_train_module()
    # 让被复用的 list_pairs 指向本机真实路径（脚本内默认是服务器绝对路径）
    for attr in ("ROOT", "DATA_ROOT", "TN3K_ROOT"):
        if hasattr(mod, attr):
            setattr(mod, attr, root)

    import numpy as np  # noqa: PLC0415  (延迟导入：无 torch 时先给出清晰错误)
    import torch  # noqa: PLC0415

    test_pairs = mod.list_pairs("test")
    if not test_pairs:
        print(json.dumps({
            "probe": "p91_tn3k_seg_eval", "status": "blocked",
            "reason": f"未找到 test 图文对（root={root}）",
        }, ensure_ascii=False, indent=2))
        return 1

    device = torch.device(args.device if torch.cuda.is_available() else "cpu")
    net = mod.build_unet()
    ckpt = torch.load(wpath, map_location="cpu", weights_only=False)
    sd = ckpt.get("state_dict", ckpt) if isinstance(ckpt, dict) else ckpt
    net.load_state_dict(sd)
    net.to(device)

    t0 = time.monotonic()
    dscs, ious, strict_dscs, strict_ious, stats = mod.evaluate(net, test_pairs, device)
    summary = mod._summarize(dscs, ious, strict_dscs, strict_ious, stats)

    report = {
        "probe": "p91_tn3k_seg_eval",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "public_dataset_holdout_lower_bound",
        "scope_note": (
            "TN3K 为公开集（许可口径见下载清单：HF 卡片 mit / 论文 CC BY-NC 3.0，取保守值仅评测）。"
            "按**官方划分** 2879 trainval / 614 test 评测，无抽样。"
            "无患者 ID、与训练集是否重叠不可证 → 公开集泛化下界。"
        ),
        "dataset": {
            "name": "TN3K", "root": str(root),
            "n_test_pairs": len(test_pairs),
            "normalization": "resize 256x256 + /255（与训练严格一致）",
        },
        "checkpoint": str(wpath),
        "device": str(device),
        "metrics": {
            **summary,
            "dice_ci95": list(bootstrap_ci(dscs)),
            "dice_strict_ci95": list(bootstrap_ci(strict_dscs)),
            "dice_p10_p50_p90": [pct(dscs, 0.1), pct(dscs, 0.5), pct(dscs, 0.9)],
            "iou_p10_p50_p90": [pct(ious, 0.1), pct(ious, 0.5), pct(ious, 0.9)],
            "bootstrap_n": BOOTSTRAP_N,
        },
        "caliber_note": (
            "对外应引用 **dice_mean_strict**（剔除两边都空的情形）。"
            "dice_mean 是乐观口径（空对空记 1.0），仅供与历史数字对照。"
        ),
        "wall_s": round(time.monotonic() - t0, 1),
    }
    rp = Path(args.report)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
