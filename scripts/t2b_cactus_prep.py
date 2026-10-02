"""T2b：CACTUS 数据准备（切面分类 + 质量分级，open_clip 训练 CSV + 探针标签）。

数据现实（2026-10-02 实测）：FRDR 发布版缺 PSAV 图像（README 虚标 37,736），
实际可用 29,963 张（A4C/SC/PL/PSMV 四类标准切面 + Random 非标准类），
全部带专家 1~10 质量分级（Random=0）。

产出（open_clip 官方管线直接消费，不自写训练循环——方案 C 既有路线）：
  <out>/train.csv, val.csv, test.csv  列: filepath,caption,view,grade
caption = 切面描述 prompt（对比学习域适应用，与 T2a 同模式）。

用法（真机 ~/uai/src）:
    ~/uai/venv/bin/python scripts/t2b_cactus_prep.py \
        --root ~/uai/data/cactus/"Cactus Dataset" \
        --out ~/uai/data/t2b_cactus --seed 42
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import Counter
from pathlib import Path

# 切面 → 描述 prompt（对比学习 caption；与 T2a 域适应同模式）
VIEW_PROMPTS = {
    "A4C": "an ultrasound image showing the apical four chamber view of the heart",
    "SC": "an ultrasound image showing the subcostal four chamber view of the heart",
    "PL": "an ultrasound image showing the parasternal long axis view of the heart",
    "PSMV": "an ultrasound image showing the parasternal short axis view at the mitral valve level",
    "PSAV": "an ultrasound image showing the parasternal short axis view at the aortic valve level",
    "Random": "an ultrasound image without a standard cardiac view",
}

SPLIT_RATIO = (0.7, 0.15, 0.15)


def load_records(root: Path) -> list[dict]:
    """图像 + 分级 CSV 按视图连接；缺 CSV 分级的图像跳过（如实计数）。"""
    grades_dir = root / "Grades"
    images_dir = root / "Images Dataset"
    records: list[dict] = []
    missing_img = 0
    for csv_path in sorted(grades_dir.glob("*_grades.csv")):
        view = csv_path.stem.removesuffix("_grades")
        with open(csv_path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                img_name = row["Image Name"]
                sub = row.get("Subfolder Name", view)
                img_path = images_dir / sub / img_name
                if not img_path.exists():
                    missing_img += 1
                    continue
                records.append({
                    "filepath": str(img_path),
                    "view": sub,
                    "grade": int(row["Grade"]),
                })
    return records


def stratified_split(records: list[dict], seed: int) -> dict[str, list[dict]]:
    """按 (view, grade) 分层切分——保证 test 集各级别/类目都有代表。"""
    rng = random.Random(seed)
    by_key: dict[tuple, list[dict]] = {}
    for r in records:
        by_key.setdefault((r["view"], r["grade"]), []).append(r)
    splits: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    for key, items in sorted(by_key.items()):
        items = sorted(items, key=lambda x: x["filepath"])
        rng.shuffle(items)
        n = len(items)
        n_train = round(n * SPLIT_RATIO[0])
        n_val = round(n * SPLIT_RATIO[1])
        splits["train"] += items[:n_train]
        splits["val"] += items[n_train:n_train + n_val]
        splits["test"] += items[n_train + n_val:]
    return splits


def main() -> int:
    parser = argparse.ArgumentParser(description="T2b CACTUS 数据准备")
    parser.add_argument("--root", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=True)

    records = load_records(root)
    view_counts = Counter(r["view"] for r in records)
    print("[t2b-prep] 记录（图像+分级连接成功）:", len(records))
    print("[t2b-prep] 视图分布:", dict(view_counts))

    # caption 注入（切面域适应 prompt）
    for r in records:
        r["caption"] = VIEW_PROMPTS.get(r["view"], VIEW_PROMPTS["Random"])

    splits = stratified_split(records, args.seed)
    for name, items in splits.items():
        path = out / f"{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["filepath", "caption", "view", "grade"])
            w.writeheader()
            w.writerows(items)
        print(f"[t2b-prep] {name}: {len(items)} → {path}")

    summary = {
        "total": len(records),
        "views": dict(view_counts),
        "splits": {k: len(v) for k, v in splits.items()},
        "seed": args.seed,
        "note": "FRDR 发布版缺 PSAV 图像（README 虚标），实收 5 视图 29,963 张",
    }
    (out / "manifest.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
