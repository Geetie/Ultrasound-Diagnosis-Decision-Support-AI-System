"""正式评测 · AUL 公开集（肝超声 良/恶性/正常）—— p29 的固化成版。

为什么另立 p85 而不是改 p29
--------------------------
p29 (`p29_t2a_aul_holdout.py`) 产出的 AUC 0.895 / 95%CI [0.8712, 0.9168] 是历史数字，
必须**可复现**、且新旧数字能对齐。因此本脚本：
  * **沿用 p29 的样本选取与判定口径**（不含 `segmentation/` 子目录、只收图像扩展名、
    只收 `Benign/Malignant/Normal` 三子目录 — p29 `build_or_verify_manifest` 原始逻辑），
    保证 n=735 与历史一致；
  * **沿用同一冻结清单** `tests/reports/t2a_aul_manifest.json`（sha256 逐条校验，防换集刷分）；
  * 在 p29 的基础上补齐输出：**每类 P/R/F1 + 宏/加权平均 + 混淆矩阵 + 样本量 +
    bootstrap 置信区间（AUC / 准确率 / 各类 F1）**，并把失败样本数写进报告。

口径（`scope`）
--------------
AUL 是**公开集**：无患者 ID、无法按患者分层、与训练用的 US-365K / TN3K 是否重叠**不可证**。
因此本脚本的数字只能作为「公开集泛化下界」，**不是**注册申报级的独立验证。

端点
----
`POST http://127.0.0.1:8003/v1/classify`（multipart 字段名固定 `images`）：
微调后的 visual tower + 逻辑回归探针，返回三分类概率。

用法（服务器，需 8003 就绪）
--------------------------
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p85_aul_holdout_formal.py \
        --aul-dir ~/uai/data/aul \
        --manifest tests/reports/t2a_aul_manifest.json \
        --report reports/p85_aul_holdout_formal.json
"""
from __future__ import annotations

import argparse
import io
import json
import random
import sys
import time
import urllib.request
import uuid
from pathlib import Path

CLASSES = ("Benign", "Malignant", "Normal")
EXTS = {".jpg", ".jpeg", ".png", ".bmp"}
# 与 p29 完全一致的清单指纹口径：只「标签目录/图像文件」，不递归进 segmentation/
# （p29 用 image_dir/* 判定扩展名，segmentation/ 是目录故天然被排除）
IMAGE_SUBDIR = "image"


# --------------------------------------------------------------------- 指标
def auc_mal_vs_rest(scores: list[float], labels: list[str]) -> float:
    """恶性 vs 其余 的 AUC（Mann-Whitney U / 秩和口径，与 p29 相同）。"""
    pairs = sorted(zip(scores, labels, strict=True), key=lambda x: x[0])
    pos = [i for i, (_, y) in enumerate(pairs) if y == "Malignant"]
    neg = [i for i, (_, y) in enumerate(pairs) if y != "Malignant"]
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
    r_pos = sum(ranks[i] for i in pos)
    n1, n0 = len(pos), len(neg)
    return (r_pos - n1 * (n1 + 1) / 2) / (n1 * n0)


def _pct(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, max(0, int(q * (len(sorted_vals) - 1))))]


def per_class_prf(y_true: list[str], y_pred: list[str], classes: tuple[str, ...]) -> dict:
    """每类 P/R/F1 + 支持数；同时给 macro / weighted 平均。"""
    out: dict[str, dict[str, float | int]] = {}
    f1s: list[float] = []
    weights: list[int] = []
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p != c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        sup = tp + fn
        out[c] = {
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1": round(f1, 4),
            "support": sup,
        }
        f1s.append(f1)
        weights.append(sup)
    n = max(1, len(y_true))
    out["macro_avg"] = {
        "precision": round(sum(out[c]["precision"] for c in classes) / len(classes), 4),
        "recall": round(sum(out[c]["recall"] for c in classes) / len(classes), 4),
        "f1": round(sum(f1s) / len(f1s), 4),
        "support": n,
    }
    out["weighted_avg"] = {
        "precision": round(sum(out[c]["precision"] * w for c, w in zip(classes, weights, strict=True)) / n, 4),
        "recall": round(sum(out[c]["recall"] * w for c, w in zip(classes, weights, strict=True)) / n, 4),
        "f1": round(sum(f * w for f, w in zip(f1s, weights, strict=True)) / n, 4),
        "support": n,
    }
    return out


def confusion_matrix(y_true: list[str], y_pred: list[str], classes: tuple[str, ...]) -> dict:
    labels = (*classes, "Unknown")
    mat = {t: dict.fromkeys(labels, 0) for t in classes}
    for t, p in zip(y_true, y_pred, strict=True):
        mat.setdefault(t, dict.fromkeys(labels, 0))
        mat[t][p] = mat[t].get(p, 0) + 1
    return mat


def bootstrap_all(
    scores: list[float],
    labels: list[str],
    preds: list[str],
    n_boot: int,
    seed: int = 20261013,
) -> dict:
    """一次重采样同时给 AUC / 三分类准确率 / 宏 F1 的 95%CI（同一批索引，口径一致）。"""
    rnd = random.Random(seed)
    idx = list(range(len(scores)))
    aucs: list[float] = []
    accs: list[float] = []
    f1s: list[float] = []
    for _ in range(n_boot):
        pick = [rnd.choice(idx) for _ in idx]
        s = [scores[i] for i in pick]
        y = [labels[i] for i in pick]
        p = [preds[i] for i in pick]
        a = auc_mal_vs_rest(s, y)
        if a == a:
            aucs.append(a)
        accs.append(sum(1 for t, q in zip(y, p, strict=True) if t == q) / len(y))
        prf = per_class_prf(y, p, CLASSES)
        f1s.append(float(prf["macro_avg"]["f1"]))
    aucs.sort()
    accs.sort()
    f1s.sort()
    return {
        "auc": [_pct(aucs, 0.025), _pct(aucs, 0.975)],
        "accuracy": [_pct(accs, 0.025), _pct(accs, 0.975)],
        "macro_f1": [_pct(f1s, 0.025), _pct(f1s, 0.975)],
        "n_boot": n_boot,
    }


# --------------------------------------------------------------------- 数据
def list_items(aul_dir: Path) -> list[dict]:
    """与 p29 相同的选取口径：三标签目录下的**图像文件**（不递归 segmentation/）。"""
    rows: list[dict] = []
    for label in CLASSES:
        image_dir = aul_dir / label / IMAGE_SUBDIR
        if not image_dir.is_dir():
            raise SystemExit(f"缺少目录：{image_dir}（AUL 期望 Benign/Malignant/Normal × image/）")
        for p in sorted(image_dir.iterdir()):
            if p.is_file() and p.suffix.lower() in EXTS:
                rows.append(
                    {
                        "path": str(p.relative_to(aul_dir)).replace("\\", "/"),
                        "label": label,
                        "sha256": _sha256(p),
                        "bytes": p.stat().st_size,
                    }
                )
    if not rows:
        raise SystemExit(f"AUL 目录下没找到图片：{aul_dir}")
    return rows


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_or_write_manifest(rows: list[dict], manifest: Path) -> str:
    """有清单则逐条校验 sha256；无则新建。返回清单状态说明（写进报告）。"""
    if manifest.is_file():
        old = json.loads(manifest.read_text(encoding="utf-8"))
        old_rows = {r["path"]: r for r in old.get("items", [])}
        if len(old_rows) != len(rows):
            raise SystemExit(
                f"冻结清单条数不符（清单 {len(old_rows)} vs 实测 {len(rows)}）—— "
                f"数据集被改动过；确认无误后删除 {manifest} 重建"
            )
        for r in rows:
            o = old_rows.get(r["path"])
            if not o or o["sha256"] != r["sha256"]:
                raise SystemExit(f"冻结清单 sha256 不符：{r['path']}（数据集被改动过）")
        return f"清单校验通过：{len(rows)} 张，sha256 逐条一致（{manifest}）"

    manifest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_note": "AUL 公开集冻结清单：相对路径 + 标签 + sha256；复跑前逐条校验，防换集刷分",
        "dataset": "AUL (CC BY 4.0, Zenodo 7272660)",
        "scope": "public_dataset_holdout_lower_bound",
        "count": len(rows),
        "items": rows,
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"清单新建：{len(rows)} 张 → {manifest}"


# --------------------------------------------------------------------- 端点
def classify_batch(clip_url: str, raws: list[bytes], timeout: float = 180) -> list[dict]:
    boundary = uuid.uuid4().hex
    body = io.BytesIO()
    for i, raw in enumerate(raws):
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            (
                'Content-Disposition: form-data; name="images"; '
                f'filename="img{i}.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'
            ).encode()
        )
        body.write(raw)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(f"{clip_url}/v1/classify", data=body.getvalue(), method="POST")
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results") or []


# --------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--aul-dir", default=str(Path.home() / "uai" / "data" / "aul"))
    ap.add_argument("--clip-url", default="http://127.0.0.1:8003")
    ap.add_argument("--manifest", default="tests/reports/t2a_aul_manifest.json")
    ap.add_argument("--report", default="reports/p85_aul_holdout_formal.json")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--bootstrap", type=int, default=2000)
    ap.add_argument("--limit", type=int, default=0, help=">0 时只跑前 N 张（冒烟；报告标 partial）")
    ap.add_argument(
        "--baseline-json",
        default="",
        help="历史 p29 报告的 JSON 路径（同机可直接对比；不给则不写 comparison 段）",
    )
    args = ap.parse_args()

    aul = Path(args.aul_dir).expanduser()
    if not aul.is_dir():
        print(f"AUL 目录不存在：{aul}")
        return 2
    rows = list_items(aul)
    manifest_status = verify_or_write_manifest(rows, Path(args.manifest).expanduser())
    print(manifest_status)
    if args.limit:
        rows = rows[: args.limit]
        print(f"⚠️ 冒烟模式：只跑前 {len(rows)} 张")

    t0 = time.monotonic()
    labels: list[str] = []
    preds: list[str] = []
    mal_scores: list[float] = []
    failed: list[str] = []
    for start in range(0, len(rows), args.batch):
        chunk = rows[start : start + args.batch]
        raws = [(aul / r["path"]).read_bytes() for r in chunk]
        try:
            results = classify_batch(args.clip_url, raws)
        except Exception as exc:  # 显式记录失败样本，绝不静默丢弃（丢样本会抬高指标）
            failed.extend(r["path"] for r in chunk)
            print(f"  批次失败 [{start}:{start + len(chunk)}] {type(exc).__name__}: {exc}")
            continue
        if len(results) != len(chunk):
            failed.extend(r["path"] for r in chunk[len(results):])
        for r, res in zip(chunk, results, strict=False):
            probs = (res or {}).get("probs") or {}
            labels.append(r["label"])
            mal_scores.append(float(probs.get("Malignant", 0.0)))
            preds.append(max(probs, key=probs.get) if probs else "Unknown")
        print(f"  {min(start + len(chunk), len(rows))}/{len(rows)}", flush=True)

    if not labels:
        print("没有任何样本成功分类 —— 检查 8003 服务与 /v1/classify 端点")
        return 2

    auc = auc_mal_vs_rest(mal_scores, labels)
    ci = bootstrap_all(mal_scores, labels, preds, args.bootstrap)
    prf = per_class_prf(labels, preds, CLASSES)
    cm = confusion_matrix(labels, preds, CLASSES)
    acc = sum(1 for t, p in zip(labels, preds, strict=True) if t == p) / len(labels)

    tp = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y == "Malignant" and s >= 0.5)
    fn = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y == "Malignant" and s < 0.5)
    fp = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y != "Malignant" and s >= 0.5)
    tn = sum(1 for s, y in zip(mal_scores, labels, strict=True) if y != "Malignant" and s < 0.5)

    support = {c: sum(1 for y in labels if y == c) for c in CLASSES}
    report = {
        "probe": "p85_aul_holdout_formal",
        "supersedes": "p29_t2a_aul_holdout（同口径固化版；补每类 P/R/F1 与样本量）",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "public_dataset_holdout_lower_bound" + ("_partial" if args.limit else ""),
        "scope_note": (
            "AUL 为公开集（CC BY 4.0，无患者 ID、与训练数据是否重叠不可证）→ 本数字只作"
            "「公开集泛化下界」，**不得**当作注册申报的独立验证"
        ),
        "dataset": {
            "name": "AUL",
            "license": "CC BY 4.0 (Zenodo 7272660)",
            "organ": "肝脏（不是甲状腺）",
            "dir": str(aul),
            "n_evaluated": len(labels),
            "n_listed": len(rows),
            "n_failed": len(failed),
            "failed": failed,
            "class_support": support,
            "manifest": manifest_status,
        },
        "classifier": {
            "endpoint": f"{args.clip_url}/v1/classify",
            "mechanism": "finetuned_visual_tower + logistic_probe（T2a v2）",
            "threshold": 0.5,
        },
        "metrics": {
            "mal_vs_rest_auc": round(auc, 4) if auc == auc else None,
            "auc_ci95": [round(v, 4) if v == v else None for v in ci["auc"]],
            "accuracy_3class": round(acc, 4),
            "accuracy_ci95": [round(v, 4) for v in ci["accuracy"]],
            "macro_f1": prf["macro_avg"]["f1"],
            "macro_f1_ci95": [round(v, 4) for v in ci["macro_f1"]],
            "bootstrap_n": ci["n_boot"],
            "bootstrap_seed": 20261013,
            "per_class": prf,
            "confusion": cm,
            "at_threshold_0.5": {
                "sensitivity": round(tp / (tp + fn), 4) if (tp + fn) else None,
                "specificity": round(tn / (tn + fp), 4) if (tn + fp) else None,
                "ppv": round(tp / (tp + fp), 4) if (tp + fp) else None,
                "npv": round(tn / (tn + fn), 4) if (tn + fn) else None,
                "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            },
        },
        "wall_s": round(time.monotonic() - t0, 1),
    }

    if args.baseline_json:
        bp = Path(args.baseline_json).expanduser()
        if bp.is_file():
            old = json.loads(bp.read_text(encoding="utf-8"))
            om = old.get("metrics", {})
            report["comparison"] = {
                "baseline_path": str(bp),
                "baseline_generated_at": old.get("generated_at"),
                "baseline_n": old.get("dataset", {}).get("n"),
                "baseline_auc": om.get("mal_vs_rest_auc"),
                "baseline_auc_ci95": om.get("auc_ci95"),
                "delta_auc": (
                    round(report["metrics"]["mal_vs_rest_auc"] - om["mal_vs_rest_auc"], 4)
                    if om.get("mal_vs_rest_auc") is not None
                    and report["metrics"]["mal_vs_rest_auc"] is not None
                    else None
                ),
                "baseline_accuracy_3class": om.get("accuracy_3class"),
            }

    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    m = report["metrics"]
    print(f"\n样本量 n={report['dataset']['n_evaluated']}（失败 {len(failed)}）  支持数 {support}")
    print(f"AUC(恶性 vs 其余) = {m['mal_vs_rest_auc']}  95%CI {m['auc_ci95']}（bootstrap {ci['n_boot']}）")
    print(f"三分类准确率 = {m['accuracy_3class']}  95%CI {m['accuracy_ci95']}  宏F1 = {m['macro_f1']}")
    for c in CLASSES:
        s = prf[c]
        print(f"  {c:10s} P={s['precision']:.4f} R={s['recall']:.4f} F1={s['f1']:.4f} n={s['support']}")
    t = m["at_threshold_0.5"]
    print(f"阈值0.5: 敏感性={t['sensitivity']} 特异性={t['specificity']} PPV={t['ppv']} NPV={t['npv']}")
    print(f"口径：{report['scope']}（公开集 held-out 下界，非注册级独立验证）")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
