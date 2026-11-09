#!/usr/bin/env python3
"""合成数据质量门（p121）：不靠模型自觉，靠程序判据。

## 为什么必须有这把闸

实测：即使提示词明确写「禁止编造文献名称与年份」，II-Medical-8B 仍然输出：
- `依据《C-TIRADS 2018》` ← 真实是 2021
- `根据《LI-RADS 2017》` ← 用在了甲状腺问题上
- `根据《Hürthle 细胞肿瘤》` ← 编的

**8B 模型约束不住。必须程序校验。**

## 五道闸

| 闸 | 判据 | 抓什么问题 |
|---|---|---|
| 1 引文真实性 | 答案里的 `《...》` 必须能在 KB 里找到同名字符串 | 编造文献名 |
| 2 体系一致性 | 甲状腺问题不许出现 LI-RADS；肝脏不许出现 C-TIRADS | 跨体系混用 |
| 3 问句形态 | query 必须含问号或疑问词，且 ≤80 字 | 把原文复述当 query |
| 4 答案信息量 | reference_answer ≥40 字，且不能整段抄 query | 空洞答案 |
| 5 gold 一致性 | gold 的 chunk_id 必须存在，且非空 | 标错位置 |

## 用法

```bash
PYTHONPATH=~/uai/src ~/uai/venv/bin/python scripts/audit/p121_synth_quality_gate.py \
    --dataset /tmp/synth.jsonl --kb-version kb_v2.1.0
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

_CITE_RE = re.compile(r"《([^》]{2,40})》")
_ASK_RE = re.compile(r"[？?]|要不要|是否需要|怎么|如何|为什么|什么|多少|是否|能否|该不该|下一步")

#: 器官 → 允许出现的分级体系；出现其它体系 = 跨体系混用
_ORGAN_SYSTEMS = {
    "thyroid": ("C-TIRADS", "CTIRADS", "TI-RADS", "TIRADS", "TR1", "TR2", "TR3", "TR4", "TR5"),
    "liver": ("LI-RADS", "LIRADS", "LR-", "CEUS", "OPTN"),
    "cardiac": ("LVEF", "EF", "E/e", "GLS", "ASE", "BSE"),
}
#: 明确不该出现的（跨器官体系）
_FORBIDDEN = {
    "thyroid": ("LI-RADS", "LIRADS", "LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M"),
    "liver": ("C-TIRADS", "CTIRADS", "TI-RADS"),
    "cardiac": ("C-TIRADS", "LI-RADS", "TI-RADS"),
}

MIN_ANSWER = 40
MAX_QUERY = 80


async def load_kb_texts(kb_version: str) -> str:
    """把 KB 全文拼起来，用于查「引文里的书名是否真实存在」。"""
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        rows = (
            await s.execute(
                text("SELECT chunk_id, content FROM knowledge_chunks WHERE kb_version = :v"),
                {"v": kb_version},
            )
        ).fetchall()
    return "\n".join((c or "") for _cid, c in rows)


def check_one(row: dict, kb_text: str, existing_ids: set[str]) -> list[str]:
    """返回问题列表（空 = 通过）。"""
    probs: list[str] = []
    organ = row.get("organ") or ""
    q = (row.get("query") or "").strip()
    a = (row.get("reference_answer") or "").strip()

    # ---- 闸 3：问句形态
    if not q:
        probs.append("query 为空")
    else:
        if not _ASK_RE.search(q):
            probs.append("query 不含疑问词（像陈述而非提问）")
        if len(q) > MAX_QUERY:
            probs.append(f"query 过长 {len(q)}>{MAX_QUERY}（像复述原文）")

    # ---- 闸 4：答案信息量
    if len(a) < MIN_ANSWER:
        probs.append(f"答案过短 {len(a)}<{MIN_ANSWER}")
    if q and a and q in a:
        probs.append("答案整段抄了 query")

    # ---- 闸 1：引文真实性（《X》必须在 KB 里出现过）
    for m in _CITE_RE.finditer(a + " " + q):
        name = m.group(1).strip()
        # 允许的形式：KB 里含该名字，或含其去掉空格/连字符的变体
        variants = {name, name.replace(" ", ""), name.replace("-", ""), name.upper(), name.lower()}
        if not any(v and v in kb_text for v in variants):
            probs.append(f"编造文献名《{name}》（KB 里找不到）")
            break   # 一条就够，不必刷屏

    # ---- 闸 2：体系一致性
    text = q + " " + a
    for bad in _FORBIDDEN.get(organ, ()):
        if bad.lower() in text.lower():
            probs.append(f"{organ} 问题出现「{bad}」（跨体系混用）")
            break

    # ---- 闸 6（2026-11-09 新增）：query 里的体系码必须与器官匹配
    #
    # 实测漏网案例：
    #   [liver] 「报告写 TR4，我作为外科医生该不该安排手术？」← TR4 是甲状腺的码
    #   [liver] 「肝脏 13mm 4A 类结节紧邻被膜，要不要穿刺？」← 4A 是甲状腺的码
    #
    # 原闸 2 只查 `_FORBIDDEN` 里的**体系名**（C-TIRADS / LI-RADS），
    # 没查**裸码**（TR4 / 4A / LR-5）。而模型恰恰爱写裸码。
    _BARE_CODE_OWNER = {
        "thyroid": ("TR1", "TR2", "TR3", "TR4", "TR5", "4A", "4B", "4C"),
        "liver": ("LR-1", "LR-2", "LR-3", "LR-4", "LR-5", "LR-M", "LR-TIV"),
        "cardiac": (),
    }
    q_upper = (q or "").upper()
    for other_organ, codes in _BARE_CODE_OWNER.items():
        if other_organ == organ:
            continue
        for code in codes:
            # 只匹配「独立出现的码」，避免 4A 命中 LABEL 之类
            if re.search(rf"(?<![A-Z0-9]){re.escape(code)}(?![0-9A-Za-z])", q_upper):
                probs.append(
                    f"{organ} 问题的 query 里出现「{code}」（属于 {other_organ} 的码）"
                )
                break
        else:
            continue
        break

    # ---- 闸 7（2026-11-09 新增）：答案里不许有「原文没有的具体数值」
    #
    # 实测漏网案例：「C-TIRADS 3 类…一般2~3个月复检一次彩超」
    # —— 原文说的是「随访间隔 6~24 个月」，2~3 个月是编的。
    #
    # 判据：答案里出现的**带单位数值**，若在原文（KB 全文）里找不到，
    # 且不是常见的通用值（如 1mm 这类），就报警。
    # 这是启发式：可能误伤"合理换算"（如 1cm 写成 10mm），所以只作 WARN 级。
    _num_re = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)")
    invented: list[str] = []
    for m in _num_re.finditer(a):
        tok = m.group(0).replace(" ", "")
        # 允许等效写法：1cm ≈ 10mm
        variants = {tok, tok.replace("cm", "mm"), tok.replace("mm", "cm")}
        if not any(v in kb_text for v in variants):
            invented.append(tok)
    if invented:
        probs.append(f"答案含原文没有的数值: {invented[:3]}")

    # ---- 闸 8（2026-11-09 新增）：query 必须含具体锚点
    #
    # 实测漏网：「边界情况：指南在这条上有没有例外或有争议的地方？」
    # 「什么情况下这条不适用」「报告这个分级意味着什么、严不严重？」
    # —— **没说是哪个分级、哪条规则**。医生不会这样问，因为它不含任何可检索的信息。
    #
    # 这类 query 的根因：模型把「问法骨架」直接当成了问题，没填入原文内容。
    _ANCHOR_RE = (
        re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-C]?\s*类)", re.I),
        re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)", re.I),
        re.compile(r"甲状腺|肝|心脏|左室|右室|二尖瓣|三尖瓣|主动脉瓣|被膜|结节|门静脉|室间隔|瓣环"),
    )
    if q and not any(p.search(q) for p in _ANCHOR_RE):
        probs.append("query 无具体锚点（没说是哪个分级/数值/部位，医生不会这样问）")

    # ---- 闸 9（2026-11-09 新增）：书名号清理留下的残句
    #
    # 实测漏网：「如果医生按原文提到：的分级标准，质控该如何处理？」
    # —— 我的 _strip_fake_citations 把「按《LI-RADS 2017》」清成了「按原文提到：」，
    # 后面直接接「的分级标准」，句子碎了。
    #
    # 判据：`原文提到：` 后面紧跟「的/是/中」这类需要名词的助词，或直接接标点。
    if re.search(r"原文提到[：:]\s*(?:的|是|中|里|[，。；、])", q or ""):
        probs.append("书名号清理后语句破碎（「原文提到：的分级标准」）")
    if re.search(r"原文提到[：:]\s*(?:的|是|中|里|[，。；、])", a or ""):
        probs.append("答案书名号清理后语句破碎")

    # ---- 闸 10（2026-11-09 L3 审核后新增）：query 与 answer 的参数必须对齐
    #
    # L3 人工审核 6 条里 5 条不合格，最典型的两类：
    #   l3-001: query「报告这个分级意味着什么」没提 LR-3，答案却自己说「分级为 LR-3」
    #           → 答案在回答一个 query 里没有的问题
    #   l3-006: query「大小 1.2cm，直径小于 1.0cm」自相矛盾，答案只答了 <1.0cm
    #
    # 判据：query 里出现的数值/分级码，必须在答案里出现**同值的**（反之亦然不要强求，
    # 因为答案可能需要引用 query 未提的其他数据）。
    _NUM_TOK = re.compile(r"\d+(?:\.\d+)?\s*(?:mm|cm|%|个月|周|岁)", re.I)
    _GRADE_TOK = re.compile(r"(?:C-?TIRADS|TI-?RADS|LI-?RADS|CEUS)?\s*(?:LR-?[1-5M]|TR\s?[1-5]|[1-6]\s*[A-C]?\s*类)", re.I)

    def _norm_tok(t: str) -> str:
        return t.replace(" ", "").lower()

    q_nums = {_norm_tok(m.group(0)) for m in _NUM_TOK.finditer(q or "")}
    a_nums = {_norm_tok(m.group(0)) for m in _NUM_TOK.finditer(a or "")}
    # 允许等效换算：1.2cm == 12mm
    def _to_mm(tok: str) -> float | None:
        m = re.match(r"(\d+(?:\.\d+)?)(mm|cm)", tok)
        if not m:
            return None
        v = float(m.group(1))
        return v * 10 if m.group(2) == "cm" else v

    a_mm = {_to_mm(t) for t in a_nums} - {None}
    missing = []
    for t in q_nums:
        if t in a_nums:
            continue
        v = _to_mm(t)
        if v is not None and any(x is not None and abs(x - v) < 0.01 for x in a_mm):
            continue
        missing.append(t)
    if missing:
        probs.append(f"query 提到的数值 {missing[:3]} 未出现在答案里（答非所问）")

    # ---- 闸 11（2026-11-09 L3 审核后新增）：跨体系码不许互换
    #
    # L3 实测 l3-005：答案写「4A 类…根据 TR4 级」，把 C-TIRADS 的 4A 说成 ACR 的 TR4。
    # **这两者差一档**（4A 低度可疑 2-10% / TR4 中度可疑），是项目中已记录的口径陷阱。
    #
    # 判据：同一答案里若同时出现 C-TIRADS 系（4A/4B/4C）与 ACR 系（TR1-TR5），
    # 且它们是**在同一句里对同一次判读**，就是混用。
    _HAS_CT = re.search(r"[1-6]\s*[A-C]\s*类|C-?TIRADS", a or "", re.I)
    _HAS_TR = re.search(r"\bTR\s?[1-5]\b", a or "", re.I)
    if _HAS_CT and _HAS_TR:
        # 只有当两套码出现在相邻 40 字以内才算"同一次判读混用"
        text_a = a or ""
        for mCt in re.finditer(r"[1-6]\s*[A-C]\s*类|C-?TIRADS", text_a, re.I):
            for mTr in re.finditer(r"\bTR\s?[1-5]\b", text_a, re.I):
                if abs(mCt.start() - mTr.start()) <= 40:
                    probs.append("同一句里混用 C-TIRADS 的 4A/4B/4C 与 ACR 的 TRx（差一档）")
                    break
            else:
                continue
            break

    # ---- 闸 12（2026-11-09 L3 审核后新增）：答案不许夹外文原文 + 清理残留
    #
    # L3 实测 l3-002：答案里夹着 "Pediatric FTC is a rare malignancy."
    # L3 实测 l3-006：答案出现「原文提到：和原文提到：」（我的清理重复）
    _EN_SPAN = re.search(r"[A-Za-z][A-Za-z\s,.'\-]{25,}", a or "")
    if _EN_SPAN:
        probs.append(f"答案里夹了整段外文原文: {_EN_SPAN.group(0)[:40]!r}")
    if re.search(r"原文提到[：:]\s*和\s*原文提到", a or "") or (a or "").count("原文提到") >= 2:
        probs.append("答案里「原文提到：」重复（清理残留）")

    # ---- 闸 5：gold 一致性
    gold = row.get("gold") or []
    if not gold:
        probs.append("gold 为空")
    else:
        for g in gold:
            cid = g.get("chunk_id") or ""
            if cid and cid not in existing_ids:
                probs.append(f"gold chunk 不存在: {cid[:50]}")
                break

    return probs


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="合成数据质量门（p121）")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--kb-version", default="kb_v2.1.0")
    ap.add_argument("--limit", type=int, default=20, help="展示前 N 个问题项")
    args = ap.parse_args(argv)

    rows = [
        json.loads(l)
        for l in Path(args.dataset).read_text(encoding="utf-8").splitlines()
        if l.strip()
    ]
    # **只检已过闸的条目**（2026-11-09 实测修正）
    #
    # 原来只滤 `error`，但 p122 的输出里有 `rejected: true` 的条目 ——
    # 它们本来就没有 query/answer（结构不同：只有 scenario_id + reason），
    # 拿它们进闸会报「query 为空 / 答案过短 / gold 为空」，
    # 把真实的闸通过率稀释了（实测 44 条里 17 条是被拒条目，
    # 导致"34.1% 不合规"这个数字毫无意义）。
    #
    # 要检「已过闸的条目质量」，就该只看 `gate_passed: true` 的。
    _before = len(rows)
    rows = [r for r in rows if "error" not in r and not r.get("rejected")]
    _skipped = _before - len(rows)
    if _skipped:
        print(f"（跳过 {_skipped} 条被拒/出错条目，只检已过闸的）")

    kb_text = await load_kb_texts(args.kb_version)
    print(f"数据集 {len(rows)} 条；KB 全文 {len(kb_text)} 字")

    # gold chunk id 集合（用于闸 5）
    from app.config.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as s:
        ids = {
            r[0]
            for r in (
                await s.execute(
                    text("SELECT chunk_id FROM knowledge_chunks WHERE kb_version = :v"),
                    {"v": args.kb_version},
                )
            ).fetchall()
        }

    gates = {"闸1 引文真实": 0, "闸2 体系一致": 0, "闸3 问句形态": 0, "闸4 答案信息量": 0, "闸5 gold一致": 0}
    bad_rows: list[tuple[str, list[str]]] = []
    for r in rows:
        probs = check_one(r, kb_text, ids)
        if probs:
            bad_rows.append((r.get("scenario_id") or r.get("query", "")[:20], probs))
            for p in probs:
                if "编造文献名" in p:
                    gates["闸1 引文真实"] += 1
                elif "跨体系" in p:
                    gates["闸2 体系一致"] += 1
                elif "query" in p:
                    gates["闸3 问句形态"] += 1
                elif "答案" in p:
                    gates["闸4 答案信息量"] += 1
                elif "gold" in p:
                    gates["闸5 gold一致"] += 1

    print(f"\n=== 五道闸 ===")
    for k, v in gates.items():
        print(f"  {k}: {v} 条不合规")
    n_bad = len(bad_rows)
    print(f"\n总不合规条目: {n_bad}/{len(rows)} = {n_bad/max(len(rows),1)*100:.1f}%")

    for sid, probs in bad_rows[: args.limit]:
        print(f"\n  {sid}:")
        for p in probs:
            print(f"    - {p}")
    if n_bad > args.limit:
        print(f"\n  … 还有 {n_bad - args.limit} 条")

    return 0 if n_bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
