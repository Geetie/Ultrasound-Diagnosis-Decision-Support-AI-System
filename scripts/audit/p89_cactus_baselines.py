"""正式评测 · CACTUS 切面分类的**两条无泄漏基线**（诊断 p87 的 21% 是什么问题）。

为什么要这个脚本
--------------
p87（视频不交集重训）实测切面准确率 **20.99%**，而 test 集的多数类 A4C 占比恰为
947/4511 = **21.0%** —— 即微调后的模型在未见视频上**退化成只猜 A4C**。
「21%」本身是事实，但**不能直接当成"这个任务做不了"**：可能是①任务本身难，
也可能是②`t2b_cactus_train.py` 的配方（3 epoch / lr 3e-6 / batch 256 / 对比损失）
把 BiomedCLIP 的通用特征**训坏了**。两者结论完全不同，必须区分。

本脚本给两条**完全不训练**的基线，用的是同一套视频不交集划分（seed=42）：

  ① `frozen_linear_probe`  —— 冻结 BiomedCLIP visual tower，只在其特征上拟合
     LogisticRegression（train 视频）→ 在 test 视频上评估。
     这是「不改底模，任务本身有多少可分信息」的度量。
  ② `zero_shot_text`       —— 完全 zero-shot：图像特征 vs 5 条切面文本 prompt 的
     余弦相似度直接 argmax，**不拟合任何分类器**。
     这是「一个模型都不训、拿现成语义对齐能到多少」的度量。

三者对照即可定位问题：
  * 若 frozen probe ≫ 21% → 是**配方问题**（微调把特征毁了），应修训练配方；
  * 若 frozen probe ≈ 21% → 是**任务本身在零泄漏协议下就难**，需换更强底模/更多视频。

用法（服务器，GPU；约 5~8 分钟，只抽特征不训练）
----------------------------------------------
    cd ~/uai/src && ~/uai/venv/bin/python scripts/audit/p89_cactus_baselines.py \
        --report reports/p89_cactus_baselines.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path

# 与 p87 共用划分与切面 prompt（不重复实现，避免两处口径漂移）
sys.path.insert(0, str(Path(__file__).resolve().parent))
from p87_cactus_video_disjoint import (  # noqa: E402
    CLASSES,
    bootstrap_acc,
    confusion_matrix,
    per_class_prf,
    video_disjoint_split,
)
from t2b_cactus_prep import VIEW_PROMPTS  # noqa: E402  （scripts/ 在 sys.path 由调用方保证）


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(Path.home() / "uai" / "data" / "t2b_cactus"))
    ap.add_argument("--model-dir", default=str(Path.home() / "uai" / "models" / "biomedclip-base-patch16-224"))
    ap.add_argument("--report", default="reports/p89_cactus_baselines.json")
    ap.add_argument("--batch-size", type=int, default=256)
    ap.add_argument("--num-workers", type=int, default=8)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    import numpy as np
    import open_clip
    import torch
    import torch.nn.functional as F
    from sklearn.linear_model import LogisticRegression
    from torch.utils.data import DataLoader, Dataset

    data_dir = Path(args.data_dir).expanduser()
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
            return self.preprocess(Image.open(r["filepath"]).convert("RGB"))

    device = "cuda"
    uri = "local-dir:" + str(Path(args.model_dir).expanduser())
    model, _, preprocess = open_clip.create_model_and_transforms(uri, device=device)
    model.eval()
    tokenizer = open_clip.get_tokenizer(uri)

    @torch.no_grad()
    def extract(rows):
        loader = DataLoader(_DS(rows, preprocess), batch_size=args.batch_size,
                            shuffle=False, num_workers=args.num_workers, pin_memory=True)
        out = []
        for imgs in loader:
            imgs = imgs.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.bfloat16):
                f = model.encode_image(imgs)
            out.append(F.normalize(f.float(), dim=-1).cpu())
        if not out:
            return np.zeros((0, model.embed_dim), dtype=np.float32)
        return torch.cat(out).numpy()

    t0 = time.time()
    Xtr = extract(splits["train"])
    Xte = extract(splits["test"])
    ytr = [r["view"] for r in splits["train"]]
    yte = [r["view"] for r in splits["test"]]
    print(f"features train={Xtr.shape} test={Xte.shape}（{time.time()-t0:.0f}s）")

    results: dict[str, dict] = {}

    # ---------------- ① 冻结特征 + LR 探针
    lr = LogisticRegression(max_iter=2000)
    lr.fit(Xtr, ytr)
    pred = [str(v) for v in lr.predict(Xte)]
    acc = sum(1 for t, p in zip(yte, pred, strict=True) if t == p) / len(yte)
    results["frozen_linear_probe"] = {
        "n_train": len(ytr), "n_test": len(yte),
        "view_accuracy": round(acc, 4),
        "view_accuracy_ci95": bootstrap_acc(yte, pred, args.bootstrap),
        "per_class": per_class_prf(yte, pred),
        "confusion": confusion_matrix(yte, pred),
        "note": "BiomedCLIP 视觉塔**冻结**，仅在 train 视频特征上拟合 LR；test 视频完全不参与",
    }
    print(f"[frozen probe] acc={acc:.4f}")

    # ---------------- ② zero-shot（图像特征 vs 切面 prompt）
    prompts = [VIEW_PROMPTS[c] for c in CLASSES]
    with torch.no_grad():
        tok = tokenizer(prompts).to(device)
        with torch.autocast("cuda", dtype=torch.bfloat16):
            txt = model.encode_text(tok)
        txt = F.normalize(txt.float(), dim=-1).cpu().numpy()
    sims = Xte @ txt.T  # (n_test, n_classes)
    zs_pred = [CLASSES[int(i)] for i in sims.argmax(axis=1)]
    zs_acc = sum(1 for t, p in zip(yte, zs_pred, strict=True) if t == p) / len(yte)
    results["zero_shot_text"] = {
        "n_test": len(yte),
        "view_accuracy": round(zs_acc, 4),
        "view_accuracy_ci95": bootstrap_acc(yte, zs_pred, args.bootstrap),
        "per_class": per_class_prf(yte, zs_pred),
        "confusion": confusion_matrix(yte, zs_pred),
        "prompts": dict(zip(CLASSES, prompts, strict=True)),
        "note": "不拟合任何分类器；图像-文本余弦 argmax。raw BiomedCLIP 的语义对齐能力",
    }
    print(f"[zero-shot] acc={zs_acc:.4f}")

    # ---------------- ③ 参照：多数类基线（报告 21% 到底意味着什么）
    maj = Counter(ytr).most_common(1)[0][0]
    maj_acc = sum(1 for t in yte if t == maj) / len(yte)
    maj_pred = [maj] * len(yte)
    results["majority_class_reference"] = {
        "majority_class_in_train": maj,
        "view_accuracy": round(maj_acc, 4),
        "per_class_recall_of_majority_only": round(maj_acc, 4),
        "confusion": confusion_matrix(yte, maj_pred),
        "note": "「什么都不学、永远猜 train 里最多的那一类」的分数；任何模型都必须显著超过它才有意义",
    }
    print(f"[majority] {maj} → acc={maj_acc:.4f}")

    report = {
        "probe": "p89_cactus_baselines",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "no_leak_baselines_video_disjoint",
        "scope_note": (
            "与 p87 完全相同的视频不交集划分（seed=42），但**不训练任何视觉参数**："
            "①冻结特征+LR ②zero-shot 文本对齐 ③多数类参照。用于判断 p87 的 20.99% "
            "是「配方把特征训坏了」还是「任务本身在零泄漏协议下就难」。"
        ),
        "split": meta,
        "results": results,
        "wall_s": round(time.time() - t0, 1),
    }
    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== 三条基线（同一视频不交集划分）")
    for k in ("frozen_linear_probe", "zero_shot_text", "majority_class_reference"):
        print(f"  {k:26s} acc={results[k]['view_accuracy']}")
    print(f"宏F1(frozen)={results['frozen_linear_probe']['per_class']['macro_avg']['f1']}")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
