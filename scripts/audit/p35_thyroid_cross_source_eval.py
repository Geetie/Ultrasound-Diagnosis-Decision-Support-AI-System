"""A.63 批 WP3：甲状腺判别「跨来源公开集」评测（生产 8003 端点，**只推理不训练**）。

为什么要这一件
--------------
t2a 已有的两个口径：①开发集（内部划分，AUC 0.8751）②公开集 held-out（AUL，A.60）。
第三口径「跨来源」此前只有一次零散数字（`tests/reports/t2a_tn3k_generalization.json`：
AUC 0.6789 / n=1228）—— 本脚本把它**正式化**：

1. **先冻结再跑**：逐张图片 sha256 进 `tests/reports/*_manifest.json`（复跑前逐条校验，
   换集/改集立刻失败 —— 防"换集刷分"）；
2. **走生产端点**：`POST /v1/classify`（8003 当前加载的 t2a 资产），与 p29 同请求口径，
   不重训、不调参；
3. 指标：恶性-vs-良性 **AUC + bootstrap 95%CI**、阈值 0.5 下敏感性/特异性、二分类准确率。

数据来源与许可（2026-10-15 核实，用户已放宽"非可商用公开集可用"）
------------------------------------------------------------------
* **计划的 TNSCUI：取不到**。Zenodo `10.5281/zenodo.3715942` 只有论文 PDF（2.7 MB），
  Zenodo API 搜索 `TNSCUI` 也无数据集记录 → 本脚本对 `--dataset tnscui` **显式失败**并说明，
  不静默跳过（"没数据"必须看得见）。
* **实际采用 TN3K**（服务器已有：`test-image/` 614 张 + `label4test.csv` 二分类标签）。
  许可按 14 号台账 **CC BY-NC 3.0（仅非商业研究）**；⚠️ 数据集自带 `README.md` 写 `license: mit`
  与 14 号冲突 —— 报告里两者并列，**按更保守口径使用**（NC，仅研究/内部验证）。
* 用途固定 `cross_source_eval_only`：**仅评测、不训练**；**不进注册申报材料**。

用法（服务器，需 8003 就绪）：
    ~/uai/venv/bin/python scripts/audit/p35_thyroid_cross_source_eval.py \
        --dataset tn3k --data-dir ~/uai/data/tn3k_dataset \
        --clip-url http://127.0.0.1:8003 --report ~/uai/reports/t2a_tn3k_cross_source.json

退出码：0 跑完（含低于建议门限）；1 低于建议门限（21 号 §阶段三-A：AUC≥0.80 且 CI 下界≥0.70，
**待 00 号拍板**）；2 数据缺失/服务不可达（可读原因）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 复用 p29 的请求与指标口径（同一把尺子，避免两套 AUC/CI 实现）
from p29_t2a_aul_holdout import (  # noqa: E402
    auc_mal_vs_rest,
    bootstrap_ci,
    classify_batch,
)

EXTS = {".jpg", ".jpeg", ".png", ".bmp"}


def _sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_tn3k_labels(data_dir: pathlib.Path, csv_name: str) -> list[dict]:
    """读取 TN3K 官方 test 划分标签（`<file>,<0|1>`；无表头，0=良性 1=恶性）。"""
    csv_path = data_dir / csv_name
    if not csv_path.is_file():
        raise SystemExit(f"标签文件不存在：{csv_path}")
    rows: list[dict] = []
    missing: list[str] = []
    for line in csv_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or "," not in line:
            continue
        name, label = line.rsplit(",", 1)
        name = name.strip().strip('"')
        if name.lower().endswith(tuple(EXTS)):
            img = data_dir / "test-image" / name
            if not img.is_file():
                img = data_dir / name
            if not img.is_file():
                # 兜底：按文件名在 test-image 里找
                cand = list((data_dir / "test-image").glob(pathlib.Path(name).stem + ".*"))
                img = cand[0] if cand else img
        else:
            img = data_dir / "test-image" / name
            if not img.is_file():
                cand = list((data_dir / "test-image").glob(f"{name}.*"))
                img = cand[0] if cand else img
        if not img.is_file():
            missing.append(name)
            continue
        rows.append(
            {
                "path": str(img.relative_to(data_dir)).replace("\\", "/"),
                "label": "Malignant" if label.strip() == "1" else "Benign",
                "sha256": _sha256(img),
                "bytes": img.stat().st_size,
            }
        )
    if not rows:
        raise SystemExit(f"没有解析出任何标签行：{csv_path}（检查格式 <file>,<0|1>）")
    if missing:
        print(f"⚠️ 有 {len(missing)} 条标签找不到对应图片（前 3 个：{missing[:3]}）")
    return rows


def freeze_or_verify(rows: list[dict], manifest: pathlib.Path, meta: dict) -> None:
    """冻结清单；已存在则逐条校验 sha256（任何不一致都显式失败）。"""
    if manifest.is_file():
        old = json.loads(manifest.read_text(encoding="utf-8"))
        old_rows = {r["path"]: r for r in old.get("items", [])}
        if len(old_rows) != len(rows):
            raise SystemExit(
                f"清单条数不符（冻结 {len(old_rows)} vs 实测 {len(rows)}）—— 数据集被改动过，"
                f"禁止换集刷分；确认无误后删除 {manifest} 重建"
            )
        for r in rows:
            o = old_rows.get(r["path"])
            if not o or o["sha256"] != r["sha256"]:
                raise SystemExit(f"清单 sha256 不符：{r['path']}（数据集被改动过）")
        print(f"清单校验通过：{len(rows)} 张（sha256 逐条一致）→ {manifest}")
        return
    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {**meta, "count": len(rows), "items": rows}
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    digest = hashlib.sha256(
        "\n".join(f"{r['path']}:{r['sha256']}" for r in rows).encode()
    ).hexdigest()[:16]
    print(f"清单已冻结：{len(rows)} 张 → {manifest}（清单指纹 {digest}）")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=("tn3k", "tnscui"), default="tn3k")
    ap.add_argument("--data-dir", default=str(pathlib.Path.home() / "uai/data/tn3k_dataset"))
    ap.add_argument("--labels-csv", default="label4test.csv")
    ap.add_argument("--clip-url", default="http://127.0.0.1:8003")
    ap.add_argument(
        "--manifest", default="tests/reports/t2a_tn3k_cross_source_manifest.json"
    )
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/t2a_tn3k_cross_source.json"))
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument(
        "--gate",
        type=float,
        default=0.70,
        help="AUC 建议门限（21 号 §阶段三-A：AUC≥0.80 且 CI 下界≥0.70，**待 00 号拍板**）",
    )
    ap.add_argument("--limit", type=int, default=0, help=">0 只跑前 N 张（冒烟；报告标注 partial）")
    args = ap.parse_args()

    if args.dataset == "tnscui":
        print(
            "[FATAL] TNSCUI 数据集不可得（2026-10-15 核实）：\n"
            "  * Zenodo 10.5281/zenodo.3715942 仅含论文 PDF（2.7 MB），无数据文件；\n"
            "  * Zenodo API 搜索 'TNSCUI' 无数据集记录。\n"
            "  → 需要该集时请先确认其**真实发布位置**；本脚本不静默跳过。\n"
            "  当前可用的跨来源公开集：TN3K（--dataset tn3k，服务器已有）。"
        )
        return 2

    data_dir = pathlib.Path(args.data_dir).expanduser()
    if not data_dir.is_dir():
        print(f"[FATAL] 数据目录不存在：{data_dir}")
        return 2

    rows = load_tn3k_labels(data_dir, args.labels_csv)
    dist: dict[str, int] = {}
    for r in rows:
        dist[r["label"]] = dist.get(r["label"], 0) + 1
    print(f"TN3K 跨来源评测：{len(rows)} 张（{dist}）")

    freeze_or_verify(
        rows,
        pathlib.Path(args.manifest).expanduser(),
        {
            "_note": (
                "A.63 WP3 甲状腺跨来源公开集冻结清单：相对路径 + 标签 + sha256；"
                "复跑前逐条校验，防换集刷分"
            ),
            "dataset": "TN3K (test split, 官方划分)",
            "license": "CC BY-NC 3.0（14 号台账）；数据集 README 写 license: mit —— 按更保守口径使用",
            "split_level": "image",
            "split_level_note": "TN3K 无 patient_id → 只能到影像级；不得称为患者级独立验证",
            "usage": "cross_source_eval_only（仅评测，不训练；不进注册申报）",
            "training_overlap": (
                "生产资产 t2a_v2（models/t2a_v2/t2a_finetune.pt + t2a_probe_lr.joblib）"
                "是否曾见过 TN3K 无代码留证（t2a_tn3k_adapt.pt 是另一份适配产物）→ 记为跨来源，"
                "不得称为独立验证"
            ),
        },
    )

    if args.limit:
        rows = rows[: args.limit]
        print(f"⚠️ 冒烟模式：只跑前 {len(rows)} 张（报告 scope 标注 partial）")

    t0 = time.monotonic()
    labels: list[str] = []
    scores: list[float] = []
    preds: list[str] = []
    failed: list[str] = []
    for start in range(0, len(rows), args.batch):
        chunk = rows[start : start + args.batch]
        raws = [(data_dir / r["path"]).read_bytes() for r in chunk]
        try:
            results = classify_batch(args.clip_url, raws)
        except Exception as exc:  # 显式记录失败样本（丢样本会抬高指标）
            failed.extend(r["path"] for r in chunk)
            print(f"  批次失败 [{start}:{start + len(chunk)}] {type(exc).__name__}: {exc}")
            continue
        for r, res in zip(chunk, results, strict=True):
            probs = (res or {}).get("probs") or {}
            labels.append(r["label"])
            scores.append(float(probs.get("Malignant", 0.0)))
            preds.append(max(probs, key=probs.get) if probs else "Unknown")
        print(f"  {min(start + len(chunk), len(rows))}/{len(rows)}")

    if not labels:
        print("[FATAL] 没有任何样本成功分类 —— 检查 8003 服务与 /v1/classify 端点")
        return 2

    auc = auc_mal_vs_rest(scores, labels)
    lo, hi = bootstrap_ci(scores, labels, args.bootstrap)
    pos = sum(1 for y in labels if y == "Malignant")
    tp = sum(1 for s, y in zip(scores, labels, strict=True) if y == "Malignant" and s >= 0.5)
    fn = pos - tp
    fp = sum(1 for s, y in zip(scores, labels, strict=True) if y != "Malignant" and s >= 0.5)
    tn = len(labels) - pos - fp
    sens = tp / (tp + fn) if (tp + fn) else float("nan")
    spec = tn / (tn + fp) if (tn + fp) else float("nan")
    acc = sum(1 for p, y in zip(preds, labels, strict=True) if p == y) / len(labels)

    report = {
        "probe": "p35_thyroid_cross_source_eval",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "cross_source_public_eval" + ("_partial" if args.limit else ""),
        "scope_note": (
            "TN3K 与开发集（AUL）来源不同，且**未用于该资产训练**这一点无代码留证 → "
            "口径为「跨来源公开集评测」，**不得**称为注册级独立验证；"
            "许可按更保守口径（14 号：CC BY-NC，仅非商业研究）；仅评测、不训练、不进注册申报"
        ),
        "license": {
            "ledger_14": "CC BY-NC 3.0（仅非商业研究）",
            "dataset_readme": "license: mit（与该台账冲突）",
            "policy": "按更保守口径使用（NC）",
        },
        "dataset": {
            "name": "TN3K",
            "dir": str(data_dir),
            "labels_csv": args.labels_csv,
            "n": len(labels),
            "class_dist": {"Malignant": pos, "Benign": len(labels) - pos},
            "failed": failed,
        },
        "classifier": {
            "endpoint": f"{args.clip_url}/v1/classify",
            "mechanism": "production t2a asset (finetuned visual tower + logistic probe)",
        },
        "metrics": {
            "mal_vs_benign_auc": round(auc, 4) if auc == auc else None,
            "auc_ci95": [round(lo, 4) if lo == lo else None, round(hi, 4) if hi == hi else None],
            "bootstrap_n": args.bootstrap,
            "sensitivity@0.5": round(sens, 4) if sens == sens else None,
            "specificity@0.5": round(spec, 4) if spec == spec else None,
            "accuracy_binary": round(acc, 4),
            "confusion": {"tp": tp, "fn": fn, "fp": fp, "tn": tn},
        },
        "gate": {"auc_min": args.gate, "pending_approval": True, "owner": "00 号（21 号 §阶段三-A）"},
        "wall_s": round(time.monotonic() - t0, 1),
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"\nAUC(恶性 vs 良性) = {report['metrics']['mal_vs_benign_auc']}  "
        f"95%CI {report['metrics']['auc_ci95']}（bootstrap {args.bootstrap}）"
    )
    print(
        f"敏感性@0.5 = {report['metrics']['sensitivity@0.5']}  "
        f"特异性@0.5 = {report['metrics']['specificity@0.5']}  "
        f"准确率 = {report['metrics']['accuracy_binary']}"
    )
    print(f"口径：{report['scope']}（跨来源公开集；非注册级独立验证）")
    print(f"报告：{out}  耗时 {report['wall_s']}s  失败样本 {len(failed)}")

    ok = auc == auc and auc >= args.gate
    print(
        f"== p35: {'PASS' if ok else 'FAIL'}"
        f"（建议门限 AUC≥{args.gate}，**待 00 号拍板**）"
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
