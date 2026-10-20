"""A.71：**RAG 指标一键报告**（口径固定，n 不足就如实说不足）。

**为什么要有这个脚本**：A.62~A.70 一路下来，指标被三件事污染过 ——
①样本是"提示词示例照抄"（98.7%）②判官跨语言（中文结论被拆成英文断言）③口径不明（raw 里混着
`分级/总分/置信度` 这类系统派生字段）。所以现在**只认一套写死的口径**，并用一个命令出全部数：

| 指标 | 口径（写死，不许运行时改） | 说明 |
|---|---|---|
| `faithfulness_knowledge` | `derived_stripped` + **中文断言** + **只算知识断言** | 主口径；影像所见/建议单列，不进分母 |
| `faithfulness_all` | 同上但算全部断言 | 仅作对照，**不作为门控** |
| `citation_coverage` | 句级：带来源标记的句子占比 | 主指标候选（可审计到段落） |
| `citation_correctness` | 引用编号/ID 是否落在本次证据范围内 | 只查"编造出处"，不查"支持与否" |
| `answer_relevancy` | 官方口径（问题反推 + 余弦） | 与判官同源 |

**三条纪律（打印与报告里都要能看见）**
1. **样本不足就报不足**：`n_measured` 逐指标给；`< MIN_SAMPLES` 时 `verdict` 记 `INSUFFICIENT_DATA`，
   **不许**给出"达标/不达标"结论；
2. **不写门限判定**：报告只给实测值 + 分位数，门限由 19 号按"干净分布"另行定；
3. **分母独立**：某指标未测到（判官挂了/无断言）就 `None`，不写 0、不写 1。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p52_rag_metric_report.py \
        --eval-set ~/uai/data/eval/clean_eval_set_20261020.jsonl --limit 50
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import statistics
import sys
import time

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from judge_eval_set import _answer_relevancy_of  # noqa: E402
from p44_faithfulness_caliber_a import _sample_of as faith_sample  # noqa: E402
from p46_citation_coverage import (  # noqa: E402
    correctness_of,
    coverage_of,
    evidence_ids_of,
    is_degraded,
)

#: 低于这个样本量只报"数据不足"，不出结论（写死）
MIN_SAMPLES = 30

#: 口径说明（进报告，避免以后又忘了当初判什么）
CALIBERS = {
    "faithfulness": "derived_stripped（剥分级/总分/置信度）+ 中文断言 + 只算知识断言（A 口径）",
    "citation": "句级来源标记覆盖率；正确率=引用编号落在 context_ids 范围内",
    "answer_relevancy": "RAGAS 官方口径（判官反推问题 + bge-m3 余弦）",
}


def summarize_scores(values: list[float]) -> dict:
    """一组分数的分布（n 太小也给数，但不给结论）。纯函数（守卫钉住）。"""
    clean = [float(v) for v in values if v is not None]
    n = len(clean)
    if not n:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None}
    ordered = sorted(clean)
    p10 = ordered[max(0, round(0.1 * (n - 1)))]
    p90 = ordered[max(0, round(0.9 * (n - 1)))]
    return {
        "n": n,
        "mean": round(sum(clean) / n, 4),
        "median": round(statistics.median(clean), 4),
        "p10": round(p10, 4),
        "p90": round(p90, 4),
    }


def verdict_of(samples: int, min_samples: int = MIN_SAMPLES) -> str:
    """样本够不够出结论。纯函数（守卫钉住）。"""
    return "INSUFFICIENT_DATA" if samples < min_samples else "MEASURED"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--eval-set",
        default=str(pathlib.Path.home() / "uai/data/eval/clean_eval_set_20261020.jsonl"),
    )
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/rag_metric_report.json")
    )
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--judge-url", default="http://127.0.0.1:8006")
    ap.add_argument("--model", default="Qwen3-8B")
    args = ap.parse_args()

    path = pathlib.Path(args.eval_set).expanduser()
    if not path.is_file():
        print(f"[FATAL] 评测集不存在：{path}")
        return 2
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    items = [
        r
        for r in rows
        if (r.get("conclusion") or "").strip() and r.get("context")
    ][: args.limit]
    if not items:
        print("[FATAL] 没有可判样本（需 conclusion + context）")
        return 2

    t0 = time.time()
    faith_k: list[float] = []
    faith_all: list[float] = []
    covs: list[float] = []
    corrs: list[float] = []
    relev: list[float] = []
    per: list[dict] = []
    failures: list[dict] = []

    for i, item in enumerate(items, 1):
        row: dict = {"request_id": item.get("request_id"), "organ": item.get("organ")}
        if is_degraded(item.get("conclusion") or ""):
            row["skipped"] = "degraded"
            per.append(row)
            continue

        # ① 忠实度（口径固定）
        try:
            f = faith_sample(item, args.judge_url, args.model, "zh", "derived_stripped")
            row["faithfulness_knowledge"] = f.get("faith_knowledge")
            row["faithfulness_all"] = f.get("faith_all")
            if row["faithfulness_knowledge"] is not None:
                faith_k.append(row["faithfulness_knowledge"])
            if row["faithfulness_all"] is not None:
                faith_all.append(row["faithfulness_all"])
        except Exception as exc:  # silent-ok: 失败已记入 per-sample 的 faithfulness_error 与 failures 汇总
            row["faithfulness_error"] = f"{type(exc).__name__}: {exc}"
            failures.append({"metric": "faithfulness", "request_id": row["request_id"],
                             "error": row["faithfulness_error"]})

        # ② 引用（覆盖率 / 正确率）
        cov = coverage_of(item.get("conclusion") or "")
        cor = correctness_of(cov["citations"], evidence_ids_of(item))
        row["citation_coverage"] = cov["coverage"]
        row["citation_correctness"] = cor["correctness"]
        if cov["coverage"] is not None:
            covs.append(cov["coverage"])
        if cor["correctness"] is not None:
            corrs.append(cor["correctness"])

        # ③ 答案相关性（官方口径）
        try:
            rel = _answer_relevancy_of(item, args.judge_url, args.model)
            row["answer_relevancy"] = rel.get("score")
            if rel.get("score") is not None:
                relev.append(rel["score"])
        except Exception as exc:  # silent-ok: 失败已记入 per-sample 的 answer_relevancy_error 与 failures 汇总
            row["answer_relevancy_error"] = f"{type(exc).__name__}: {exc}"
            failures.append({"metric": "answer_relevancy", "request_id": row["request_id"],
                             "error": row["answer_relevancy_error"]})

        per.append(row)
        print(
            f"  [{i}/{len(items)}] faith_k={row.get('faithfulness_knowledge')} "
            f"cov={row.get('citation_coverage')} rel={row.get('answer_relevancy')}"
        )

    report = {
        "probe": "p52_rag_metric_report",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "eval_set": str(path),
        "eval_set_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "calibers": CALIBERS,
        "min_samples": MIN_SAMPLES,
        "n_items": len(items),
        "metrics": {
            "faithfulness_knowledge": summarize_scores(faith_k),
            "faithfulness_all": summarize_scores(faith_all),
            "citation_coverage": summarize_scores(covs),
            "citation_correctness": summarize_scores(corrs),
            "answer_relevancy": summarize_scores(relev),
        },
        "verdict": verdict_of(len(faith_k)),
        "note": (
            "verdict=INSUFFICIENT_DATA 表示干净样本不足 MIN_SAMPLES，只看分布、不下结论；"
            "门限由 19 号按干净分布分位数另定，本报告不判定达标。"
        ),
        "failures": failures,
        "n_failed": len(failures),
        "per_sample": per,
        "wall_s": round(time.time() - t0, 1),
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n== RAG 指标（口径已固定）样本 {len(items)} 条 ==")
    for name, slot in report["metrics"].items():
        print(
            f"   {name:<24} n={slot['n']:>3} mean={slot['mean']} "
            f"median={slot['median']} p10={slot['p10']} p90={slot['p90']}"
        )
    print(f"   verdict: {report['verdict']}（MIN_SAMPLES={MIN_SAMPLES}）")
    print(f"报告：{out}  耗时 {report['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
