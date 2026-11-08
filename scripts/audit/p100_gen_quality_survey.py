"""第 4 组 4.1 前置勘察：**生产判读数据现状**（只读，一条 SQL 都不写）。

要回答的四件事（任务书 4.1「先查清楚」）：
1. 生产表是哪张、多少条、多少条有结论；
2. 463 条残留（A.71 删完 52 条脏记录后的余量）的构成 —— 有多少真有结论；
3. 现有评测集（`clean_eval_set_20261020.jsonl` / `restored_non_echo_20261021.jsonl`）现状；
4. 知识库版本与块数（幻觉率/faithfulness 的分母来源）。

判据一律**显式计数**，不给任何"大概/多数"：
* `empty`  —— 结论正文为空；
* `degraded` —— 结论是降级串（llm_unavailable / grade_unparseable …）；
* `echo` —— 提示词示例照抄（复用生产同一把尺子 `app.inference.output_guard`）；
* `usable` —— 非空、非降级、非回显。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p100_gen_quality_survey.py \
        --report ~/uai/reports/p100_gen_quality_survey.json

退出码：0 出数；2 库不可达。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import Counter

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402
from p49_degraded_cause import degraded_kind  # noqa: E402

from app.inference.output_guard import detect_prompt_echo  # noqa: E402


def classify(findings: object) -> tuple[str, str]:
    """一条判读记录 → (类别, 细因)。纯函数（守卫钉住）。

    类别：`empty` / `degraded` / `echo` / `usable`。
    """
    text = _conclusion_of(findings)
    if not (text or "").strip():
        return "empty", "no_content"
    kind = degraded_kind(text)
    if kind != "not_degraded":
        return "degraded", kind
    echo = detect_prompt_echo(text)
    if echo:
        return "echo", echo
    if isinstance(findings, dict):
        fr = findings.get("fusion_result")
        if isinstance(fr, dict) and fr.get("degraded") is True:
            return "degraded", "fusion_degraded_flag"
    return "usable", "ok"


async def survey() -> dict:
    from sqlalchemy import func, select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult, KnowledgeChunkModel

    out: dict = {}

    async with AsyncSessionLocal() as session:
        total = await session.scalar(select(func.count()).select_from(DiagnosisResult))
        out["diagnosis_results_total"] = int(total or 0)

        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.created_at,
                    DiagnosisResult.status,
                    DiagnosisResult.findings,
                    DiagnosisResult.knowledge_ids,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

        cats: Counter[str] = Counter()
        reasons: Counter[str] = Counter()
        by_organ: dict[str, Counter[str]] = {}
        by_month: dict[str, Counter[str]] = {}
        usable_ids: list[str] = []
        for rid, organ, created, _status, findings, _kids in rows:
            cat, why = classify(findings)
            cats[cat] += 1
            reasons[f"{cat}:{why}"] += 1
            org = str(organ or "(未知)")
            by_organ.setdefault(org, Counter())[cat] += 1
            month = created.strftime("%Y-%m") if created else "(未知)"
            by_month.setdefault(month, Counter())[cat] += 1
            if cat == "usable":
                usable_ids.append(str(rid))

        out["by_category"] = dict(cats)
        out["by_reason"] = dict(reasons)
        out["by_organ"] = {k: dict(v) for k, v in sorted(by_organ.items())}
        out["by_month"] = {k: dict(v) for k, v in sorted(by_month.items())}
        out["usable_request_ids"] = usable_ids
        out["usable_n"] = len(usable_ids)

        # 有知识库上下文（knowledge_ids 非空）的可用记录数 —— 幻觉/faithfulness 要用
        with_kids = 0
        by_organ_usable = Counter()
        for _rid, organ, _created, _status, findings, kids in rows:
            cat, _ = classify(findings)
            if cat != "usable":
                continue
            by_organ_usable[str(organ or "(未知)")] += 1
            if kids:
                with_kids += 1
        out["usable_with_knowledge_ids"] = with_kids
        out["usable_by_organ"] = dict(by_organ_usable)

        # 知识库
        kb_rows = (
            await session.execute(
                select(
                    KnowledgeChunkModel.kb_version,
                    KnowledgeChunkModel.organ,
                    func.count(),
                ).group_by(KnowledgeChunkModel.kb_version, KnowledgeChunkModel.organ)
            )
        ).all()
        kb: dict[str, dict[str, int]] = {}
        for ver, organ, n in kb_rows:
            kb.setdefault(str(ver), {})[str(organ)] = int(n)
        out["knowledge_chunks"] = kb

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/p100_gen_quality_survey.json"))
    ap.add_argument("--eval-dir", default=str(pathlib.Path.home() / "uai/data/eval"))
    args = ap.parse_args()

    try:
        data = asyncio.run(survey())
    except Exception as exc:
        print(f"[FATAL] 库不可达或查询失败：{exc!r}")
        return 2

    # 现有评测集盘点（只看条数与字段丰度，不改）
    d = pathlib.Path(args.eval_dir).expanduser()
    eval_sets: dict[str, dict] = {}
    for f in sorted(d.glob("*.jsonl")):
        try:
            rows = [json.loads(line) for line in f.open(encoding="utf-8") if line.strip()]
        except Exception as exc:
            eval_sets[f.name] = {"error": repr(exc)}
            continue
        n = len(rows)
        with_conc = sum(1 for r in rows if (r.get("conclusion") or "").strip())
        with_ctx = sum(1 for r in rows if r.get("context"))
        echo = sum(1 for r in rows if detect_prompt_echo(r.get("conclusion") or ""))
        eval_sets[f.name] = {
            "n": n,
            "with_conclusion": with_conc,
            "with_context": with_ctx,
            "prompt_echo": echo,
            "bytes": f.stat().st_size,
        }
    data["eval_sets"] = eval_sets

    path = pathlib.Path(args.report)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps({k: v for k, v in data.items() if k != "usable_request_ids"},
                     ensure_ascii=False, indent=2)[:6000])
    print(f"\n报告：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
