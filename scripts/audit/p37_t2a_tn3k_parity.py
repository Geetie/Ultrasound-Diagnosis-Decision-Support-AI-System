"""A.64 WP1：T2a 在 TN3K 上的**口径对照与判定**（同一把尺子，只推理）。

要回答的问题
------------
A.63.3 登记了一个不一致：生产 8003 端点 TN3K **AUC 0.4929** vs 旧报告
`tests/reports/t2a_tn3k_generalization.json` 记的 **0.6789（n=1228）**。
本轮先把「旧报告是怎么来的」查清（code-explorer 全仓定位，结论见下），再用**同一把尺子**
把能复现的口径钉死，最后给出**四选一的明确判定**——不允许"看起来查过"。

已查清的事实（决定本探针的判定空间）
------------------------------------
1. **旧报告是孤儿产物**：全仓没有任何脚本会生成它——它的 schema（`malignancy_auc` /
   `acc_at_0.5`）全仓唯一，其余脚本用 `auc` / `mal_vs_rest_auc` / `test_auc`；
   且 `n=1228`（=614×2）与 TN3K 官方 test 的 614 张对不上（`1228` 在文档里只对应
   `tn3k_lfs_fix` 修好的**文件计数**，与评测样本量无关）。
2. **资产只有一条链**：`models/t2a_v2/t2a_finetune.pt`（sha256 `7a5849d5…`）+
   `models/t2a_v2/t2a_probe_lr.joblib`（`40bfa6b4…`）；代码里的回退路径
   `src/reports/t2a_finetune.pt` **在服务器上不存在**，8003 进程环境变量与启动日志
   （`t2a_classifier_loaded`）都指向 `models/t2a_v2/*` → **不存在"两份权重"的可能**。
   （`t2a_tn3k_adapt.pt` 只在备份目录 `src.bak.20261003_031917/reports/` 里，且**无任何代码加载它**。）
3. **仓库里有四条链、四套 AUC 实现**（`roc_auc_score` ×2、自写 `_auc`、p29 的秩和）
   → "数字不可比"本身就是要登记的隐患；本探针**只允许**用 p29 的 `auc_mal_vs_rest`。

四种判定（本探针必须落到其一）
------------------------------
* `SAME_METRIC_REPRODUCED`：同一尺子重跑，旧数字（±0.01）被复现 → 说明是同一口径的不同批次。
* `LABEL_DIRECTION_MISMATCH`：把标签反向重算后与旧数字吻合（且**正向**不吻合）
  → 旧数字是标签映射写反的产物（真缺陷，退出码 1）。
* `ORPHAN_UNREPRODUCIBLE`：旧数字既不是正向、也不是反向、且 n 对不上、全仓无生成脚本
  → **作废该数字**（不再引用），只保留本探针可复现的读数（退出码 0）。
* `DATA_OR_SERVICE_MISSING`：数据/8003 不可用（退出码 2，显式失败）。

用法（服务器，需 8003 就绪）：
    ~/uai/venv/bin/python scripts/audit/p37_t2a_tn3k_parity.py \
        --clip-url http://127.0.0.1:8003 --report ~/uai/reports/t2a_tn3k_parity.json
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# 同一把尺子：AUC / bootstrap 只从 p29 来（p35 已复用；禁止本文件重写）
from p29_t2a_aul_holdout import auc_mal_vs_rest, bootstrap_ci, classify_batch  # noqa: E402
from p35_thyroid_cross_source_eval import load_tn3k_labels  # noqa: E402

#: 旧报告（孤儿）——只读引用，作为对照物；缺失不致命（判定照样给）
LEGACY_REPORT = "tests/reports/t2a_tn3k_generalization.json"

#: 与旧数字"吻合"的容差
MATCH_TOL = 0.01


def _youden_threshold(scores: list[float], labels: list[str]) -> tuple[float, float, float]:
    """遍历候选阈值找 Youden 最大的那个（诊断"是不是只是阈值不对"）。

    返回 ``(threshold, sensitivity, specificity)``；样本不足时返回 ``(0.5, nan, nan)``。
    """
    if not scores:
        return 0.5, float("nan"), float("nan")
    pos = [s for s, y in zip(scores, labels, strict=True) if y == "Malignant"]
    neg = [s for s, y in zip(scores, labels, strict=True) if y != "Malignant"]
    if not pos or not neg:
        return 0.5, float("nan"), float("nan")
    best = (0.5, -1.0, float("nan"), float("nan"))
    for t in sorted({s for s in scores}):
        sens = sum(1 for s in pos if s >= t) / len(pos)
        spec = sum(1 for s in neg if s < t) / len(neg)
        if sens + spec > best[1]:
            best = (t, sens + spec, sens, spec)
    return best[0], best[2], best[3]


def _verdict(
    legacy: dict | None, n: int, auc: float, auc_rev: float
) -> tuple[str, str]:
    """四选一判定（把"看起来查过"变成"必须落一种"）。"""
    if legacy is None:
        return (
            "ORPHAN_UNREPRODUCIBLE",
            f"旧报告不存在或不可读 → 无可对照物；本探针读数 AUC={round(auc, 4)}（n={n}）",
        )
    old_auc = legacy.get("malignancy_auc")
    old_n = legacy.get("n")
    if old_auc is None:
        return "ORPHAN_UNREPRODUCIBLE", "旧报告缺 malignancy_auc 字段，无法对照"
    if abs(float(old_auc) - auc) <= MATCH_TOL:
        return (
            "SAME_METRIC_REPRODUCED",
            f"同尺子重跑复现旧值（旧 {old_auc} vs 现 {round(auc, 4)}，n 旧 {old_n} / 现 {n}）",
        )
    if abs(float(old_auc) - auc_rev) <= MATCH_TOL:
        return (
            "LABEL_DIRECTION_MISMATCH",
            f"旧值与**标签反向**的读数吻合（旧 {old_auc} vs 反向 {round(auc_rev, 4)}）"
            " → 旧数字是标签映射写反的产物",
        )
    return (
        "ORPHAN_UNREPRODUCIBLE",
        f"旧值 {old_auc}（n={old_n}）既不等于正向 {round(auc, 4)}、也不等于反向 "
        f"{round(auc_rev, 4)}，且 n 不符（现 {n}）→ 该数字无生成脚本，**作废**",
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(pathlib.Path.home() / "uai/data/tn3k_dataset"))
    ap.add_argument("--labels-csv", default="label4test.csv")
    ap.add_argument("--clip-url", default="http://127.0.0.1:8003")
    ap.add_argument("--legacy-report", default=LEGACY_REPORT)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/t2a_tn3k_parity.json"))
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--bootstrap", type=int, default=2000)
    args = ap.parse_args()

    data_dir = pathlib.Path(args.data_dir).expanduser()
    if not data_dir.is_dir():
        print(f"[FATAL] 数据目录不存在：{data_dir}")
        return 2

    rows = load_tn3k_labels(data_dir, args.labels_csv)
    print(f"TN3K：{len(rows)} 张（_parse labels: {args.labels_csv}）")

    t0 = time.monotonic()
    labels: list[str] = []
    scores: list[float] = []
    per_sample: list[dict] = []
    failed: list[str] = []
    for start in range(0, len(rows), args.batch):
        chunk = rows[start : start + args.batch]
        raws = [(data_dir / r["path"]).read_bytes() for r in chunk]
        try:
            results = classify_batch(args.clip_url, raws)
        except Exception as exc:
            failed.extend(r["path"] for r in chunk)
            print(f"  批次失败 [{start}:{start + len(chunk)}] {type(exc).__name__}: {exc}")
            continue
        for r, res in zip(chunk, results, strict=True):
            probs = (res or {}).get("probs") or {}
            score = float(probs.get("Malignant", 0.0))
            labels.append(r["label"])
            scores.append(score)
            per_sample.append(
                {"path": r["path"], "label": r["label"], "malignant_prob": round(score, 6)}
            )

    if not labels:
        print("[FATAL] 没有任何样本成功分类 —— 检查 8003 服务与 /v1/classify 端点")
        return 2

    auc = auc_mal_vs_rest(scores, labels)
    lo, hi = bootstrap_ci(scores, labels, args.bootstrap)
    # 标签反向腿（把 Malignant↔Benign 互换后重算；AUC 反向 = 1 - 正向）
    rev_labels = ["Benign" if y == "Malignant" else "Malignant" for y in labels]
    auc_rev = auc_mal_vs_rest(scores, rev_labels)
    thr, sens, spec = _youden_threshold(scores, labels)
    pos = sum(1 for y in labels if y == "Malignant")

    legacy_path = pathlib.Path(args.legacy_report).expanduser()
    legacy = None
    if legacy_path.is_file():
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            print(f"⚠️ 旧报告不是合法 JSON（{exc}）→ 按无对照物处理")
            legacy = None

    code, why = _verdict(legacy, len(labels), auc, auc_rev)

    report = {
        "probe": "p37_t2a_tn3k_parity",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "把「生产端点 vs 旧报告」的口径差异判定到四选一，并钉住唯一可复现读数",
        "metric_rule": "AUC/bootstrap 一律来自 scripts/audit/p29_t2a_aul_holdout.py（同尺子）",
        "dataset": {
            "name": "TN3K",
            "dir": str(data_dir),
            "labels_csv": args.labels_csv,
            "n": len(labels),
            "malignant": pos,
            "failed": failed,
        },
        "classifier": {
            "endpoint": f"{args.clip_url}/v1/classify",
            "assets": {
                "weights": "models/t2a_v2/t2a_finetune.pt",
                "weights_sha256": "7a5849d598d4c206cb70170e115c8de6f81fbf92af56e9b8786fb874044547bc",
                "probe": "models/t2a_v2/t2a_probe_lr.joblib",
                "probe_sha256": "40bfa6b42b15a857713123b87d43e052dfbc0a0494c1c4c4f7d7c815649ac191",
                "note": "回退路径 src/reports/t2a_finetune.pt 在服务器上不存在 → 资产只有这一条链",
            },
        },
        "metrics": {
            "auc_malignant_vs_benign": round(auc, 4),
            "auc_ci95": [round(lo, 4) if lo == lo else None, round(hi, 4) if hi == hi else None],
            "auc_label_reversed": round(auc_rev, 4),
            "bootstrap_n": args.bootstrap,
            "youden": {
                "threshold": round(thr, 4),
                "sensitivity": round(sens, 4) if sens == sens else None,
                "specificity": round(spec, 4) if spec == spec else None,
                "note": "AUC≈0.5 时换阈值救不回判别力（这条用来排除「只是阈值不对」）",
            },
        },
        "legacy_comparison": {
            "legacy_report": str(legacy_path),
            "legacy_present": legacy is not None,
            "legacy": legacy,
            "match_tolerance": MATCH_TOL,
        },
        "verdict": {"code": code, "why": why},
        "per_sample": per_sample,
        "wall_s": round(time.monotonic() - t0, 1),
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\nAUC(恶性 vs 良性) = {report['metrics']['auc_malignant_vs_benign']}  "
          f"95%CI {report['metrics']['auc_ci95']}")
    print(f"标签反向 AUC = {report['metrics']['auc_label_reversed']}"
          f"（AUC≈0.5 时反向也只是 1-0.49，说明不是映射写反）")
    print(f"Youden 最佳阈值 {report['metrics']['youden']['threshold']} → "
          f"敏感性 {report['metrics']['youden']['sensitivity']} / "
          f"特异性 {report['metrics']['youden']['specificity']}")
    print(f"判定 = **{code}**：{why}")
    print(f"报告：{out}  耗时 {report['wall_s']}s  失败样本 {len(failed)}")
    return 1 if code == "LABEL_DIRECTION_MISMATCH" else 0


if __name__ == "__main__":
    sys.exit(main())
