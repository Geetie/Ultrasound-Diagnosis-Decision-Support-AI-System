"""第 4 组 4.3 口径 B（对照口径）：把**生产 prompt 模板**也算作支持来源。

## 为什么需要这个对照

主口径（A）只让判官对着**检索块**判"能不能推出这条断言"。但本系统的模型还被喂进了
**生产 prompt 模板**，模板里明写着：

* `app/agents/prompts/thyroid.md`：TI-RADS 评分表（`TR2（2 分）`、`TR4（4-6 分）`、
  `TR5（≥ 7 分）`）与三个评分示例；
* `app/agents/prompts/cardiac.md`：LVEF 分档（`normal | ≥ 55%`、`mildly_reduced | 45%-54%`）
  与 LVIDd/IVS/LVPW/FS 正常值表。

模型照这张表陈述（"≥55% 属于正常"、"4 分属于 TR4"）时，判官只查检索块必然判 0 ——
这是**尺子把系统自己的规则当成编造**。所以必须给一个"模板也算来源"的对照数。

## 算法（确定性、可复算、不用判官）

断言归一化（全角→半角、去标点空格、`≥/≤` 统一）后切成 **6/8/10/12 字滑窗片段**，
只要**任一**片段出现在（检索块 ∪ 该器官 prompt 模板 ∪ 通用规则）里，即视为"有出处"。

* 这是**下界**：同义改写（"EF 六成" vs "LVEF 60%"）仍会漏；
* 只作对照，**不替代**主口径（主口径 = 判官 NLI，报 0.0514）。

用法：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py \
        scripts/audit/p109_faith_caliber_b.py \
        --report ~/uai/reports/p108_hf_final4.json \
        --out ~/uai/reports/p109_faith_caliber_b.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from collections import defaultdict

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

_FULL2HALF = str.maketrans("０１２３４５６７８９％－～", "0123456789%-~")
#: 去掉空白与标点，但**保留 `= < >`** —— 否则 `≥55%` 与 `55%` 归一化成同一个串，
#: 会让"阈值方向写反"的断言被误判为"有出处"。
_STRIP = re.compile(r"[\s，。、；：（）()\[\]【】,.;:%\-~→\"'`*#]+")

#: 知识断言再分三层（规则透明；与 p108 内的同一套正则）
_RE_DERIVED = re.compile(
    r"总分|总评分|评分(?:为|范围|达到|对应)|评分总和|各项.{0,6}得分|分值为\s*\d|"
    r"\d+\s*分(?:\s*(?:对应|属于|归为|被归类|→))|\d+\s*分\s*$|"
    r"TR[1-5]\s*(?:等级|类别|分类|标准|的评分)|≥\s*\d+\s*分|≤\s*\d+\s*分|"
    r"\d+\s*[-~至]\s*\d+\s*分为",
    re.IGNORECASE,
)
_RE_SELFREF = re.compile(
    r"鉴别诊断(?:中|的|第)|证据(?:来源|来自|引用)|文献\s*\[|输出示例|"
    r"(?:第一|第二|第三)项|本条|上述|该结论|报告(?:中|里)",
    re.IGNORECASE,
)


def norm(text: str) -> str:
    """归一化（纯函数）。"""
    t = (text or "").translate(_FULL2HALF)
    t = t.replace("≥", ">=").replace("≤", "<=").replace("＞", ">").replace("＜", "<")
    return _STRIP.sub("", t)


def fragments(n: str, lo: int = 6, hi: int = 12) -> set[str]:
    """归一化文本 → 6/8/10/12 字滑窗片段集合（纯函数）。"""
    out: set[str] = set()
    if not n:
        return out
    if len(n) <= hi:
        if len(n) >= lo:
            out.add(n)
        return out
    for size in (hi, 10, 8, lo):
        for i in range(0, len(n) - size + 1):
            out.add(n[i : i + size])
    return out


def sub_bucket(statement: str) -> str:
    """知识断言再分 derived / selfref / pure（纯函数，守卫钉住）。"""
    s = statement or ""
    if _RE_DERIVED.search(s):
        return "derived"
    if _RE_SELFREF.search(s):
        return "selfref"
    return "pure"


async def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/p108_hf_final4.json"))
    ap.add_argument("--out", default=str(pathlib.Path.home() / "uai/reports/p109_faith_caliber_b.json"))
    ap.add_argument("--eval-set", default=str(
        pathlib.Path.home() / "uai/data/eval/gen_quality_assertive_20261108.jsonl"))
    args = ap.parse_args()

    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import KnowledgeChunkModel

    rep = json.loads(pathlib.Path(args.report).expanduser().read_text(encoding="utf-8"))
    meta = {
        json.loads(line)["request_id"]: json.loads(line)
        for line in pathlib.Path(args.eval_set).expanduser().open(encoding="utf-8")
        if line.strip()
    }
    ids = {k for m in meta.values() for k in (m.get("knowledge_ids") or [])}
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                select(KnowledgeChunkModel.chunk_id, KnowledgeChunkModel.content).where(
                    KnowledgeChunkModel.chunk_id.in_(list(ids))
                )
            )
        ).all()
    ctx_norm = {str(a): norm(str(b or "")) for a, b in rows}

    tpl_dir = pathlib.Path(__file__).resolve().parents[2] / "app" / "agents" / "prompts"
    tpl: dict[str, str] = {}
    for organ in ("thyroid", "liver", "cardiac"):
        f = tpl_dir / f"{organ}.md"
        tpl[organ] = norm(f.read_text(encoding="utf-8")) if f.is_file() else ""
    common = ""
    for extra in ("dispatcher.md", "knowledge.md"):
        f = tpl_dir / extra
        if f.is_file():
            common += norm(f.read_text(encoding="utf-8"))

    per: list[dict] = []
    tiers: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in rep["faithfulness"]["per_sample"]:
        rid = r.get("request_id")
        organ = r.get("organ") or (meta.get(rid, {}) or {}).get("organ") or ""
        ctx_a = "".join(ctx_norm.get(k, "") for k in (meta.get(rid, {}).get("knowledge_ids") or []))
        ctx_b = ctx_a + tpl.get(organ, "") + common
        for d in r.get("detail") or []:
            if d.get("bucket") != "knowledge":
                continue
            v = int(d.get("verdict") or 0)
            fs = fragments(norm(d.get("statement") or ""))
            hit_a = any(f in ctx_a for f in fs)
            hit_b = any(f in ctx_b for f in fs)
            sub = sub_bucket(d.get("statement") or "")
            tiers[sub][0] += 1
            tiers[sub][1] += v
            per.append(
                {
                    "request_id": rid,
                    "organ": organ,
                    "statement": d.get("statement"),
                    "judge_verdict": v,
                    "in_retrieval_blocks": hit_a,
                    "in_blocks_or_prompt": hit_b,
                    "sub_bucket": sub,
                }
            )

    n = len(per)
    sup_a = sum(1 for x in per if x["judge_verdict"] == 1)
    hit_a = sum(1 for x in per if x["in_retrieval_blocks"])
    hit_b = sum(1 for x in per if x["in_blocks_or_prompt"])
    out = {
        "n_knowledge_statements": n,
        "caliber_A_judge": {
            "supported": sup_a,
            "rate": round(sup_a / n, 4) if n else None,
            "note": "主口径：判官只对着检索块判（与 p108 主报告一致）",
        },
        "caliber_A2_substring_retrieval_only": {
            "supported": hit_a,
            "rate": round(hit_a / n, 4) if n else None,
            "note": "确定性旁证：只查检索块的滑窗子串命中（下界）",
        },
        "caliber_B_blocks_or_prompt": {
            "supported": hit_b,
            "rate": round(hit_b / n, 4) if n else None,
            "note": "对照口径：检索块或生产 prompt 模板（TI-RADS 评分表 / LVEF 分档）里有出处即算支持（下界）",
        },
        "judge_says_0_but_prompt_has_it": sum(
            1 for x in per if x["judge_verdict"] == 0 and x["in_blocks_or_prompt"]
        ),
        "knowledge_tiers": {
            k: {
                "n": v[0],
                "supported": v[1],
                "faith": round(v[1] / v[0], 4) if v[0] else None,
            }
            for k, v in tiers.items()
        },
        "per_statement": per,
    }
    path = pathlib.Path(args.out).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in out.items() if k != "per_statement"},
                     ensure_ascii=False, indent=2))
    print(f"\n报告：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
