"""A.95 Q9 第一步：为知识库回填**跨文档**边（`cross_doc_section`）。

## 为什么需要这个（Q9 的真实阻塞点，本轮查清）

Q9 原名「图谱邻居：换选择器信号（S1 结构 / S2 真重排 / S3 混合）」。
之前登记的原因写的是「等 R3 造出 gold 唯一的难集」——**那只是判定前提，不是能力前提**。

本轮真机核对发现更基础的问题：

| 事实 | 实测 |
|---|---|
| `knowledge_chunks` 有 `metadata.relations` 的块 | **321 / 4526** |
| 其中 `relation = cross_doc_section` | **0** |
| 现有关系类型 | 只有 `next_section` / `prev_section`（**同文档内**） |

也就是说：**跨文档边一条都没有**。A.65/A.66 里"扩边把池子召回从 0.045 提到 0.195"
用的是**运行时按 `(organ, section_path)` 动态构造候选**，不是图里的边；
所以「换邻居选择器」这件事一直缺一个**持久化的边基底**。

## 本脚本做什么

为同一器官、同一 `section_path` 前缀、**不同 `source_doc_id`** 的块建立
`cross_doc_section` 双向边。语义：**"讲同一主题的不同来源文档"** —— 这正是
"跨文档检索"要的那种关系，也是 A.66 动态构造候选时用的同一判据。

## 与 `kb_backfill_relations.py` 的关系（不重复造轮子）

那个脚本只做**同文档相邻块**（`next_section`/`prev_section`），且**不支持**
`--cross-doc-section`（A.67 计划里写了这个开关，但从未实现）。
本脚本补齐这一块，复用同一套 `metadata.relations` 契约与幂等语义。

## ⚠️ 真机踩到的字段位置错误（第一次运行 0 条边）

第一版从 `metadata->>'section_path'` 读 → **1257 个块全部为空** → 0 条边。
真相：`section_path` 是 `knowledge_chunks` 的**独立列**（1257 个块全部非空），
不是 metadata 里的键。

样例（真机）：
```
thyroid_cancer_guideline_2022
九、甲状腺癌多学科综合治疗模式和随访
thyroid_cancer_guideline_2022
```
注意**同一文档的 section_path 有两种形态**：有的块是文档名、有的是章节名。
所以"同 section 前缀"这个判据本身也比预期弱 —— 详见下面 `plan_edges` 的说明。

## 安全约束

* **默认 dry-run**，`--apply` 才写；
* **只追加，不覆盖**同块已有的 relations（幂等：已存在的 target 不重复加）；
* **上限保护**：每块最多加 `--max-per-chunk`（默认 3）个跨文档邻居，
  避免"同 section 有 200 篇"时写出 200 条边（那会让图查询爆）；
* **不改向量**（只动 metadata JSONB），无需重灌；
* 出边数量、跳过的块数都要打印（零静默）。

## 用法

    # 1) 盘点（只读）
    ~/uai/venv/bin/python scripts/audit/p70_cross_doc_edges.py --kb-version kb_v1.3.0

    # 2) 真写
    ... --apply

    # 3) 复核
    ... --verify
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import defaultdict

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

#: 每块最多加多少跨文档邻居（防止同 section 很大时边爆炸）
DEFAULT_MAX_PER_CHUNK = 3


async def _load_chunks(kb_version: str) -> list[dict]:
    from sqlalchemy import text

    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT chunk_id, source_doc_id, organ,
                           COALESCE(section_path, '') AS section_path,
                           metadata
                    FROM knowledge_chunks
                    WHERE kb_version = :v
                    ORDER BY source_doc_id, chunk_id
                    """
                ),
                {"v": kb_version},
            )
        ).all()
    return [
        {
            "chunk_id": str(r[0]),
            "source_doc_id": str(r[1]),
            "organ": str(r[2] or ""),
            "section_path": str(r[3] or ""),
            "metadata": dict(r[4]) if r[4] else {},
        }
        for r in rows
    ]


def _section_prefix(section_path: str, depth: int = 1) -> str:
    """取 section_path 的前 N 段作为"同主题"判据。

    为什么不用完整 section_path：A.68 已实测**完整路径分组太细**
    （`section_path` 只有 8 个取值 = 全是指南名，组内 2~5 篇的小组 = 0）。
    取前缀 1 段既保留"同主题"，又不会细到无邻居。
    """
    if not section_path:
        return ""
    parts = [p for p in section_path.replace("\\", "/").split("/") if p]
    return "/".join(parts[:depth])


def plan_edges(
    chunks: list[dict], *, depth: int = 1, max_per_chunk: int = DEFAULT_MAX_PER_CHUNK
) -> dict:
    """计算要加哪些跨文档边。**纯函数**（守卫钉住）。

    规则：同 `organ` + 同 `section_prefix` + **不同 source_doc_id** → 互为邻居。
    每块最多取 `max_per_chunk` 个（按 chunk_id 稳定排序，保证可复现）。

    Returns:
        ``{"edges": {chunk_id: [target, ...]}, "stats": {...}}``
    """
    # 按 (organ, prefix) 分桶
    buckets: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in chunks:
        prefix = _section_prefix(c["section_path"], depth)
        if not prefix:
            continue  # 无 section_path 的块不参与（无法判断主题）
        buckets[(c["organ"], prefix)].append(c)

    edges: dict[str, list[str]] = {}
    stat = {
        "chunks_total": len(chunks),
        "chunks_with_prefix": sum(len(v) for v in buckets.values()),
        "chunks_without_prefix": len(chunks) - sum(len(v) for v in buckets.values()),
        "buckets": len(buckets),
        "buckets_single_doc": 0,
        "edges_added": 0,
        "chunks_covered": 0,
    }

    for (_organ, _prefix), members in buckets.items():
        docs = {m["source_doc_id"] for m in members}
        if len(docs) < 2:
            # 这个桶只有一篇文档 → 造不出跨文档边
            stat["buckets_single_doc"] += 1
            continue
        for m in members:
            # 候选：同桶内**不同文档**的块，按 chunk_id 排序取前 N
            cands = sorted(
                x["chunk_id"] for x in members if x["source_doc_id"] != m["source_doc_id"]
            )
            if not cands:
                continue
            picked = cands[:max_per_chunk]
            edges[m["chunk_id"]] = picked
            stat["edges_added"] += len(picked)
            stat["chunks_covered"] += 1

    return {"edges": edges, "stats": stat}


def _merge_relations(existing: list, new_targets: list[str]) -> tuple[list, int]:
    """把新 target 并进已有 relations（**幂等**，不覆盖）。

    Returns:
        ``(merged_relations, added_count)``
    """
    cur = [r for r in (existing or []) if isinstance(r, dict)]
    have = {r.get("target") for r in cur}
    added = 0
    for t in new_targets:
        if t in have:
            continue
        cur.append({"target": t, "relation": "cross_doc_section"})
        have.add(t)
        added += 1
    return cur, added


async def apply_edges(kb_version: str, edges: dict[str, list[str]], chunks: list[dict]) -> dict:
    from sqlalchemy import text

    from app.config.database import AsyncSessionLocal

    by_id = {c["chunk_id"]: c for c in chunks}
    stat = {"updated_chunks": 0, "relations_added": 0, "skipped_no_change": 0}
    async with AsyncSessionLocal() as db:
        for cid, targets in edges.items():
            meta = dict(by_id[cid]["metadata"])
            merged, added = _merge_relations(meta.get("relations"), targets)
            if added == 0:
                stat["skipped_no_change"] += 1
                continue
            meta["relations"] = merged
            await db.execute(
                text(
                    "UPDATE knowledge_chunks SET metadata = CAST(:m AS jsonb) "
                    "WHERE chunk_id = :c AND kb_version = :v"
                ),
                {"m": json.dumps(meta, ensure_ascii=False), "c": cid, "v": kb_version},
            )
            stat["updated_chunks"] += 1
            stat["relations_added"] += added
        await db.commit()
    return stat


async def verify(kb_version: str) -> int:
    from sqlalchemy import text

    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        r = (
            await db.execute(
                text(
                    """
                    SELECT
                      count(*) AS total,
                      count(*) FILTER (
                        WHERE jsonb_typeof(metadata->'relations') = 'array'
                      ) AS has_rel
                    FROM knowledge_chunks WHERE kb_version = :v
                    """
                ),
                {"v": kb_version},
            )
        ).first()
        r2 = (
            await db.execute(
                text(
                    """
                    SELECT count(*) FROM knowledge_chunks kc
                    CROSS JOIN LATERAL jsonb_array_elements(
                      CASE WHEN jsonb_typeof(kc.metadata->'relations') = 'array'
                           THEN kc.metadata->'relations' ELSE '[]'::jsonb END
                    ) AS rel
                    WHERE kc.kb_version = :v
                      AND rel->>'relation' = 'cross_doc_section'
                    """
                ),
                {"v": kb_version},
            )
        ).scalar()
    print(f"  知识块 {r[0]} / 有 relations {r[1]} / cross_doc_section 边 **{r2}**")
    return 0 if r2 > 0 else 1


async def main(
    kb_version: str, apply: bool, depth: int, max_per_chunk: int, do_verify: bool
) -> int:
    if do_verify:
        return await verify(kb_version)

    chunks = await _load_chunks(kb_version)
    print(f"载入 {len(chunks)} 个知识块（kb={kb_version}）")
    if not chunks:
        print("[FATAL] 没有知识块")
        return 2

    plan = plan_edges(chunks, depth=depth, max_per_chunk=max_per_chunk)
    st = plan["stats"]
    print("\n=== 建边计划 ===")
    print(f"  分桶（organ + section 前 {depth} 段）: {st['buckets']}")
    print(f"  仅单文档、造不出跨文档边的桶: {st['buckets_single_doc']}")
    print(f"  无 section_path 的块（不参与）: {st['chunks_without_prefix']}")
    print(f"  将覆盖块数: {st['chunks_covered']}")
    print(f"  将新增边数: {st['edges_added']}")

    if not apply:
        print("\n[dry-run] 未写任何数据。加 --apply 真正建边。")
        return 0

    res = await apply_edges(kb_version, plan["edges"], chunks)
    print(
        f"\n[APPLIED] 更新 {res['updated_chunks']} 块 / 新增 {res['relations_added']} 条边 "
        f"/ 无变化跳过 {res['skipped_no_change']}"
    )
    return await verify(kb_version)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    ap.add_argument("--depth", type=int, default=1, help="section_path 取前几段")
    ap.add_argument("--max-per-chunk", type=int, default=DEFAULT_MAX_PER_CHUNK)
    ap.add_argument("--verify", action="store_true", help="只复核边数量")
    a = ap.parse_args()
    sys.exit(
        asyncio.run(
            main(a.kb_version, a.apply, a.depth, a.max_per_chunk, a.verify)
        )
    )
