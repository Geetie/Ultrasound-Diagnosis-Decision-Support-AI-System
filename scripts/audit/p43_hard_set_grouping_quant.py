"""A.68 / R3-①：难集「分组粒度」量化（只读）——决定要不要放宽跨文档分组。

**为什么先量**：A.66 造的 crossdoc 难集只有 **13 个分组**（`organ + section_path` 精确相等），
样本太集中，结论容易对少数文档过敏。放宽粒度（按章节层级取前缀）可能造出更多题，
但**也可能把不同亚节混成一组 → gold 变得不唯一**（题目本身就不成立）。

本探针**只统计、不造题**，回答三个数：
1. 不同粒度下有多少「≥2 篇文档」的分组；
2. 每组平均/最多几篇文档（**组太大 → gold 歧义**）；
3. 能构造出多少「A 的问句 → gold = B」的候选对（只有 A 有问句且 B≠A 才算）。

裁决口径（写死）：
* **推荐**某粒度需同时满足：可构造对数 ≥ 当前的 3 倍、且最大组 ≤ 30 篇文档；
* 若最大组 > 30 → 判 `AMBIGUOUS_GOLD_RISK`：题会变成「同章节里有好几篇都可能对」，
  必须人工抽检后才能用（不许直接转正）。

用法（服务器，只需 DB + 对话源）：
    ~/uai/venv/bin/python scripts/audit/p43_hard_set_grouping_quant.py \
        --kb-version kb_v1.3.0 --report ~/uai/reports/hard_set_grouping_quant.json

退出码：0 有结论；2 前置缺失（DB 不可达 / 对话源读不到）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

DIALOGUE_SRC = pathlib.Path.home() / "uai" / "data" / "kb_sources" / "dialogue_docs.jsonl"

#: 粒度定义：prefix_len = 取 section_path 的前几段（0 = 精确整串）
SCHEMES = (
    ("exact", 0, "精确相等（现状口径）"),
    ("prefix2", 2, "前 2 段（对话语料等价于精确；指南会合并亚节）"),
    ("prefix1", 1, "前 1 段（最粗；对话会按「医疗对话」全并到一organ 一组）"),
)

#: 判据（写死，跑完不改）
MIN_QUERY_PAIR_GAIN = 3.0  # 可构造对数至少是现状的 3 倍
MAX_GROUP_DOCS = 30  # 组内文档数上限（超过就 gold 歧义）


def load_docs_with_questions(src: pathlib.Path) -> set[str]:
    """对话源里「有患者提问」的文档 id 集合（只有它们能当 query 来源）。"""
    out: set[str] = set()
    if not src.is_file():
        return out
    parse_failures = 0
    for line in src.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            parse_failures += 1
            continue
        doc = d.get("source_doc_id") or ""
        content = d.get("content") or ""
        if doc and ("患者提问" in content or "[REDACTED]" in content):
            out.add(doc)
    if parse_failures:
        print(f"  （对话源有 {parse_failures} 行解析失败，已跳过）")
    return out


def verdict_of(rows: list[dict]) -> dict:
    """按判据给每种粒度下结论。纯函数（守卫用）。"""
    exact = next((r for r in rows if r["scheme"] == "exact"), None)
    base_pairs = (exact or {}).get("query_pairs") or 0
    out = {}
    for r in rows:
        pairs = r.get("query_pairs") or 0
        gain = (pairs / base_pairs) if base_pairs else 0.0
        max_docs = r.get("max_group_docs") or 0
        if max_docs > MAX_GROUP_DOCS:
            code = "AMBIGUOUS_GOLD_RISK"
            why = f"最大组 {max_docs} 篇 > {MAX_GROUP_DOCS}：同组多篇都可能对，gold 会歧义"
        elif r["scheme"] != "exact" and gain >= MIN_QUERY_PAIR_GAIN:
            code = "RECOMMEND"
            why = f"可构造对数 {gain:.2f}× 现状且最大组 {max_docs} ≤ {MAX_GROUP_DOCS}"
        else:
            code = "NOT_ENOUGH"
            why = f"收益不足（{gain:.2f}× 现状）或粒度未变"
        out[r["scheme"]] = {"code": code, "why": why, "pair_gain_vs_exact": round(gain, 3)}
    return out


async def _run(kb_version: str) -> dict:
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    rows: list[dict] = []
    try:
        for name, prefix_len, note in SCHEMES:
            if prefix_len == 0:
                grouped = await conn.fetch(
                    """
                    SELECT organ, COALESCE(section_path,'') AS grp,
                           count(DISTINCT source_doc_id) AS docs,
                           array_agg(DISTINCT source_doc_id) AS doc_ids
                    FROM knowledge_chunks
                    WHERE kb_version = $1 AND COALESCE(section_path,'') <> ''
                    GROUP BY organ, COALESCE(section_path,'')
                    HAVING count(DISTINCT source_doc_id) >= 2
                    """,
                    kb_version,
                )
            else:
                grouped = await conn.fetch(
                    """
                    SELECT organ,
                           array_to_string((string_to_array(COALESCE(section_path,''), '/'))[1:$2], '/') AS grp,
                           count(DISTINCT source_doc_id) AS docs,
                           array_agg(DISTINCT source_doc_id) AS doc_ids
                    FROM knowledge_chunks
                    WHERE kb_version = $1 AND COALESCE(section_path,'') <> ''
                    GROUP BY organ,
                             array_to_string((string_to_array(COALESCE(section_path,''), '/'))[1:$2], '/')
                    HAVING count(DISTINCT source_doc_id) >= 2
                    """,
                    kb_version,
                    prefix_len,
                )
            rows.append(
                {
                    "scheme": name,
                    "prefix_len": prefix_len,
                    "note": note,
                    "groups": len(grouped),
                    "docs_in_groups": sum(int(g["docs"]) for g in grouped),
                    "max_group_docs": max((int(g["docs"]) for g in grouped), default=0),
                    "avg_group_docs": round(
                        sum(int(g["docs"]) for g in grouped) / len(grouped), 1
                    )
                    if grouped
                    else 0.0,
                    "_groups": [(g["organ"], g["grp"], list(g["doc_ids"] or [])) for g in grouped],
                }
            )
    finally:
        await conn.close()

    # 可构造对数：组内「有问句的文档 A」×「组内其它文档 B」
    with_q = load_docs_with_questions(DIALOGUE_SRC)
    for r in rows:
        pairs = 0
        src_docs = 0
        for _organ, _grp, doc_ids in r["_groups"]:
            askers = [d for d in doc_ids if d in with_q]
            if askers:
                src_docs += len(askers)
                pairs += len(askers) * (len(doc_ids) - 1)
        r["query_pairs"] = pairs
        r["asker_docs"] = src_docs
        r["group_count"] = len(r.pop("_groups"))

    return {
        "probe": "p43_hard_set_grouping_quant",
        "kb_version": kb_version,
        "dialogue_src": str(DIALOGUE_SRC),
        "docs_with_questions": len(with_q),
        "criteria": {
            "min_query_pair_gain": MIN_QUERY_PAIR_GAIN,
            "max_group_docs": MAX_GROUP_DOCS,
        },
        "schemes": rows,
        "verdict": verdict_of(rows),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/hard_set_grouping_quant.json"),
    )
    args = ap.parse_args()

    if not DIALOGUE_SRC.is_file():
        print(f"[FATAL] 对话源不存在：{DIALOGUE_SRC}（没有它无法判断哪些文档能当 query 来源）")
        return 2

    t0 = time.monotonic()
    try:
        payload = asyncio.run(_run(args.kb_version))
    except Exception as exc:
        print(f"[FATAL] 分组量化失败：{type(exc).__name__}: {exc}")
        return 2
    payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["wall_s"] = round(time.monotonic() - t0, 1)

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"对话源里有问句的文档：{payload['docs_with_questions']}")
    print(f"{'粒度':<10}{'分组':>6}{'涉及文档':>8}{'均篇':>6}{'最大篇':>7}{'可构造对':>9}  判定")
    for r in payload["schemes"]:
        v = payload["verdict"][r["scheme"]]
        print(
            f"{r['scheme']:<10}{r['groups']:>6}{r['docs_in_groups']:>8}"
            f"{r['avg_group_docs']:>6}{r['max_group_docs']:>7}{r['query_pairs']:>9}  {v['code']}"
        )
    print(f"报告：{out}  耗时 {payload['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
