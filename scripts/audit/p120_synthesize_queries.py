#!/usr/bin/env python3
"""从场景合成 query + reference_answer（按 Ragas 的 _generate_sample）。

## 与 p119 的分工（照 Ragas 的两段式）

| 阶段 | Ragas | 本项目 |
|---|---|---|
| 生成场景骨架 | `_generate_scenarios` | `p119_gen_scenarios.py` |
| **场景 → 成品 query** | **`_generate_sample`** | **本脚本** |

## 为什么必须调 LLM 而不能模板拼

1. 模板拼会撞 p79 的「模板污染 ≤10%」闸
2. 四个 persona 的措辞差异很大（住院医师 vs 医务处），模板拼不出
3. Ragas 的 `_generate_sample` 本来就用 LLM

## 为什么还要人工审核

LLM 生成的医疗 query 可能临床不合理（问了个医生不会问的问题，或答案与指南相反）。
所以本脚本的输出**标记 `source: "synthetic"` 且 `needs_human_review: true`**，
必须过 L3 人工审核才能进最终数据集。

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p120_synthesize_queries.py \
    --scenarios /tmp/scenarios.jsonl --out /tmp/synth.jsonl --limit 60
```
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: 长度 → token 预算提示
#: 器官中文名（给模型看，英文它容易忽略）
_ORGAN_CN = {"thyroid": "甲状腺", "liver": "肝脏", "cardiac": "心脏"}

#: 器官 → 允许的分级体系。
#:
#: ## 为什么必须显式写在 prompt 里（2026-11-09 实测）
#:
#: 原来 prompt 只写「器官：liver」，结果模型生成了
#: 「4A 类结节 13mm 紧邻被膜，要不要穿刺？」—— 这是甲状腺问题，被标成 liver。
#:
#: **根因**：原文里出现了「4A」这个甲状腺的码，模型跟着原文走，忽略了英文器官标签。
#: 修法：prompt 里给**中文器官名 + 明确的白名单**，并允许它拒答。
_ORGAN_SYSTEM_HINT = {
    "thyroid": "C-TIRADS / TI-RADS / TR1-TR5（不得出现 LI-RADS、LR-x）",
    "liver": "LI-RADS / LR-1~LR-5 / LR-M / CEUS / OPTN（不得出现 C-TIRADS、TRx）",
    "cardiac": "LVEF / EF / E/e' / GLS / ASE / BSE（不得出现 C-TIRADS、LI-RADS）",
}

#: 问法骨架池（2026-11-09 加）。
#:
#: ## 为什么需要（实测证据）
#:
#: G 臂：关系池扩到 1331 条（100% 唯一），但去重丢弃仍 47%。
#: 检查发现**节点对不重复，query 却高度同构**：
#:   「肝内结节 13mm 且紧邻被膜，要不要穿刺？」
#:   「肝脏结节 13mm，紧邻被膜，要不要穿刺？」
#:
#: **根因**：prompt 固定了「persona + 意图 + 长度」三段，模型只能产出结构相同的问题。
#:
#: **修法**：给每格一组问法骨架，随机选一个塞进 prompt。
#: 骨架本身**不含具体临床内容**（那是原文决定的），只定"话怎么说"。
_ASK_SHAPES: dict[str, tuple[str, ...]] = {
    "resident": (
        "直接问规则：这个征象/数值按指南算什么？",
        "带着困惑问：我不确定这个情况算哪一档，怎么判断？",
        "拿不准要不要往上/往下调一档，问依据。",
        "问「如果…是不是就属于…」这种假设式判定。",
        "先复述自己看到的征象，再问积几分/归几类。",
    ),
    "attending": (
        "问边界：指南在这条上有没有例外或有争议的地方？",
        "拿两个相近档位对比，问差别在哪。",
        "问「什么情况下这条不适用」。",
        "问操作层面的取舍：同样结论下两种处理怎么选。",
        "质疑式提问：这条规则会不会导致过度/不足诊断？",
    ),
    "clinician": (
        "拿到报告先问这个分级意味着什么、严不严重。",
        "问下一步该做什么（复查/转诊/手术/观察）。",
        "问如果不管它会怎样，风险多大。",
        "问需不需要再做什么检查来确认。",
        "问对病人的实际影响（要不要住院、多久能好）。",
    ),
    "qa": (
        "问本院该以哪一套为准。",
        "问两套指南在这条上数值/分类不一致，怎么向临床解释。",
        "问如果医生按另一套判读，质控该怎么处理。",
        "问这条规则的出处与版本，便于归档。",
        "问两套体系并存会不会导致同一病人两种结论。",
    ),
}

_LEN_HINT = {
    "short": "15-25 字，像医生快速提问",
    "medium": "25-45 字，带一点上下文",
    "long": "45-80 字，像粘了一段病历后提问",
}

#: 抽取式答案生成（2026-11-09 实测有效的关键改动）
#:
#: ## 为什么必须改成抽取式
#:
#: L3 人工审核 20 条，15 条不合格，**全部是医学知识错误**：
#:   「4 类以上恶性可能性逐渐减小」（方向反了）
#:   「APHE 分 6 级」（LI-RADS 的 APHE 是二值）
#:   「13mm 紧邻被膜积分为 2 分」（把 FNA 阈值当积分）
#:
#: 根因：II-Medical-8B **不知道这些医学事实**，让它「生成答案」必然编造。
#:
#: **修法：不让它生成，让它摘录。**
#:
#: 实测三条 L3 抓错案例，抽取式全部答对：
#:   「APHE 分几级」→「分为「有 APHE」与「无 APHE」两种情形」✅
#:   「13mm 紧邻被膜要不要穿刺」→「阈值 >15mm；紧邻被膜降为 >10mm」✅
#:   「4 类以上恶性可能变大还是变小」→「5 类高度提示恶性（>90%）」✅
#:
#: **原理**：摘录任务只考「能不能找到并对齐原文」，不考「懂不懂医学」。
#: 8B 模型对齐原文的能力足够，医学知识不足的问题被绕开了。
_EXTRACT_SYSTEM = """你是医学资料摘录员。给定指南原文和问题，
**只能从原文里摘录能回答该问题的句子**，不许用自己的知识补充。

## 铁律
1. 答案的每个字都必须能在原文里找到（可以拼接多句，但不许改写、不许补充）
2. 原文没提到答案 → 只输出 {"answerable": false}
3. **不许推断、不许举一反三、不许补充原文没有的数值或定义**

## 为什么这么严
上一版让模型自己写答案，出现了「APHE 分 6 级」「4 类以上恶性可能逐渐减小」
这类医学错误。**摘录不会犯这种错**——原文没有的东西，摘不出来。"""

_SYSTEM = """你是超声医学领域的出题专家。给定指南原文和出题要求，
写一个**医生在临床工作中真的会问的问题**，并给出**严格依据原文的参考答案**。

## 什么样的算「医生真的会问」

**好问题**（针对具体患者/具体决策）：
- 「4A 类结节 13mm 且紧邻被膜，要不要穿刺？」
- 「这个 5 分结节按两套指南分别归哪一类？该听谁的？」
- 「报告写 TR4，我作为外科医生该不该安排手术？」

**坏问题**（纯技术手册查询，医生不会这样问）：
- 「MI 的范围是多少？」← 技师调机才关心，不是临床问题
- 「扫描角度怎么设置？」← 同上
- 「大分割方案每日剂量多少？」← 这是放疗科的事，不是超声判读

**判据**：这个问题问出口，是为了**给某个病人做决策**吗？如果不是，就不要出。

## 参考答案的写法（**摘录式，不是生成式**）

- **≥40 字**
- **逐字摘录原文**：答案的每个字都必须能在给定原文里找到
  （可以拼接原文的多句，但**不许改写、不许补充、不许推断**）
- 引用原文里的具体数值/分级/阈值
- **禁止编造文献名称与年份**：不要写「依据《C-TIRADS 2018》」「根据《LI-RADS 2017》」这类
  ——你不知道原文出自哪份文件，就不要假装知道。
  要引就写「原文提到：……」或「指南指出：……」。
  **编造文献名比不写更糟**：审计时无法核对。
- **不许**补充原文没有的医学知识
- **最关键的纪律**：如果原文没说「APHE 分几级」，就不要写几分级；
  如果原文没说「4 类以上恶性可能变大还是变小」，就不要判断方向。
  **只能抄，不能答。**

## 摘录的「对题」要求（比"真实"更容易违反）

**摘录必须直接回答问题的那个点，不能只是同一段的别的内容。**

反例（摘录真实但与问题无关）：
```
Q: 两套 TIRADS 并存会不会导致同一病人两种结论？
A: 原文提到：「我国甲状腺 FNA 尚没有广泛开展，手术决策往往基于超声表现…」
   ← 这段讲的是「为什么中国医生难接受 ACR-TIRADS」，没回答「会不会导致两种结论」
```

正例：
```
Q: 两套 TIRADS 并存会不会导致同一病人两种结论？
A: 原文提到：「C-TIRADS 与 ACR-TIRADS 对同一结节的分类可能不同，
   例如 1 分结节在 C-TIRADS 为 4A 而在 ACR 为 TR4」
   ← 直接回答了「会不会不同」以及「怎么不同」
```

**自检**：把你摘录的句子单独拿出来读，它是否**恰好回答了问题里问的那件事**？
如果只是「同一篇文档里的相关内容」，就换一句。

## 其他铁律

1. 如果原文只讲技术参数、不含临床决策信息 → `answerable` 写 false，并在 query 里问一个
   "这份原文能回答什么临床问题"的（或直接选更合适的角度）
2. 不要写"根据文献""一般认为"这类模糊表述

只输出 JSON：{"query": "...", "reference_answer": "...", "answerable": true}"""


def _pick_shape(persona: str, seed_key: str) -> str:
    """按 scene 稳定地选一个问法骨架（同一条场景每次结果一致，便于复现）。"""
    import hashlib

    shapes = _ASK_SHAPES.get(persona) or ("直接问这条规则的具体内容。",)
    h = int(hashlib.sha256(seed_key.encode()).hexdigest()[:8], 16)
    return shapes[h % len(shapes)]


def _build_prompt(sc: dict, nodes_content: list[str]) -> str:
    ctx = "\n\n---\n\n".join(c[:1200] for c in nodes_content)
    shape = _pick_shape(sc.get("persona", ""), sc.get("scenario_id", ""))
    return f"""{_SYSTEM}

## 出题要求
- 提问者角色：{sc['persona_role']}
  **必须体现该角色的视角**：
  · 住院医师 → 问「这个征象怎么判、积几分」这类学习性问题
  · 主治医师 → 问「边界情况怎么定、两套体系怎么取舍」这类判断性问题
  · 临床科室 → 问「报告这个分级意味着什么、我下一步做什么」这类处置性问题
  · 医务处质控 → 问「本院该用哪套、两套冲突怎么统一」这类治理性问题
- 出题意图：{sc['intent']}
- 问题类型：{'单跳（一个来源能答）' if sc['hop']=='single' else '多跳（要综合多个来源）'}
- {'具体事实型（问明确的数值/规则）' if sc['specificity']=='specific' else '抽象理解型（问为什么/怎么取舍）'}
- 长度：{_LEN_HINT[sc['length']]}
- **这次请用这种问法**：{shape}
  （不要和别的题目用同一种问法；问法要贴合上面对话里的这个具体角度）
- 器官：{_ORGAN_CN.get(sc['organ'], sc['organ'])}
- **本次只能用这套分级体系：{_ORGAN_SYSTEM_HINT.get(sc['organ'], '（按原文）')}**
  ⚠️ 原文里可能提到别的器官/别的体系（因为是不同文档被检索到一起），
  **你必须只围绕上面指定的器官与体系出题**。
  **如果原文主体讲的是别的器官，就只输出 `{{"skip": true}}`（不要编造问题）。
  绝对不要写「这份原文能否回答XX的问题」这类询问——那不是医生会问的问题。**

## 指南原文
{ctx}

请输出 JSON。"""


async def synth_one(client, sc: dict, node_map: dict, max_tokens: int) -> dict | None:
    from app.inference.protocols import InferenceRequest

    contents = [node_map.get(nid, "") for nid in sc["nodes"]]
    contents = [c for c in contents if c]
    if not contents:
        return None

    prompt = _build_prompt(sc, contents)
    try:
        r = await client.generate(
            InferenceRequest(prompt=prompt, max_tokens=max_tokens, temperature=0.3)
        )
    except Exception as exc:  # silent-ok: 单条合成失败不影响整批
        return {"scenario_id": sc["scenario_id"], "error": f"{type(exc).__name__}: {exc}"}

    text = (r.text or "").strip()
    # 抽 JSON（模型可能包 ```json）
    import re

    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return {"scenario_id": sc["scenario_id"], "error": "no_json", "raw": text[:200]}
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return {"scenario_id": sc["scenario_id"], "error": "bad_json", "raw": text[:200]}

    return {
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
        "finish_reason": r.finish_reason,
    }


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="场景 → query 合成（p120）")
    ap.add_argument("--scenarios", default="/tmp/scenarios.jsonl")
    ap.add_argument("--kg", default="/tmp/kg.json")
    ap.add_argument("--out", default="/tmp/synth.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--offset", type=int, default=0)
    ap.add_argument("--max-tokens", type=int, default=700)
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args(argv)

    scs = [json.loads(l) for l in Path(args.scenarios).read_text(encoding="utf-8").splitlines() if l.strip()]
    scs = scs[args.offset : args.offset + args.limit]
    print(f"待合成 {len(scs)} 个场景")

    # KG 里 node 只有属性，没有正文 → 需要回 DB 拿 content
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    ids = sorted({nid for sc in scs for nid in sc["nodes"]})
    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text("SELECT chunk_id, content FROM knowledge_chunks WHERE chunk_id = ANY(:ids)"),
                {"ids": ids},
            )
        ).fetchall()
    node_map = {cid: (c or "") for cid, c in rows}
    print(f"取回 {len(node_map)}/{len(ids)} 个节点的正文")

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
            r = await synth_one(client, sc, node_map, args.max_tokens)
            done += 1
            if done % 10 == 0:
                print(f"  进度 {done}/{len(scs)}", flush=True)
            return r

    results = await asyncio.gather(*(worker(s) for s in scs))

    # ----------------------------------------------------------------
    # 去重：实测同一批 8 条里出现 3 条「4A 类 13mm 紧邻被膜要不要穿刺」
    # —— 因为多跳关系对反复选到相同的节点组合。
    # 用**字符级 3-gram Jaccard** 判重（中文无需分词），阈值 0.7。
    # ----------------------------------------------------------------
    def _grams(t: str) -> set[str]:
        t = "".join(ch for ch in t if ch.isalnum())
        return {t[i : i + 3] for i in range(max(1, len(t) - 2))}

    def _jaccard(a: str, b: str) -> float:
        ga, gb = _grams(a), _grams(b)
        if not ga or not gb:
            return 0.0
        return len(ga & gb) / len(ga | gb)

    deduped: list = []
    dropped = 0
    seen_q: list[str] = []
    for r in results:
        if r is None or "error" in r:
            deduped.append(r)
            continue
        q = r.get("query") or ""
        if any(_jaccard(q, prev) >= 0.7 for prev in seen_q):
            dropped += 1
            continue
        seen_q.append(q)
        deduped.append(r)
    if dropped:
        print(f"\n去重：丢弃 {dropped} 条高度相似 query（Jaccard ≥0.7）")
    results = deduped

    out = Path(args.out)
    ok = err = 0
    with out.open("w", encoding="utf-8") as f:
        for r in results:
            if r is None:
                continue
            if "error" in r:
                err += 1
            else:
                ok += 1
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\n=== 结果 ===")
    print(f"  成功 {ok} / 失败 {err}")
    if ok:
        srs = [r for r in results if r and "error" not in r]
        from collections import Counter

        print(f"  finish_reason: {dict(Counter(r.get('finish_reason') for r in srs))}")
        print(f"  answerable: {dict(Counter(r.get('answerable') for r in srs))}")
        ql = sorted(len(r["query"]) for r in srs if r.get("query"))
        if ql:
            print(f"  query 长度: min {ql[0]} P50 {ql[len(ql)//2]} max {ql[-1]}")
        al = sorted(len(r["reference_answer"]) for r in srs if r.get("reference_answer"))
        if al:
            print(f"  答案长度: min {al[0]} P50 {al[len(al)//2]} max {al[-1]}")
    print(f"写出 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
