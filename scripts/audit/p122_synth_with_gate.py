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

#: 首轮就加上的硬约束（不依赖重试）
#:
#: 实测：仅靠「禁止编造文献名」的否定指令，10 条里仍有 4 条编造。
#: 改成「给出可核查的替代写法」，比单纯禁止有效。
_HARD_RULES = """

## 输出前的自检（三条，缺一不可）

1. **答案里不许出现《》书名号**。要说明依据就写「原文提到：……」。
   理由：你不知道这段原文出自哪份文件，写书名就是编造。
2. **器官与体系必须匹配**：
   thyroid → 只可出现 C-TIRADS / TI-RADS / TR1-TR5
   liver   → 只可出现 LI-RADS / LR-1~LR-5 / CEUS / OPTN
   cardiac → 只可出现 LVEF / EF / E/e' / GLS / ASE / BSE
3. **query 必须是一个问句**（含问号或「要不要/怎么/为什么/多少」等疑问词），
   且不超过 80 字。**不要把原文抄一遍。**

违反任何一条，你的输出会被程序拒绝。"""

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


#: few-shot 正例（比否定指令对 8B 更有效 —— 待实测确认）
#:
#: 理由：8B 模型对「不要做 X」的遵循能力弱（实测编造文献名 4/10），
#: 但对「照这个样子写」的模仿能力强。所以把「正确答案长什么样」直接演出来。
_FEWSHOT = """

## 参考答案的正确写法（照这个风格，不要写文献名）

**例 1（甲状腺，住院医师问）**
问：4A 类结节 13mm 且紧邻被膜，要不要穿刺？
答：原文提到 4A 类的 FNA 阈值是 >15mm，但紧邻被膜、气管或喉返神经时阈值降到 >10mm。
本例 13mm 已超过 10mm，属于「紧邻被膜」情形，可以考虑穿刺；需结合患者意愿与操作条件。
**注意这个答案里没有出现任何《书名》或年份。**

**例 2（肝脏，主治问）**
问：CEUS 上 LR-4 和 LR-5 的关键差别在哪？
答：原文指出 LR-5 需要「动脉期高增强 + 晚且轻度洗脱」，而 LR-4 是介于中间的不确定档。
两者差别不在大小，而在洗脱的时机与程度；超声造影不显示包膜，所以包膜不能作为判据。
**注意：只用了 LI-RADS 体系，没有混入甲状腺的分级码。**

**例 3（心脏，临床科室问）**
问：报告写 EF 38%，我这个病人要不要转心内科？
答：原文提到 EF ≤35% 属于缺血性左室功能障碍的关键分层点，38% 已接近该阈值。
虽未达 35%，但已属中度降低，建议结合症状与负荷试验结果，与心内科共同评估。
**注意：答案给出了「所以该怎么办」，不是只复述数值。**
"""


#: 书名号引用模式
_CITE_RE = re.compile(r"[依根]?据?\s*《[^》]{2,40}》\s*(?:原文|指南)?\s*[：:，,]?\s*")


def _strip_fake_citations(text: str) -> tuple[str, int]:
    """删除答案里的《书名》引用，换成中性表述。

    ## 为什么必须程序处理（2026-11-09 三臂实测）

    | 臂 | 提示词策略 | 被拒条目里编造文献名的比例 |
    |---|---|---|
    | 基线 | 否定指令「禁止编造」 | 4/9 |
    | B | 改成「用『原文提到：……』代替」 | 7/8 |
    | C | 加器官体系白名单 | 7/8 |

    **提示词层面三种写法都压不住 8B 模型。** 所以改成程序清理：

    ```
    「依据《C-TIRADS 2018》原文：4A 类阈值是 >15mm」
    → 「原文提到：4A 类阈值是 >15mm」
    ```

    保住事实内容，去掉不可审计的伪引用。

    Returns:
        ``(清理后的文本, 删掉的引用数)``
    """
    n = 0

    def _repl(m: re.Match[str]) -> str:
        nonlocal n
        n += 1
        return "原文提到："

    out = _CITE_RE.sub(_repl, text or "")
    # 清理可能残留的重复冒号与多余标点
    out = re.sub(r"原文提到：\s*原文提到：", "原文提到：", out)
    out = re.sub(r"\s{2,}", " ", out).strip()
    return out, n


def _build_prompt(sc: dict, nodes_content: list[str], problems: list[str] | None = None) -> str:
    """构建合成提示词。

    ## 实测记录：few-shot 有害，已移除（2026-11-09）

    我曾加过三个 few-shot 正例（4A 类 13mm / LR-4 vs LR-5 / EF 38%），
    想让 8B 模型模仿"正确答案的样子"。**实测更差**：

    | 臂 | 过闸 | 去重丢弃 | 过闸率 |
    |---|---|---|---|
    | 无 few-shot | 9 | 2 | 45% |
    | **有 few-shot** | **6** | **13** | **30%** |

    **根因**：模型把示例当成"答案模板"直接套用 ——
    6 条过闸里有 4 条都在问「4A 类 13mm 紧邻被膜要不要穿刺」，
    还有一条把甲状腺的 TR4 标到了 liver 器官上。

    8B 模型的模仿能力强但**区分不了"示范风格"与"示范内容"**。
    所以不用 few-shot，改用**结构化约束 + 程序校验 + 重试时告知具体问题**。
    """
    from p120_synthesize_queries import _build_prompt as base

    p = base(sc, nodes_content) + _HARD_RULES
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

        # ----------------------------------------------------------------
        # 后处理 1：删除编造的文献名引用（2026-11-09 实测必需）
        #
        # 8B 模型无法稳定遵守「不要写文献名」——实测三臂（否定指令/替代写法/自检清单）
        # 之后，被拒条目里仍有 7/8 是编造文献名（《LI-RADS 2017》《C-TIRADS 2018》）。
        #
        # **与其继续加提示词，不如程序清理**：
        #   「依据《C-TIRADS 2018》原文：4A 类阈值是 >15mm」
        #   → 「原文提到：4A 类阈值是 >15mm」
        #
        # 这样既保住了答案的事实内容，又去掉了不可审计的伪引用。
        # 清理后仍过不了闸的（如答案因此变空）才算真失败。
        # ----------------------------------------------------------------
        answer_text = str(obj.get("reference_answer") or "").strip()
        query_text = str(obj.get("query") or "").strip()
        # **query 也要清**（2026-11-09 实测漏了这步）：
        # 过闸的 12 条里仍有「这个 13 分结节按 C-TIRADS 2018 和 TI-RADS 2015 分别归哪一类？」
        # —— 我只清了答案，query 里的伪引用没清。
        answer_text, n_cite = _strip_fake_citations(answer_text)
        query_text, n_cite_q = _strip_fake_citations(query_text)
        n_cite += n_cite_q

        row = {
            "scenario_id": sc["scenario_id"],
            "organ": sc["organ"],
            "persona": sc["persona"],
            "hop": sc["hop"],
            "specificity": sc["specificity"],
            "length": sc["length"],
            "relationship": sc["relationship"],
            "query": query_text,
            "reference_answer": answer_text,
            "citations_stripped": n_cite,
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

    all_scs = [json.loads(l) for l in Path(args.scenarios).read_text(encoding="utf-8").splitlines() if l.strip()]

    # ----------------------------------------------------------------
    # **按格子分层抽样**（2026-11-09 实测必需）
    #
    # 原来直接 `all_scs[offset:offset+limit]` —— 而 p119 输出的顺序是
    # 「先所有 (single,specific) 的 4 persona × 3 length」，
    # 所以 limit=20 时**20 条全来自同一个象限**（resident/single/specific）。
    # 后果：指标看起来在涨，但覆盖的只是矩阵的一角。
    #
    # 修法：按 (hop, specificity, persona, length) 分组后轮流取，
    # 保证 limit 条覆盖尽可能多的格子。
    # ----------------------------------------------------------------
    from collections import defaultdict as _dd

    cells: dict[tuple, list] = _dd(list)
    for sc in all_scs:
        key = (sc["hop"], sc["specificity"], sc["persona"], sc["length"])
        cells[key].append(sc)

    order = sorted(cells)
    picked: list = []
    round_i = 0
    while len(picked) < args.limit:
        added = False
        for key in order:
            if len(picked) >= args.limit:
                break
            if round_i < len(cells[key]):
                picked.append(cells[key][round_i])
                added = True
        if not added:
            break
        round_i += 1

    scs = picked[args.offset : args.offset + args.limit]
    print(f"分层抽样：{len(all_scs)} 个场景 → 取 {len(scs)} 条，"
          f"覆盖 {len({(s['hop'],s['specificity'],s['persona'],s['length']) for s in scs})} 个格子")
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
