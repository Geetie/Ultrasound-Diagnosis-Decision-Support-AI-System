#!/usr/bin/env python3
"""带质量门的合成（generate → gate → retry），把 p120 与 p121 串成闭环。

## 为什么需要闭环

实测（p121 在 10 条上）：**50% 不合规**
- 4 条编造文献名（`C-TIRADS 2018` / `LI-RADS 2017`）
- 3 条跨体系混用（甲状腺问题出现 LI-RADS）
- 2 条把原文复述当 query

**纯 LLM 合成不可信。** 但也不能全靠人工——那是回到"耗时且昂贵"的老路。

**闭环做法**：
```
场景 → LLM 合成 → p121 五道闸
                    ├─ 通过 → 收下
                    └─ 不通过 → 把「具体哪里不合格」写回提示词 → 重试（≤2 次）
                                  └─ 仍不通过 → 标 rejected（不硬塞进数据集）
```

## 关键：重试时要把失败原因告诉模型

不是简单"再生成一次"，而是：
> 「你上一次的输出有两个问题：1) 你写了《C-TIRADS 2018》，但原文里没有这个文献名；
>  2) 这是甲状腺问题，不该出现 LI-RADS。请重写，不要提及任何文献名。」

**这是 Ragas 没写、但实测必需的一步。**

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p122_synth_with_gate.py \
    --scenarios /tmp/scenarios.jsonl --kg /tmp/kg.json --kb-version kb_v2.1.0 \
    --out /tmp/synth_clean.jsonl --limit 60 --concurrency 3
```
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# 复用 p120 的 prompt 构建与 p121 的判据
sys.path.insert(0, str(_SRC / "scripts" / "audit"))
from p121_synth_quality_gate import check_one  # noqa: E402

MAX_RETRY = 2

_RETRY_NOTE = """

## ⚠️ 你上一次的输出不合格，必须修正

{problems}

**特别注意**：
1. **不要提及任何文献名称或年份**（不要写「依据《XXX》」「根据《XXX 2017》」）。
   你不知道原文出自哪份文件，就不要假装知道。用「原文提到：……」代替。
2. 甲状腺问题**只能**用 C-TIRADS / TI-RADS；肝脏只用 LI-RADS；心脏用 LVEF/E/e'。
   不许跨体系。
3. query 必须是**一个问句**（含问号或疑问词），≤80 字，不要把原文抄一遍。

请重新输出 JSON。"""


def _build_prompt(sc: dict, nodes_content: list[str], problems: list[str] | None = None) -> str:
    from p120_synthesize_queries import _build_prompt as base

    p = base(sc, nodes_content)
    if problems:
        p += _RETRY_NOTE.format(problems="\n".join(f"- {x}" for x in problems))
    return p


async def synth_with_gate(
    client, sc: dict, node_map: dict, kb_text: str, ids: set[str], max_tokens: int
) -> dict:
    from app.inference.protocols import InferenceRequest

    contents = [node_map.get(nid, "") for nid in sc["nodes"]]
    contents = [c for c in contents if c]
    if not contents:
        return {"scenario_id": sc["scenario_id"], "rejected": True, "reason": "no_content"}

    problems: list[str] | None = None
    last: dict | None = None
    for attempt in range(MAX_RETRY + 1):
        prompt = _build_prompt(sc, contents, problems)
        try:
            r = await client.generate(
                InferenceRequest(prompt=prompt, max_tokens=max_tokens, temperature=0.3)
            )
        except Exception as exc:
            return {"scenario_id": sc["scenario_id"], "rejected": True,
                    "reason": f"{type(exc).__name__}: {exc}"}

        text = (r.text or "").strip()
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            problems = ["输出不是合法 JSON"]
            continue
        try:
            obj = json.loads(m.group(0))
        except json.JSONDecodeError:
            problems = ["JSON 解析失败"]
            continue

        row = {
            "scenario_id": sc["scenario_id"],
            "organ": sc["organ"],
            "persona": sc["persona"],
            "hop": sc["hop"],
            "specificity": sc["specificity"],
            "length": sc["length"],
            "relationship": sc["relationship"],
            "query": str(obj.get("query") or "").strip(),
            "reference_answer": str(obj.get("reference_answer") or "").strip(),
            "answerable": bool(obj.get("answerable", True)),
            "gold": [{"chunk_id": nid} for nid in sc["nodes"]],
            "source": "synthetic",
            "needs_human_review": True,
            "attempts": attempt + 1,
        }
        probs = check_one(row, kb_text, ids)
        if not probs:
            row["gate_passed"] = True
            return row
        problems = probs
        last = row

    # 三次都不过 → 标 rejected，**不硬塞进数据集**
    return {
        "scenario_id": sc["scenario_id"],
        "rejected": True,
        "reason": "gate_failed_after_retries",
        "last_problems": problems,
        "last_query": (last or {}).get("query", "")[:120],
    }


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="带质量门的合成（p122）")
    ap.add_argument("--scenarios", default="/tmp/scenarios.jsonl")
    ap.add_argument("--out", default="/tmp/synth_clean.jsonl")
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--max-tokens", type=int, default=700)
    args = ap.parse_args(argv)

    scs = [json.loads(l) for l in Path(args.scenarios).read_text(encoding="utf-8").splitlines() if l.strip()]
    scs = scs[args.offset : args.offset + args.limit]
    print(f"待合成 {len(scs)} 个场景（最多 {MAX_RETRY + 1} 次尝试/条）")

    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    ids_needed = sorted({nid for sc in scs for nid in sc["nodes"]})
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text("SELECT chunk_id, content FROM knowledge_chunks WHERE chunk_id = ANY(:ids)"),
                {"ids": ids_needed},
            )
        ).fetchall()
        all_rows = (
            await s.execute(
                text("SELECT chunk_id, content FROM knowledge_chunks WHERE kb_version = :v"),
                {"v": args.kb_version},
            )
        ).fetchall()

    node_map = {cid: (c or "") for cid, c in rows}
    kb_text = "\n".join((c or "") for _c, c in all_rows)
    ids = {c for c, _ in all_rows}
    print(f"节点正文 {len(node_map)}/{len(ids_needed)}；KB 全文 {len(kb_text)} 字")

    from app.config.settings import settings
    from app.inference.vllm import VLLMEngine

    client = VLLMEngine(
        base_url=f"http://{settings.vllm_host}:{settings.vllm_port}",
        timeout_seconds=180.0,
        default_model=settings.vllm_default_model,
    )

    sem = asyncio.Semaphore(args.concurrency)
    done = 0

    async def worker(sc):
        nonlocal done
        async with sem:
            r = await synth_with_gate(client, sc, node_map, kb_text, ids, args.max_tokens)
            done += 1
            if done % 10 == 0:
                print(f"  进度 {done}/{len(scs)}", flush=True)
            return r

    results = await asyncio.gather(*(worker(s) for s in scs))

    # 去重
    def _grams(t: str) -> set[str]:
        t = "".join(ch for ch in t if ch.isalnum())
        return {t[i : i + 3] for i in range(max(1, len(t) - 2))}

    def _jac(a: str, b: str) -> float:
        ga, gb = _grams(a), _grams(b)
        return len(ga & gb) / len(ga | gb) if ga and gb else 0.0

    kept, seen, dropped = [], [], 0
    for r in results:
        if r.get("rejected"):
            kept.append(r)
            continue
        q = r.get("query") or ""
        if any(_jac(q, p) >= 0.7 for p in seen):
            dropped += 1
            continue
        seen.append(q)
        kept.append(r)

    out = Path(args.out)
    with out.open("w", encoding="utf-8") as f:
        for r in kept:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    ok = sum(1 for r in kept if r.get("gate_passed"))
    rej = sum(1 for r in kept if r.get("rejected"))
    print(f"\n=== 结果 ===")
    print(f"  过闸 {ok}")
    print(f"  被拒 {rej}")
    print(f"  去重丢弃 {dropped}")
    print(f"  过闸率 {ok / max(len(scs), 1) * 100:.1f}%")
    if ok:
        srs = [r for r in kept if r.get("gate_passed")]
        from collections import Counter

        print(f"  尝试次数分布: {dict(Counter(r.get('attempts') for r in srs))}")
        ql = sorted(len(r["query"]) for r in srs)
        al = sorted(len(r["reference_answer"]) for r in srs)
        print(f"  query 长度: min {ql[0]} P50 {ql[len(ql)//2]} max {ql[-1]}")
        print(f"  答案长度: min {al[0]} P50 {al[len(al)//2]} max {al[-1]}")
    print(f"写出 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
