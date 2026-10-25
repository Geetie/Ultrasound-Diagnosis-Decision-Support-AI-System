"""A.95 Q9：图谱**跨文档邻居**选择器三臂离线对照（S1 结构 / S2 真重排 / S3 混合）。

## 前提（本轮已完成）

A.95 已用 `p70_cross_doc_edges.py` 把 **3672 条 `cross_doc_section` 边**写进
`knowledge_chunks.metadata.relations`（覆盖 1224/1257 块，全部真跨文档）。
此前是 **0 条** —— 这就是 Q9 一直没做的真实原因（不是"等难集"，是**没有边**）。

## 这条要验证什么

A.66 的结论是：「扩边把池子召回从 0.045 提到 0.195（+15pp，有用），
但按 query 余弦取 5 个只有 0.015 ≈ 任取 0.0192 → **NO_GO**」。
即：**问题不在扩边，在"怎么从那 5 个邻居里挑"**。

Q9 要做的就是换选择器信号，三臂对照：
* **S1 结构**：按 `relation` 类型 + 邻居的 `section_path` 与 query 词的**字面重合度**排
* **S2 真重排**：把候选送 bge-reranker(8005) 真打分（不是 embedding 余弦）
* **S3 混合**：S1 与 S2 归一化后加权

## 判据（沿用 p42 的 P0/P1/P2，**不另立**）

* `P0` 扩边必须真带来召回增益：`pool_after >= pool_before + 0.02`
* `P1` 排序后不退化：`sorted + 0.005 >= baseline`
* `P2` 排序显著优于任取：`sorted >= random + 0.05`
三态：`GO` / `NO_GO_NO_RECALL_GAIN` / `NO_GO` / `INSTRUMENT_NOT_READY`

## 关于难集（如实说明）

A.68 已证明**在现有语料上造不出 gold 唯一的检索难集**（分组粒度太粗）。
所以本脚本**不假装有难集**：
* 若没有难集 → 只做**结构性对照**（用现有 golden 的 136 条），
  并在报告里标 `instrument_note = "saturated_golden"`，结论只作方向参考；
* 若提供了 `--hard-set`（`p41` 产物或人工标注）→ 走完整判据。

## 用法

    ~/uai/venv/bin/python scripts/audit/p71_crossdoc_selector_ab.py
    ~/uai/venv/bin/python scripts/audit/p71_crossdoc_selector_ab.py \
        --hard-set ~/uai/data/eval/hard_query_set_crossdoc.json

退出码：0 有结论 ｜ 2 前置缺失。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import re
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN = pathlib.Path.home() / "uai" / "data" / "golden" / "kb_qa_golden_v1.json"
DEFAULT_KB_VERSION = "kb_v1.0.0"  # golden 用的是这个版本
RERANK_URL = "http://127.0.0.1:8005"

#: 判据（沿用 p42，不另立）
P0_MIN_POOL_GAIN = 0.02
P1_TOLERANCE = 0.005
P2_MIN_LIFT = 0.05
#: 每臂选几个邻居
TOP_K_NEIGHBORS = 5
#: 随机臂重复次数（取均值，降低抖动）
RANDOM_REPEATS = 20


async def _load_graph_neighbors(kb_version: str) -> dict[str, list[dict]]:
    """读 `cross_doc_section` 边 → {chunk_id: [{target, section_path, doc}]}。"""
    from sqlalchemy import text

    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT a.chunk_id,
                           rel->>'target' AS target,
                           b.section_path AS target_section,
                           b.source_doc_id AS target_doc,
                           b.content AS target_content
                    FROM knowledge_chunks a
                    CROSS JOIN LATERAL jsonb_array_elements(
                      CASE WHEN jsonb_typeof(a.metadata->'relations') = 'array'
                           THEN a.metadata->'relations' ELSE '[]'::jsonb END
                    ) AS rel
                    JOIN knowledge_chunks b ON b.chunk_id = rel->>'target'
                    WHERE a.kb_version = :v AND rel->>'relation' = 'cross_doc_section'
                    """
                ),
                {"v": kb_version},
            )
        ).all()
    out: dict[str, list[dict]] = {}
    for src, tgt, sec, doc, content in rows:
        out.setdefault(str(src), []).append(
            {
                "target": str(tgt),
                "section_path": str(sec or ""),
                "doc": str(doc or ""),
                "content": str(content or ""),
            }
        )
    return out


def _query_terms(text: str) -> set[str]:
    """极简中文词集：2-gram（与仓库 BM25 的两字窗口口径一致，不引 jieba）。"""
    t = re.sub(r"[^\u4e00-\u9fa5A-Za-z0-9]", "", text or "")
    if len(t) < 2:
        return {t} if t else set()
    return {t[i : i + 2] for i in range(len(t) - 1)}


def s1_structural(query: str, cands: list[dict], k: int = TOP_K_NEIGHBORS) -> list[str]:
    """S1：**结构 + 字面重合**（不调模型）。

    打分 = 2-gram 与候选 content 的重合数 / 候选 2-gram 数
          + 0.3 × (section_path 与 query 有交集)  —— 结构信号
    纯函数（守卫钉住）。
    """
    q = _query_terms(query)
    if not q:
        return [c["target"] for c in cands[:k]]
    scored = []
    for c in cands:
        ct = _query_terms(c["content"])
        overlap = len(q & ct) / max(len(ct), 1)
        sec_bonus = 0.3 if (q & _query_terms(c["section_path"])) else 0.0
        scored.append((overlap + sec_bonus, c["target"]))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [t for _, t in scored[:k]]


async def s2_rerank(query: str, cands: list[dict], k: int = TOP_K_NEIGHBORS) -> list[str]:
    """S2：**真重排**（bge-reranker 8005），不是 embedding 余弦。

    失败时**显式降级**到 S1 并留痕（不许静默返回空）。
    """
    if not cands:
        return []
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(
                f"{RERANK_URL}/v1/rerank",
                json={
                    "query": query,
                    "documents": [c["content"] for c in cands],
                    "top_k": min(k, len(cands)),
                },
            )
            r.raise_for_status()
            data = r.json()
        items = data.get("results") or data.get("data") or []
        picked = []
        for it in items[:k]:
            idx = it.get("index")
            if isinstance(idx, int) and 0 <= idx < len(cands):
                picked.append(cands[idx]["target"])
        if picked:
            return picked
        print("    [warn] reranker 返回空，降级 S1")
        return s1_structural(query, cands, k)
    except Exception as exc:
        print(f"    [warn] reranker 不可用（{type(exc).__name__}），降级 S1")
        return s1_structural(query, cands, k)


def s3_hybrid(
    query: str, cands: list[dict], rerank_order: list[str], k: int = TOP_K_NEIGHBORS
) -> list[str]:
    """S3：S1 与 S2 的归一化加权（结构分 0.4 + 重排位次 0.6）。"""
    if not cands:
        return []
    s1_order = s1_structural(query, cands, k=len(cands))
    s1_rank = {t: i for i, t in enumerate(s1_order)}
    s2_rank = {t: i for i, t in enumerate(rerank_order)}
    n = max(len(cands), 1)

    def score(t: str) -> float:
        a = 1.0 - (s1_rank.get(t, n) / n)
        b = 1.0 - (s2_rank.get(t, n) / n)
        return 0.4 * a + 0.6 * b

    ranked = sorted({c["target"] for c in cands}, key=lambda t: (-score(t), t))
    return ranked[:k]


def _verdict(pool_before: float, pool_after: float, arms: dict[str, float], random_hit: float) -> tuple[str, dict]:
    """按 P0/P1/P2 裁决（纯函数，守卫钉住）。"""
    best_arm = max(arms, key=lambda k: arms[k]) if arms else None
    best_hit = arms.get(best_arm, 0.0) if best_arm else 0.0
    p0 = pool_after >= pool_before + P0_MIN_POOL_GAIN
    p1 = best_hit + P1_TOLERANCE >= pool_before
    p2 = best_hit >= random_hit + P2_MIN_LIFT

    facts = {
        "pool_recall_before": round(pool_before, 4),
        "pool_recall_after": round(pool_after, 4),
        "pool_gain": round(pool_after - pool_before, 4),
        "arm_hit_rates": {k: round(v, 4) for k, v in arms.items()},
        "best_arm": best_arm,
        "best_hit": round(best_hit, 4),
        "random_baseline": round(random_hit, 4),
        "P0_pool_gain": p0,
        "P1_no_regress": p1,
        "P2_beats_random": p2,
        "thresholds": {
            "P0_MIN_POOL_GAIN": P0_MIN_POOL_GAIN,
            "P1_TOLERANCE": P1_TOLERANCE,
            "P2_MIN_LIFT": P2_MIN_LIFT,
            "RANDOM_REPEATS": RANDOM_REPEATS,
        },
    }
    if not p0:
        return "NO_GO_NO_RECALL_GAIN", facts
    if p2:
        return "GO", facts
    return "NO_GO", facts


async def main(hard_set: str | None, report: str | None, kb_version: str = DEFAULT_KB_VERSION) -> int:
    # ---- 前置：必须有 cross_doc 边 ----
    neighbors = await _load_graph_neighbors(kb_version)
    if not neighbors:
        print("[FATAL] 没有任何 cross_doc_section 边 —— 先跑 p70_cross_doc_edges --apply")
        return 2
    total_edges = sum(len(v) for v in neighbors.values())
    print(f"载入 {len(neighbors)} 个源块 / {total_edges} 条 cross_doc 边")

    # ---- 取 query 集 ----
    instrument_note = None
    if hard_set:
        hs = json.loads(pathlib.Path(hard_set).expanduser().read_text(encoding="utf-8"))
        queries = hs.get("queries") or hs if isinstance(hs, list) else hs.get("queries", [])
        instrument_note = "hard_set_provided"
    else:
        data = json.loads(GOLDEN.read_text(encoding="utf-8"))
        queries = data.get("queries") or []
        instrument_note = (
            "saturated_golden（A.68 已证明现有语料造不出 gold 唯一难集；"
            "本结论只作方向参考，非判定性）"
        )
    if not queries:
        print("[FATAL] 无 query")
        return 2
    print(f"query 数: {len(queries)}  (instrument: {instrument_note})")

    # golden 真实字段（真机核对）：relevant_doc_id = gold chunk_id，
    # relevant_source_doc_id = gold 文档。不要臆测字段名 —— 我第一次用
    # source_chunk_id/gold_source_doc_id 全是 None → 可用样本 0。
    gold_of: dict[str, str] = {}
    q_of: dict[str, str] = {}
    for q in queries:
        cid = q.get("relevant_doc_id") or q.get("source_chunk_id") or q.get("chunk_id")
        gold = (
            q.get("relevant_source_doc_id")
            or q.get("gold_source_doc_id")
            or q.get("source_doc_id")
        )
        if cid and gold:
            gold_of[str(cid)] = str(gold)
            q_of[str(cid)] = q.get("query_text") or ""

    # 只保留**有邻居且有 gold** 的块
    usable = [c for c in neighbors if c in gold_of]
    print(f"可用样本（有邻居且有 gold）: {len(usable)}")
    if len(usable) < 5:
        print("[FATAL] 可用样本太少（<5），无法做对照")
        return 2

    # 邻居覆盖的文档集合（用于判命中：gold 文档是否在邻居里）
    neighbor_docs = {c: {x["doc"] for x in neighbors[c]} for c in usable}

    # pool_recall_before：不扩边时，gold 文档是否已在源块自身文档里
    # （严格说扩边前没有任何跨文档候选 → 命中只看源块自身文档）
    from sqlalchemy import text as _text

    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                _text(
                    "SELECT chunk_id, source_doc_id FROM knowledge_chunks WHERE kb_version = :v"
                ),
                {"v": kb_version},
            )
        ).all()
    self_doc = {str(r[0]): str(r[1]) for r in rows}

    # A.95 修正：**扩边是「原池 ∪ 邻居」**，不是「用邻居替换原池」。
    # 第一版把 after 写成"gold 是否在邻居里" → 池召回 1.0→0.0，
    # 因为原块自身的文档被排除了。扩边的语义是**追加**候选，
    # 所以 after = (gold 在原文档) OR (gold 在邻居文档里)。
    before_hits = sum(1 for c in usable if self_doc.get(c) == gold_of[c])
    pool_before = before_hits / len(usable)
    after_hits = sum(
        1
        for c in usable
        if self_doc.get(c) == gold_of[c] or gold_of[c] in neighbor_docs[c]
    )
    pool_after = after_hits / len(usable)
    # 纯「邻居带来的新增召回」（不含原文档命中）
    neighbor_only_hits = sum(
        1 for c in usable if self_doc.get(c) != gold_of[c] and gold_of[c] in neighbor_docs[c]
    )
    print(f"\n池召回: 扩边前 {pool_before:.4f} → 扩边后 {pool_after:.4f}"
          f"（其中纯邻居新增 {neighbor_only_hits}/{len(usable)}）")
    if neighbor_only_hits == 0:
        print("  ⚠️ 邻居没带来任何新增召回 —— 跨文档边覆盖不到 gold 文档")

    # ---- 三臂 + 随机 ----
    print("\n=== S1 结构 ===")
    s1_picks = {c: s1_structural(q_of.get(c, ""), neighbors[c]) for c in usable}

    print("=== S2 真重排（8005）===")
    s2_picks = {}
    for c in usable:
        s2_picks[c] = await s2_rerank(q_of.get(c, ""), neighbors[c])

    print("=== S3 混合 ===")
    s3_picks = {
        c: s3_hybrid(q_of.get(c, ""), neighbors[c], s2_picks.get(c, [])) for c in usable
    }

    # 各臂命中率：所选邻居里是否有 gold 文档
    def arm_hit(picks: dict[str, list[str]]) -> float:
        """命中 = gold 文档出现在「源块自身文档 ∪ 所选邻居文档」里。

        为什么把源块自身也算进去：扩边是**追加**候选，原块始终在池里。
        只算邻居会得到 P1 恒 False 的假结论（第一版就这么错的）。
        """
        n = 0
        h = 0
        for c, picked in picks.items():
            gold = gold_of.get(c)
            if not gold:
                continue
            n += 1
            docs = {self_doc.get(c)} if self_doc.get(c) else set()
            for t in picked:
                d = self_doc.get(t)
                if d:
                    docs.add(d)
            if gold in docs:
                h += 1
        return h / n if n else 0.0

    arms = {
        "S1_structural": arm_hit(s1_picks),
        "S2_rerank": arm_hit(s2_picks),
        "S3_hybrid": arm_hit(s3_picks),
    }

    # 随机基线（重复 RANDOM_REPEATS 取均值）
    rnd_total = 0.0
    rnd = random.Random(42)
    for _ in range(RANDOM_REPEATS):
        rp = {c: [x["target"] for x in rnd.sample(neighbors[c], min(TOP_K_NEIGHBORS, len(neighbors[c])))]
              for c in usable}
        rnd_total += arm_hit(rp)
    random_hit = rnd_total / RANDOM_REPEATS

    for k, v in arms.items():
        print(f"  {k}: {v:.4f}")
    print(f"  随机基线({RANDOM_REPEATS} 次均值): {random_hit:.4f}")

    v, facts = _verdict(pool_before, pool_after, arms, random_hit)
    print(f"\n== 裁决：{v} ==")
    for k in ("P0_pool_gain", "P1_no_regress", "P2_beats_random"):
        print(f"  {k}: {facts[k]}")

    payload = {
        "probe": "p71_crossdoc_selector_ab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "kb_version": kb_version,
        "graph_edges": total_edges,
        "n_usable": len(usable),
        "verdict": v,
        "instrument_note": instrument_note,
        "caliber_note": (
            "A.68 已证明现有语料造不出 gold 唯一的检索难集；"
            "本报告若 instrument=saturated_golden，结论只作方向参考。"
            "判据沿用 p42 的 P0/P1/P2，不另立。"
        ),
        **facts,
    }
    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告：{rp}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hard-set", default=None, help="难集路径（不提供则用 golden，标注饱和）")
    ap.add_argument("--kb-version", default=DEFAULT_KB_VERSION,
                    help="知识库版本（golden 用 kb_v1.0.0）")
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/crossdoc_selector_ab.json"),
    )
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.hard_set, a.report, a.kb_version)))
