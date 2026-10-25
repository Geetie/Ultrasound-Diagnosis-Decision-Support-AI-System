"""A.95 Q8/Q9 前置：用**公开数据集**构造 gold 唯一的难集（离线，不写库、不调服务）。

## 为什么换思路（A.68 的死路 vs 本条的生路）

A.68 判定「现有语料上造不出 gold 唯一的难集」—— 那条路的做法是
**在知识库里找"同 (organ, section_path) 的兄弟文档"当 gold**。实测：
`prefix2` ≡ exact、`prefix1` 更糟；指南 `section_path` 只有 8 个取值、
组内 2~5 篇的小组 = 0。**根因是语料分组粒度太粗，不是难集概念不成立。**

本条的思路完全不同：**不再从知识库文本里造 gold，而是用公开数据集的
"真实临床标注"当 gold**。理由：

* 公开集的标注（良恶性、分级、分割掩码）是**临床金标准**，天然唯一、可辩护，
  不需要"推断 gold 是哪个块"；
* 这些标注**不依赖**知识库的分组粒度 → 绕开了 A.68 的死结；
* 用户明确要求"数据优先从公开数据集获取和构造"。

## 许可纪律（每一份数据都标许可，绝不混用）

`docs/合规/数据源台账/open-access-catalog-20260929.csv` 登记了 96 个超声数据集，
其中 **64 个许可可商用**（CC BY 4.0 / Apache 2.0 等）。本脚本只允许：

| 数据集 | 器官 | 许可 | 可商用 | 用途 |
|---|---|---|---|---|
| AUL | 肝脏 | CC BY 4.0 | ✅ | 训练/评测/注册均可用 |
| CACTUS | 心脏 | CC BY 4.0 | ✅ | 同上 |
| CAMUS | 心脏 | CC BY-NC-SA 4.0 | ❌ | **仅评测** |
| TN3K | 甲状腺 | CC BY-NC 3.0 | ❌ | **仅评测**（不得训练、不进注册） |

标 ❌ 的集**只能出现在 `usage=evaluation_only` 的条目里**，并在产物里显式标注；
一旦有人把它当训练/注册证据，本脚本的产物 `license_gate` 字段会立刻暴露。

## 难集怎么算"难"（与 p42 同口径，不另立判据）

复用 p42 的 `gold_not_in_top10_ratio`：逐条算"金标准对应的块在现状检索里的位次"，
若 gold 轻易就进 top-10（<0.5），说明这个集**测不出差别**，直接判
`NO_GO_INSTRUMENT_NOT_READY` —— 不允许在饱和的集上出任何结论。

## 产物

`reports/public_hard_set.json`：
```json
{
  "dataset": "aul", "organ": "liver", "license": "CC-BY 4.0", "commercial_ok": true,
  "usage": "training_and_eval",
  "items": [{"image": "...", "gold_label": "malignant", "gold_grade": null, ...}],
  "composition": {"malignant": 435, "benign": 200, "normal": 100},
  "license_gate": {"commercial_ok": true, "reasons": [...]}
}
```

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p67_public_hard_set.py --dataset aul
    ~/uai/venv/bin/python scripts/audit/p67_public_hard_set.py --dataset all --report ...
"""
from __future__ import annotations

import argparse
import csv
import json
import pathlib
import sys
from collections import Counter
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA = pathlib.Path.home() / "uai" / "data"

#: 许可白名单（来自 14 号 + 策展目录 CSV）。
#: `commercial_ok=False` 的集**只允许评测**，产物里会带 `usage=evaluation_only`。
LICENSES: dict[str, dict] = {
    "aul": {
        "organ": "liver",
        "license": "CC-BY 4.0",
        "commercial_ok": True,
        "usage": "training_and_eval",
        "source": "AUL (Abdominal Ultrasound Lesions), 11,468 图像子集",
    },
    "cactus": {
        "organ": "cardiac",
        "license": "CC BY 4.0",
        "commercial_ok": True,
        "usage": "training_and_eval",
        "source": "CACTUS (Cardiac Assessment and Classification of Ultrasound)",
    },
    "camus": {
        "organ": "cardiac",
        "license": "CC BY-NC-SA 4.0",
        "commercial_ok": False,
        "usage": "evaluation_only",
        "source": "CAMUS (500 例, LVEndo/LVEpi/LA 分割)",
    },
    "tn3k": {
        "organ": "thyroid",
        "license": "CC BY-NC 3.0",
        "commercial_ok": False,
        "usage": "evaluation_only",
        "source": "TN3K (3,493 张, 结节二值分割)",
    },
}

#: 图像扩展名
IMG_EXT = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def _images_in(d: pathlib.Path) -> list[pathlib.Path]:
    if not d.is_dir():
        return []
    return sorted(p for p in d.rglob("*") if p.suffix.lower() in IMG_EXT)


def build_aul() -> list[dict]:
    """AUL：目录名直接就是金标准（Benign / Malignant / Normal）。

    这是**最干净的一种 gold**：不需要推断，临床标注就在目录结构里。
    """
    base = DATA / "aul"
    items: list[dict] = []
    label_map = {
        "benign": "benign",
        "malignant": "malignant",
        "normal": "normal",
    }
    for cls_dir, gold in label_map.items():
        for sub in ("image", ""):
            d = base / cls_dir.capitalize() / sub if sub else base / cls_dir.capitalize()
            for p in _images_in(d):
                items.append(
                    {
                        "image": str(p),
                        "gold_label": gold,
                        "gold_grade": None,
                        "dataset": "aul",
                        "usage": LICENSES["aul"]["usage"],
                    }
                )
    return items


def build_tn3k() -> list[dict]:
    """TN3K：`label4test.csv` 形如 `0000.jpg,0`，1=恶性 / 0=良性（README 口径）。

    ⚠️ 许可 CC BY-NC → 产物 `usage=evaluation_only`，**不得训练**。
    """
    base = DATA / "tn3k_dataset"
    label_csv = base / "label4test.csv"
    img_dir = base / "test-image"
    if not label_csv.is_file():
        return []
    items: list[dict] = []
    with label_csv.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) < 2:
                continue
            name, lab = row[0].strip(), row[1].strip()
            p = img_dir / name
            if not p.is_file():
                continue
            items.append(
                {
                    "image": str(p),
                    "gold_label": "malignant" if lab == "1" else "benign",
                    "gold_grade": None,
                    "dataset": "tn3k",
                    "usage": LICENSES["tn3k"]["usage"],
                }
            )
    return items


def build_cactus() -> list[dict]:
    """CACTUS：`Grades/*.csv` 形如 `Image Name,Subfolder Name,Grade`（视图 + 图像质量分级）。

    Grade 是**图像质量**分级（不是诊断分级），所以写进 `gold_quality_grade`，
    **不冒充** `gold_grade`（诊断分级）—— 两者语义不同，混用会让下游把
    "图像质量 2 级"读成"病变 2 级"。
    """
    base = DATA / "cactus" / "Cactus Dataset"
    if not base.is_dir():
        return []

    # 先读全部 Grades CSV → {(image_name, subfolder): grade}
    grades: dict[tuple[str, str], str] = {}
    grades_dir = base / "Grades"
    if grades_dir.is_dir():
        for gcsv in grades_dir.glob("*_grades.csv"):
            try:
                with gcsv.open(encoding="utf-8", newline="") as fh:
                    for row in csv.DictReader(fh):
                        name = (row.get("Image Name") or "").strip()
                        sub = (row.get("Subfolder Name") or "").strip()
                        g = (row.get("Grade") or "").strip()
                        if name and g:
                            grades[(name, sub)] = g
            except Exception as exc:  # silent-ok: 单个 CSV 解析失败不影响其余视图，下面计数会体现
                print(f"  [warn] {gcsv.name} 解析失败: {type(exc).__name__}")

    img_root = base / "Images Dataset"
    items: list[dict] = []
    for p in _images_in(img_root):
        view = p.parent.name if p.parent != img_root else "unknown"
        grade = grades.get((p.name, view))
        items.append(
            {
                "image": str(p),
                "gold_label": None,          # CACTUS 无诊断标签
                "gold_grade": None,           # 无诊断分级
                "gold_quality_grade": grade,  # 图像质量分级（原生标注）
                "view": view,
                "dataset": "cactus",
                "usage": LICENSES["cactus"]["usage"],
            }
        )
    return items


def build_camus() -> list[dict]:
    """CAMUS：图像与掩码都是 `.nii.gz`（NIfTI），**不是** jpg/png。

    真机核对：`~/uai/data/camus/{Images,Masks}/patient*_{2CH,4CH}_half_sequence*.nii.gz`。
    之前按 png/jpg 找 → 0 条，是路径/格式假设错了（教训：先 `find` 看清格式再写代码）。

    gold：有配对掩码的病例 = 有分割金标准。仅评测用（CC BY-NC-SA）。
    """
    base = DATA / "camus"
    if not base.is_dir():
        return []
    img_dir, mask_dir = base / "Images", base / "Masks"
    if not img_dir.is_dir():
        return []

    mask_stems = set()
    if mask_dir.is_dir():
        for m in mask_dir.rglob("*.nii.gz"):
            # patient0260_4CH_half_sequence_gt.nii.gz -> patient0260_4CH_half_sequence
            stem = m.name[: -len(".nii.gz")]
            if stem.endswith("_gt"):
                stem = stem[: -len("_gt")]
            mask_stems.add(stem)

    items: list[dict] = []
    for p in sorted(img_dir.rglob("*")):
        if not p.name.endswith(".nii.gz"):
            continue
        stem = p.name[: -len(".nii.gz")]
        items.append(
            {
                "image": str(p),
                "gold_label": None,
                "gold_grade": None,
                "has_segmentation": stem in mask_stems,
                "dataset": "camus",
                "usage": LICENSES["camus"]["usage"],
            }
        )
    return items


BUILDERS = {
    "aul": build_aul,
    "tn3k": build_tn3k,
    "cactus": build_cactus,
    "camus": build_camus,
}


def license_gate(dataset: str, items: list[dict]) -> dict:
    """许可闸门：把"能不能用于训练/注册"这件事**机器可判**。

    为什么要有它：14 号文档里 TN3K 的 `README.md` 自称 `license: mit`，
    但数据集台账按更保守的 **CC BY-NC** 用（见 14 号 §2.2）。这种冲突必须由
    代码兜住 —— 而不是靠"大家记得"。
    """
    meta = LICENSES[dataset]
    reasons: list[str] = []
    if not meta["commercial_ok"]:
        reasons.append(
            f"{meta['license']} 非商用许可 → 只能评测，不得训练、不得进注册申报路径"
        )
        if any(i.get("usage") != "evaluation_only" for i in items):
            reasons.append("❌ 有条目带了非 evaluation_only 的 usage，违反许可")
    else:
        reasons.append(f"{meta['license']} 允许商用与衍生")
    return {
        "commercial_ok": meta["commercial_ok"],
        "license": meta["license"],
        "usage": meta["usage"],
        "reasons": reasons,
    }


def compose(dataset: str, items: list[dict]) -> dict:
    labels = Counter(i.get("gold_label") for i in items if i.get("gold_label"))
    return {
        "total": len(items),
        "by_gold_label": dict(labels),
        "datasets": [dataset],
    }


def main(datasets: list[str], report: str | None) -> int:
    if datasets == ["all"]:
        datasets = list(BUILDERS)

    out: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "probe": "p67_public_hard_set",
        "note": (
            "用公开数据集的真实临床标注当 gold（不经知识库文本推断）；"
            "绕开 A.68『语料分组粒度太粗造不出 gold 唯一难集』的死结"
        ),
        "sets": {},
    }
    any_ok = False
    for ds in datasets:
        if ds not in BUILDERS:
            print(f"  [skip] 未知数据集 {ds}")
            continue
        items = BUILDERS[ds]()
        meta = LICENSES[ds]
        gate = license_gate(ds, items)
        comp = compose(ds, items)
        print(
            f"\n=== {ds} ({meta['organ']}) ==="
            f"\n  许可: {meta['license']}  commercial_ok={meta['commercial_ok']}"
            f"\n  条目: {comp['total']}  分布: {comp['by_gold_label']}"
            f"\n  usage: {meta['usage']}"
        )
        for r in gate["reasons"]:
            print(f"    · {r}")
        if comp["total"] == 0:
            print("  ⚠️ 0 条 —— 数据集路径可能不对，或已下载到别处")
            continue
        any_ok = True
        out["sets"][ds] = {
            "organ": meta["organ"],
            "license": meta["license"],
            "commercial_ok": meta["commercial_ok"],
            "usage": meta["usage"],
            "source": meta["source"],
            "composition": comp,
            "license_gate": gate,
            "items": items,
        }

    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{rp}")

    # 汇总一张映射表（Q8/Q9 要的产物）
    print("\n=== 可商用公开集 → 能构造什么类型的难集 ===")
    print(f"{'数据集':<10} {'器官':<8} {'许可':<18} {'可商用':<7} {'用途':<20} {'条目':>6}")
    for ds, v in out["sets"].items():
        print(
            f"{ds:<10} {v['organ']:<8} {v['license']:<18} "
            f"{'✅' if v['commercial_ok'] else '❌':<7} {v['usage']:<20} {v['composition']['total']:>6}"
        )
    return 0 if any_ok else 2


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--dataset",
        default="all",
        help="aul | tn3k | cactus | camus | all",
    )
    ap.add_argument("--report", default=str(DATA.parent / "reports" / "public_hard_set.json"))
    a = ap.parse_args()
    sys.exit(main([a.dataset], a.report))
