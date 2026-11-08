"""正式评测 · CACTUS 心脏切面分类（T2b `/v1/analyze_view`）—— 首次评测。

数据集
------
CACTUS（Cardiac Ultrasound Dataset，FRDR 发布版，CC BY 4.0 可商用）。
`~/uai/data/t2b_cactus/{train,val,test}.csv` 由 `scripts/t2b_cactus_prep.py` 分层切分
（seed=42）。**本脚本只用 test.csv**（4494 张）：

    train 20977 / val 4492 / test 4494   合计 29963（A4C 7422 / PL 4161 / PSMV 6014 /
    Random 6021 / SC 6345）

⚠️ **这个 test 集不干净**（2026-11-08 实测，见 p82 的 contamination_audit）：
   29,963 张图只来自 **117 段视频**，而 prep 脚本是按**图像**随机切的 →
   官方 test 4494 张里 **96.1%** 所属视频在 train 出现过、**93.8%** 与 train 中同视频
   某帧帧号相差 ≤5 帧（同视频连续帧肉眼几乎无差别）；训练又恰在 train 上微调过 visual tower。
   因此本脚本产出的是「部署模型在官方 test.csv 上的分数」= **受污染上界**，
   **不得**当作 held-out 泛化能力对外报数。可辩护的数字见 `p82_cactus_video_disjoint.py`。

⚠️ 发布版 README 标称 6 类（含 PSAV），实收只有 5 类 —— 模型同样在 5 类上训练，
   因此**不把缺失的 PSAV 计为错误**，但必须在报告里写明（口径失真点之一）。

端点
----
`POST http://127.0.0.1:8003/v1/analyze_view`（multipart 字段名 `images`）：
CACTUS 微调后的 visual tower + 逻辑回归切面探针 + 质量回归头。响应字段：
`view_probs` / `view` / `standard` / `quality_grade`（Random 时 quality_grade=null）。

产出
----
* 切面分类准确率（+ bootstrap 95%CI）
* 每类 P/R/F1（+ macro / weighted 平均）、混淆矩阵、样本量、支持数
* 附：`standard`（标准切面 vs Random）二分类准确率/敏感性/特异性
* 附：质量分级 MAE（仅标准切面，quality_grade 有值的样本）

用法（服务器，需 8003 就绪）
--------------------------
    PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p81_cactus_view_formal.py \
        --split-csv ~/uai/data/t2b_cactus/test.csv \
        --report reports/p81_cactus_view_formal.json
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import random
import sys
import time
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

# 官方 5 类（发布版缺 PSAV；见模块 docstring）
CLASSES = ("A4C", "PL", "PSMV", "Random", "SC")


# --------------------------------------------------------------------- 指标
def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return float("nan")
    return vals[min(len(vals) - 1, max(0, int(q * (len(vals) - 1))))]


def per_class_prf(y_true: list[str], y_pred: list[str], classes: tuple[str, ...]) -> dict:
    out: dict[str, dict] = {}
    f1s, weights = [], []
    for c in classes:
        tp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p == c)
        fp = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t != c and p == c)
        fn = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == c and p != c)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        out[c] = {
            "precision": round(prec, 4), "recall": round(rec, 4),
            "f1": round(f1, 4), "support": tp + fn,
        }
        f1s.append(f1)
        weights.append(tp + fn)
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


def bootstrap_ci(
    y_true: list[str], y_pred: list[str], standard_true: list[int],
    standard_pred: list[int], grades: list[tuple[float, float]], n_boot: int, seed: int = 20261013,
) -> dict:
    rnd = random.Random(seed)
    idx = list(range(len(y_true)))
    accs, std_accs, maes = [], [], []
    for _ in range(n_boot):
        pick = [rnd.choice(idx) for _ in idx]
        yt = [y_true[i] for i in pick]
        yp = [y_pred[i] for i in pick]
        accs.append(sum(1 for t, p in zip(yt, yp, strict=True) if t == p) / len(yt))
        st = [standard_true[i] for i in pick]
        sp = [standard_pred[i] for i in pick]
        std_accs.append(sum(1 for t, p in zip(st, sp, strict=True) if t == p) / len(st))
        # 只对标准切面（预测且标注都非 Random）且有真值的样本算质量 MAE
        sel = [pick[j] for j in range(len(pick))
               if grades[pick[j]][0] is not None and grades[pick[j]][1] is not None]
        if sel:
            maes.append(sum(abs(grades[i][0] - grades[i][1]) for i in sel) / len(sel))
    accs.sort(); std_accs.sort(); maes.sort()
    return {
        "accuracy": [_pct(accs, 0.025), _pct(accs, 0.975)],
        "standard_accuracy": [_pct(std_accs, 0.025), _pct(std_accs, 0.975)],
        "quality_mae": [_pct(maes, 0.025), _pct(maes, 0.975)] if maes else [None, None],
        "n_boot": n_boot,
    }


# --------------------------------------------------------------------- 端点
def analyze_batch(clip_url: str, raws: list[bytes], timeout: float = 240) -> list[dict]:
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
    req = urllib.request.Request(
        f"{clip_url}/v1/analyze_view", data=body.getvalue(), method="POST"
    )
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data.get("results") or []


def load_split(csv_path: Path, limit: int = 0, seed: int = 20261013) -> list[dict]:
    """读分层 CSV；只保留**文件真实存在**的行（缺失会显式计入报告）。"""
    rows: list[dict] = []
    missing = 0
    with csv_path.open(encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            p = Path(r["filepath"])
            if not p.is_file():
                missing += 1
                continue
            rows.append({"path": str(p), "view": r["view"], "grade": r.get("grade") or ""})
    if missing:
        print(f"⚠️ CSV 里 {missing} 条文件不存在，已跳过（报告里记 csv_missing）")
    if limit and limit < len(rows):
        rnd = random.Random(seed)
        rows = rnd.sample(rows, limit)
        print(f"⚠️ 冒烟模式：随机抽 {len(rows)} 张（seed={seed}）")
    return rows, missing


# --------------------------------------------------------------------- 主流程
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--split-csv", default=str(Path.home() / "uai" / "data" / "t2b_cactus" / "test.csv"))
    ap.add_argument("--clip-url", default="http://127.0.0.1:8003")
    ap.add_argument("--report", default="reports/p81_cactus_view_formal.json")
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--bootstrap", type=int, default=1000)
    ap.add_argument("--limit", type=int, default=0, help=">0 时随机抽 N 张（冒烟；报告标 partial）")
    args = ap.parse_args()

    csv_path = Path(args.split_csv).expanduser()
    if not csv_path.is_file():
        print(f"切分 CSV 不存在：{csv_path}")
        return 2
    rows, csv_missing = load_split(csv_path, args.limit)

    t0 = time.monotonic()
    y_true: list[str] = []
    y_pred: list[str] = []
    eff_true: list[int] = []
    eff_pred: list[int] = []
    grades: list[tuple[float | None, float | None]] = []
    probs_norm: list[list[float]] = []
    failed: list[str] = []
    for start in range(0, len(rows), args.batch):
        chunk = rows[start : start + args.batch]
        raws = [Path(r["path"]).read_bytes() for r in chunk]
        try:
            results = analyze_batch(args.clip_url, raws)
        except Exception as exc:  # 显式记录失败，绝不静默丢样本
            failed.extend(r["path"] for r in chunk)
            print(f"  批次失败 [{start}:{start + len(chunk)}] {type(exc).__name__}: {exc}", flush=True)
            continue
        if len(results) != len(chunk):
            failed.extend(r["path"] for r in chunk[len(results):])
        for r, res in zip(chunk, results, strict=False):
            pred = (res or {}).get("view") or "Unknown"
            y_true.append(r["view"])
            y_pred.append(pred)
            eff_true.append(0 if r["view"] == "Random" else 1)
            eff_pred.append(int(bool((res or {}).get("standard"))))
            g = (res or {}).get("quality_grade")
            true_g: float | None = None
            try:
                true_g = float(r["grade"]) if r["grade"] != "" else None
            except ValueError:
                true_g = None
            # 质量真值只在标准切面存在（Random 无分级）
            if r["view"] == "Random":
                true_g = None
            grades.append((true_g, float(g) if g is not None else None))
            vp = (res or {}).get("view_probs") or {}
            probs_norm.append([float(vp.get(c, 0.0)) for c in CLASSES])
        print(f"  {min(start + len(chunk), len(rows))}/{len(rows)}", flush=True)

    if not y_true:
        print("没有任何样本成功分析 —— 检查 8003 与 /v1/analyze_view")
        return 2

    acc = sum(1 for t, p in zip(y_true, y_pred, strict=True) if t == p) / len(y_true)
    prf = per_class_prf(y_true, y_pred, CLASSES)
    cm = confusion_matrix(y_true, y_pred, CLASSES)
    std_acc = sum(1 for t, p in zip(eff_true, eff_pred, strict=True) if t == p) / len(eff_true)
    std_tp = sum(1 for t, p in zip(eff_true, eff_pred, strict=True) if t == 1 and p == 1)
    std_tn = sum(1 for t, p in zip(eff_true, eff_pred, strict=True) if t == 0 and p == 0)
    std_fp = sum(1 for t, p in zip(eff_true, eff_pred, strict=True) if t == 0 and p == 1)
    std_fn = sum(1 for t, p in zip(eff_true, eff_pred, strict=True) if t == 1 and p == 0)

    q_pairs = [(t, g) for t, g in grades if t is not None and g is not None]
    q_mae = (sum(abs(t - g) for t, g in q_pairs) / len(q_pairs)) if q_pairs else None
    q_rmse = ((sum((t - g) ** 2 for t, g in q_pairs) / len(q_pairs)) ** 0.5) if q_pairs else None

    ci = bootstrap_ci(y_true, y_pred, eff_true, eff_pred, grades, args.bootstrap)
    support = dict(Counter(y_true))

    report = {
        "probe": "p81_cactus_view_formal",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "contaminated_upper_bound" + ("_partial" if args.limit else ""),
        "scope_note": (
            "⚠️ 本数字是**受污染上界，不是泛化能力**。官方 test.csv 是按**图像**随机切的，"
            "而 29,963 张图只来自 117 段视频：实测 96.1% 的测试图所属视频在 train 中出现过，"
            "93.8% 与 train 中同视频某帧的**帧号相差 ≤5 帧**（见 p82 的 contamination_audit）。"
            "部署的 visual tower 恰在 train 上微调 → 等于拿「见过的视频里相邻的帧」当 held-out。"
            "可辩护的数字见 p82（视频不交集重训）。另：发布版缺 PSAV 一类（README 虚标 6 类，"
            "实收 5 类），且 Random（非标准切面）是模型自己的一类而非拒识类别。"
        ),
        "see_also": "p82_cactus_video_disjoint（视频不交集协议；本数据集唯一可辩护的泛化数字）",
        "dataset": {
            "name": "CACTUS (Cardiac Ultrasound Dataset, CC BY 4.0)",
            "organ": "心脏（5 类切面 + Random）",
            "split_csv": str(csv_path),
            "n_evaluated": len(y_true),
            "n_listed": len(rows),
            "n_failed": len(failed),
            "failed": failed,
            "csv_missing": csv_missing,
            "class_support": support,
            "classes": list(CLASSES),
        },
        "classifier": {
            "endpoint": f"{args.clip_url}/v1/analyze_view",
            "mechanism": "CACTUS-finetuned visual tower + logistic view probe + quality regressor (T2b)",
        },
        "metrics": {
            "view_accuracy": round(acc, 4),
            "view_accuracy_ci95": [round(v, 4) for v in ci["accuracy"]],
            "per_class": prf,
            "confusion": cm,
            "standard_vs_random": {
                "accuracy": round(std_acc, 4),
                "accuracy_ci95": [round(v, 4) for v in ci["standard_accuracy"]],
                "sensitivity_standard": round(std_tp / (std_tp + std_fn), 4) if (std_tp + std_fn) else None,
                "specificity_standard": round(std_tn / (std_tn + std_fp), 4) if (std_tn + std_fp) else None,
                "tp": std_tp, "fp": std_fp, "fn": std_fn, "tn": std_tn,
                "n_random": sum(1 for t in eff_true if t == 0),
                "n_standard": sum(1 for t in eff_true if t == 1),
            },
            "quality_grade": {
                "n_pairs": len(q_pairs),
                "mae": round(q_mae, 4) if q_mae is not None else None,
                "rmse": round(q_rmse, 4) if q_rmse is not None else None,
                "mae_ci95": [round(v, 4) if v is not None else None for v in ci["quality_mae"]],
                "note": "仅统计「真实标准切面 且 预测为标准切面 且有分级真值」的样本；Random 无分级",
            },
            "bootstrap_n": ci["n_boot"],
            "bootstrap_seed": 20261013,
        },
        "wall_s": round(time.monotonic() - t0, 1),
    }

    out = Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    m = report["metrics"]
    print(f"\n样本量 n={len(y_true)}（失败 {len(failed)}，CSV 缺失 {csv_missing}）  支持数 {support}")
    print(f"切面分类准确率 = {m['view_accuracy']}  95%CI {m['view_accuracy_ci95']}（bootstrap {ci['n_boot']}）")
    for c in CLASSES:
        s = prf[c]
        print(f"  {c:8s} P={s['precision']:.4f} R={s['recall']:.4f} F1={s['f1']:.4f} n={s['support']}")
    print(f"  宏平均 F1={prf['macro_avg']['f1']}  加权 F1={prf['weighted_avg']['f1']}")
    sv = m["standard_vs_random"]
    print(f"标准切面 vs Random 二分类准确率 = {sv['accuracy']}  敏感性={sv['sensitivity_standard']} "
          f"特异性={sv['specificity_standard']}（Random n={sv['n_random']}）")
    q = m["quality_grade"]
    print(f"质量分级 MAE = {q['mae']}  RMSE = {q['rmse']}（n={q['n_pairs']}）")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
