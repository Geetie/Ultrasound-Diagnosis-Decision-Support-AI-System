"""验证患者解释可读性检查（B3）准不准：黄金集人工标注 vs 检查器预测。

## 为什么要有这个脚本

`app/knowledge/explanation_readability.py` 的 9 项检查**自己准不准从来没人验证过**。
会误报（明明写得好的解释被扣分）也会漏报（写得差却放行）。

本脚本把 `data/eval/v2/patient_explanation_golden_v1.jsonl`（50 条人工标注）
喂给检查器，算混淆矩阵与准确率/精确率/召回率/特异度，并给出：
- 整条的 FP / FN 清单（哪几条判反了）
- 逐检查项的 TP/FP/FN（哪条规则太严、哪条太松）

## 用法

    python scripts/audit/p82_readability_golden_eval.py
    python scripts/audit/p82_readability_golden_eval.py --json report.json

退出码恒为 0（这是度量脚本，不是门禁）。要当门禁用请自己比 ``--min-accuracy``。
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import pathlib
import sys

CODES = (
    "no_main_message", "too_many_ideas", "long_sentence", "jargon_density",
    "passive_or_vague", "no_next_step", "risk_not_explained",
    "uncertainty_hidden", "scare_language",
)


def load_checker():
    """按文件路径加载检查器。

    不走 ``from app.knowledge...``：包级 ``__init__`` 会连带 import structlog 等
    运行期依赖，而这个模块本身是纯标准库的纯函数。按路径加载能保证本脚本
    **只依赖被测的那一个文件**，在最小环境里也能复现历史数字。
    """
    path = pathlib.Path(__file__).resolve().parents[2] / "app/knowledge/explanation_readability.py"
    spec = importlib.util.spec_from_file_location("_readability_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclasses 需要模块已注册
    spec.loader.exec_module(mod)
    return mod.check_readability


def evaluate(rows, check_readability) -> dict:
    tp = fp = tn = fn = 0
    case_results = []
    for r in rows:
        rep = check_readability(r["text"])
        pred = "pass" if rep.passed else "fail"
        truth = r["label"]
        if truth == "fail" and pred == "fail":
            tp += 1
        elif truth == "pass" and pred == "fail":
            fp += 1
        elif truth == "pass":
            tn += 1
        else:
            fn += 1
        case_results.append({
            "case_id": r["case_id"],
            "human": truth,
            "pred": pred,
            "hard_case_type": r.get("hard_case_type"),
            "expected_codes": sorted(r.get("expected_codes") or []),
            "predicted_codes": sorted(rep.codes()),
            "verdict": "ok" if pred == truth else ("FP" if truth == "pass" else "FN"),
        })

    n = len(rows)
    acc = (tp + tn) / n if n else 0.0
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    spec = tn / (tn + fp) if tn + fp else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0

    per_code = {}
    for c in CODES:
        c_tp = c_fp = c_fn = 0
        for r in rows:
            rep = check_readability(r["text"])
            expected = c in (r.get("expected_codes") or [])
            got = c in rep.codes()
            if expected and got:
                c_tp += 1
            elif got:
                c_fp += 1
            elif expected:
                c_fn += 1
        per_code[c] = {
            "expected_total": sum(1 for r in rows if c in (r.get("expected_codes") or [])),
            "tp": c_tp, "fp": c_fp, "fn": c_fn,
            "precision": round(c_tp / (c_tp + c_fp), 3) if c_tp + c_fp else None,
            "recall": round(c_tp / (c_tp + c_fn), 3) if c_tp + c_fn else None,
        }

    return {
        "n": n,
        "confusion": {"tp": tp, "fp": fp, "tn": tn, "fn": fn},
        "accuracy": round(acc, 4),
        "precision": round(prec, 4),
        "recall": round(rec, 4),
        "specificity": round(spec, 4),
        "f1": round(f1, 4),
        "per_code": per_code,
        "false_positives": [c for c in case_results if c["verdict"] == "FP"],
        "false_negatives": [c for c in case_results if c["verdict"] == "FN"],
        "cases": case_results,
    }


def main(argv: list[str] | None = None) -> int:
    root = pathlib.Path(__file__).resolve().parents[2]
    ap = argparse.ArgumentParser(description="可读性检查 × 黄金集（B3）")
    ap.add_argument("--dataset", default=str(root / "data/eval/v2/patient_explanation_golden_v1.jsonl"))
    ap.add_argument("--json", default=None, help="把完整报告写到这个路径")
    ap.add_argument("--min-accuracy", type=float, default=None,
                    help="给定时，准确率低于它就返回 1（当门禁用）")
    args = ap.parse_args(argv)

    path = pathlib.Path(args.dataset).expanduser()
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = evaluate(rows, load_checker())

    c = report["confusion"]
    print(f"黄金集: {path}  n={report['n']}")
    print(f"混淆矩阵: TP={c['tp']}  FP={c['fp']}  TN={c['tn']}  FN={c['fn']}")
    print(f"准确率={report['accuracy']:.4f}  精确率={report['precision']:.4f}  "
          f"召回率={report['recall']:.4f}  特异度={report['specificity']:.4f}  F1={report['f1']:.4f}")
    print()
    print(f"{'检查项':22s} {'应报':>4s} {'TP':>3s} {'FP':>3s} {'FN':>3s} {'精确率':>7s} {'召回率':>7s}")
    for code, m in report["per_code"].items():
        p = "-" if m["precision"] is None else f"{m['precision']:.2f}"
        r = "-" if m["recall"] is None else f"{m['recall']:.2f}"
        print(f"{code:22s} {m['expected_total']:4d} {m['tp']:3d} {m['fp']:3d} {m['fn']:3d} {p:>7s} {r:>7s}")
    print()
    if report["false_positives"]:
        print(f"误报 FP（检查器说不行、人工认为行）{len(report['false_positives'])} 条：")
        for c_ in report["false_positives"]:
            print(f"  {c_['case_id']} [{c_['hard_case_type']}] 报出={','.join(c_['predicted_codes'])}")
    if report["false_negatives"]:
        print(f"漏报 FN（检查器放行、人工认为不行）{len(report['false_negatives'])} 条：")
        for c_ in report["false_negatives"]:
            print(f"  {c_['case_id']} [{c_['hard_case_type']}] 应报={','.join(c_['expected_codes'])}")

    if args.json:
        out = pathlib.Path(args.json).expanduser()
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n完整报告已写入 {out}")

    if args.min_accuracy is not None and report["accuracy"] < args.min_accuracy:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
