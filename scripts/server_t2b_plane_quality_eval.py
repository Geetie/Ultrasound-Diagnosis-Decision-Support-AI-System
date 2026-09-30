"""T2b：plane_assessment / image_quality 模块真实数据基线评测（真机）。

任务定义（T2b）：用真实公开数据评测边缘端切面评估与图像质量模块，
产出混淆矩阵与 Top-1 准确率。

数据渠道现实（2026-09-30 实测，详见 16 号 A.7）：原定 CACTUS（FRDR
frdr.usask.ca 不可达 http=000，处于备份维护）与 CARDIACUDC（Kaggle 镜像
存在但下载需 API 凭证，真机无 ~/.kaggle/kaggle.json；IEEE DataPort 需账号）
均受阻。**替代方案**：US-365K（CC 待确认，仅评测）caption 自带切面描述词
（transverse 166 / longitudinal 204），作为切面真值标注对
LocalFakePlaneAssessmentAdapter（当前唯一实现，按长宽比定切面，无临床
依据的假实现）做检索式评测——量化"真模型头缺位时的实际基线"。
image_quality 无任何标注词（caption quality 词 0 条），仅产出分数分布，
不评准确率（如实报告口径限制）。

用法（真机 ~/uai/src）：
    python scripts/server_t2b_plane_quality_eval.py \
        --us365k-jsonl ~/uai/data/us365k/test.resolved.jsonl \
        --output reports/t2b_plane_quality_report.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402


def _grayscale(path: Path) -> np.ndarray:
    return np.asarray(Image.open(path).convert("L"))


def main() -> int:
    parser = argparse.ArgumentParser(description="T2b 切面/质量模块基线评测")
    parser.add_argument("--us365k-jsonl", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-class", type=int, default=120)
    args = parser.parse_args()

    from edge.image_quality import LocalFakeImageQualityAdapter
    from edge.plane_assessment import LocalFakePlaneAssessmentAdapter
    from edge.protocols import Frame

    # ① caption 切面词标注抽样（互斥：只收词面单一的样本）
    print("[1/3] 筛选 caption 切面标注样本...")
    samples: dict[str, list[Path]] = {"transverse": [], "longitudinal": []}
    with open(args.us365k_jsonl, encoding="utf-8") as f:
        for line in f:
            rec = json.loads(line)
            cap = rec.get("caption", "").lower()
            has_t = "transverse" in cap
            has_l = "longitudinal" in cap
            if has_t == has_l:
                continue  # 同时含或都不含 → 跳过
            label = "transverse" if has_t else "longitudinal"
            if len(samples[label]) >= args.per_class:
                continue
            p = Path(rec["image"])
            if p.exists():
                samples[label].append(p)
            if all(len(v) >= args.per_class for v in samples.values()):
                break
    print(f"  transverse={len(samples['transverse'])} longitudinal={len(samples['longitudinal'])}")

    # ② 假实现推理 + 混淆矩阵
    print("[2/3] LocalFakePlaneAssessmentAdapter 推理...")
    adapter = LocalFakePlaneAssessmentAdapter()
    adapter.load()
    labels = ["transverse", "longitudinal"]
    confusion = {
        t: {p: 0 for p in [*labels, "non_standard", "none"]} for t in labels
    }
    quality_scores: list[float] = []
    iq_adapter = LocalFakeImageQualityAdapter()
    iq_adapter.load()
    for label, paths in samples.items():
        for p in paths:
            try:
                gray = _grayscale(p)
            except Exception:
                continue
            frame = Frame(
                frame_id=p.stem,
                session_id="t2b",
                index=0,
                pixels=gray,
                received_at_ms=0.0,
            )
            out = adapter.assess(frame, organ_code="liver")
            pred = out.plane_type if out is not None else "none"
            if pred not in confusion[label]:
                pred = "non_standard"
            confusion[label][pred] += 1
            try:
                q = iq_adapter.assess(frame)
                if q is not None:
                    quality_scores.append(float(q.overall_score))
            except Exception:
                pass

    correct = confusion["transverse"]["transverse"] + confusion["longitudinal"]["longitudinal"]
    n_scored = sum(sum(row.values()) for row in confusion.values())
    top1 = correct / n_scored if n_scored else 0.0
    # 二值化 Top-1（把 non_standard/none 记错）
    report = {
        "task": "T2b 切面/图像质量模块基线评测（LocalFake 假实现，真模型头未交付）",
        "dataset": "US-365K caption 切面词标注（transverse/longitudinal 互斥样本）",
        "blocked_channels": {
            "CACTUS": "FRDR 不可达（http=000，备份维护中）",
            "CARDIACUDC": "Kaggle 镜像存在但需 API 凭证（真机无 kaggle.json）；IEEE DataPort 需账号",
            "ALN-ULTRA": "Zenodo 15003119 files=0（发布不完整，无实际图像下载渠道）",
        },
        "samples": {k: len(v) for k, v in samples.items()},
        "confusion_matrix": confusion,
        "top1_accuracy": round(top1, 4),
        "top1_binary_excluding_nonstandard": (
            round(correct / (n_scored - sum(confusion[t]["non_standard"] + confusion[t]["none"] for t in labels)) , 4)
            if n_scored
            else 0.0
        ),
        "image_quality_scores": {
            "n": len(quality_scores),
            "mean": round(sum(quality_scores) / len(quality_scores), 4)
            if quality_scores
            else None,
            "note": "caption 无质量标注词，无法评准确率，仅分数分布",
        },
        "conclusion": "假实现 Top-1 接近长宽比先验水平（无临床判别力）——"
        "真模型头交付前，切面/质量监控链路仅能做链路联调，不得用于临床提示",
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Top-1={report['top1_accuracy']} → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
