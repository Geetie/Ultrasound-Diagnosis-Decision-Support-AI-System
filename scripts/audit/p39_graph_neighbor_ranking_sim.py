"""A.65 WP1：图谱邻居「排序到底能不能救」—— **离线仿真，不写库**（含预注册判据）。

为什么要先仿真
--------------
A.63 判图谱扩展 `NOT_APPLIED`（适用面 0/136）；A.64 量化出根因（边只连同文档相邻块，命中的是
一篇一块语料），并登记唯一前置条件＝**邻居排序**：跨文档同章节边的候选池每种子 **125.6 个**，
而检索器一次只取 **5** 条（`graph_neighbor_limit`）→ 不排序就是"任取"，等于灌噪声。
A.63/A.64 连续两轮证明"直觉合理的改动会被数据否掉"，所以本批**先离线量出排序上限**，
达标才动代码与数据。

一个必须先说清的路径差异（决定本探针怎么设计）
----------------------------------------------
* **生产**（`request.use_reranking=True`）：邻居 append 进 `result.chunks` 后，service 层会把
  **整份结果**送进 reranker 取 top-k（`app/rag/service.py:177-225`）→ 排序其实由 rerank 承担。
* **探针/评测腿**（`use_reranking=False`，p36/p15 都这样跑）：邻居保持 append 顺序、`score=0.0`
  → 这时"排序"才有意义。
因此本探针同时量两件事：**① 候选池的召回上限**（扩边能把 gold 文档拉进池子的比例）
与 **② 排序有效性**（在池子里按 query-候选余弦重排后，gold 能否进 top-5）。

三种取法对照（同一份候选池）
----------------------------
| 臂 | 取法 | 作用 |
|---|---|---|
| `baseline` | 现状 top-5（不扩边） | 对照组，应与 p36 的 hit@5 一致（自证） |
| `random5` | 池子里**随机**取 5（多种子采样） | "任取"的噪声基线 |
| `sorted5` | 池子按 **query-候选余弦** 取 top-5 | 排序能做到的上限 |

**预注册判据（写死在脚本里，不事后调整）**
* P1（不回退）：`sorted5_hit + tolerance >= baseline_hit`（`tolerance = 0.005`）
* P2（排序确实有用）：`sorted5_hit >= random5_hit + 0.05`
两条都过 = `GO`；否则 `NO_GO`（结论＝在这份语料上"扩边+排序"不值得做，不建 rc、不动生产）。

口径与复用（禁止另写一把尺子）
------------------------------
* 嵌入与余弦：复用 `scripts/t1_eval_retrieval.py::{embed_texts, cosine_topk}`；
* doc 级命中：复用 `scripts/gate2_bge_top10.py::doc_level_hit_at_k`；
* 种子检索：与 `p38 --coverage` 同一口径（`RetrievalRequest(top_k=10, use_* = False)`）。

用法（服务器，需 8004 嵌入 + DB）：
    ~/uai/venv/bin/python scripts/audit/p39_graph_neighbor_ranking_sim.py \
        --report ~/uai/reports/graph_neighbor_ranking.json

退出码：0 结论已出（GO 或 NO_GO 都算成功）；2 数据/服务缺失（显式失败）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import random
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
for _p in (str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "audit")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from gate2_bge_top10 import doc_level_hit_at_k  # noqa: E402
from t1_eval_retrieval import cosine_topk, embed_texts  # noqa: E402

GOLDEN = pathlib.Path.home() / "uai" / "data" / "golden" / "kb_qa_golden_v1.json"

# ---- 预注册判据（先写死，跑完不改）----
P0_MIN_POOL_GAIN = 0.02  # **扩边必须真的把 gold 拉进池子**（否则"不退化"毫无价值）
P1_TOLERANCE = 0.005  # sorted5 允许比 baseline 低这么多仍算"不回退"
P2_MIN_LIFT = 0.05  # sorted5 必须比 random5 高出这么多才算"排序有效"
TOP_K_FINAL = 5  # 与 settings.rag_rerank_output_top_k 一致（最终进上下文的条数）
RANDOM_SAMPLES = 20  # random5 臂的采样次数（取均值，降随机性）


def verdict_of(
    baseline_hit: float,
    random_hit: float,
    sorted_hit: float,
    pool_before: float,
    pool_after: float,
) -> tuple[str, str]:
    """预注册判据 → (GO / NO_GO_NO_RECALL_GAIN / NO_GO, 理由)。纯函数，便于守卫测试。

    ⚠️ **P0 是首跑之后补的，而且必须补**：首跑时我只写了 P1/P2，得出 `GO` ——
    但同一份报告里池子召回是 `0.9853 → 0.9853`（**扩边一个新 gold 都没捞到**）。
    "排序后不退化"≠"扩边有价值"：P0 用来堵住这个"虚假 GO"。
    （P0 用池子召回而不是 hit@5，是因为 hit@5 在这份 golden 上已被现状打到天花板 0.9853。）
    """
    p0 = pool_after >= pool_before + P0_MIN_POOL_GAIN
    p1 = sorted_hit + P1_TOLERANCE >= baseline_hit
    p2 = sorted_hit >= random_hit + P2_MIN_LIFT
    detail = (
        f"P0 扩边有召回增益：{pool_after:.4f} >= {pool_before:.4f} + {P0_MIN_POOL_GAIN} → {p0}；"
        f"P1 不退化：{sorted_hit:.4f} + {P1_TOLERANCE} >= {baseline_hit:.4f} → {p1}；"
        f"P2 排序有效：{sorted_hit:.4f} >= {random_hit:.4f} + {P2_MIN_LIFT} → {p2}"
    )
    if p0 and p1 and p2:
        return "GO", detail
    if p1 and p2:
        return "NO_GO_NO_RECALL_GAIN", detail
    return "NO_GO", detail


def count_placeholder_chunks(chunks: list) -> int:
    """数「占位块」条数：`source_doc_id == chunk_id` 且 content 为 `[placeholder]`。

    ⚠️ 为什么必须显式检查（A.65 实测踩到）：`Retriever(session_factory=None)` 时每个命中
    都会走 `app/rag/retriever.py:624 _placeholder_chunk` —— `content="[placeholder]"`、
    `source_doc_id=chunk_id`。此时任何按 `source_doc_id`/`content` 做的统计都会**全 0 或全错**，
    而脚本会"正常"退出（本探针第一次跑就是 hit@5 = 0.0 / 0.0 / 0.0）。
    占位块只允许出现在轻量单测里，**探针里一旦占多数就必须响亮失败**。
    """
    bad = 0
    for sc in chunks:
        chunk = getattr(sc, "chunk", sc)
        cid = getattr(chunk, "chunk_id", None)
        sdoc = getattr(chunk, "source_doc_id", None)
        content = getattr(chunk, "content", None)
        if cid and sdoc == cid and (content in (None, "", "[placeholder]")):
            bad += 1
    return bad


async def _collect(kb_version: str, max_candidates: int, top_k_seeds: int) -> dict:
    import asyncpg

    from app.config.settings import settings
    from app.embedding.bge_m3 import BgeM3MedicalService
    from app.embedding.index_factory import create_index_client_from_settings
    from app.rag.protocols import RetrievalRequest
    from app.rag.retriever import Retriever

    gold = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = gold.get("queries", gold)
    queries = [q for q in queries if q.get("query_text") and q.get("relevant_source_doc_id")]
    if not queries:
        raise SystemExit("[FATAL] golden 集没有可用条目（缺 query_text / relevant_source_doc_id）")

    client = await create_index_client_from_settings()
    emb = BgeM3MedicalService(
        base_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}"
    )
    # ⚠️ 必须给 session_factory：否则每个命中都是 `_placeholder_chunk`
    # （`source_doc_id == chunk_id`、`content="[placeholder]"`），后续任何按来源/内容的统计
    # 都会静默算成全 0（本探针首跑现场）。
    from app.config.database import AsyncSessionLocal

    retriever = Retriever(
        vector_client=client,
        embedding_service=emb,
        ultrasound_clip=None,
        bm25_index=None,
        session_factory=AsyncSessionLocal,
    )
    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)

    async def _doc_map(chunk_ids: list[str]) -> dict[str, str]:
        """chunk_id → 真实 source_doc_id（**从 DB 取**，不信检索结果里的那份）。"""
        if not chunk_ids:
            return {}
        rows_ = await conn.fetch(
            "SELECT chunk_id, source_doc_id FROM knowledge_chunks WHERE chunk_id = ANY($1::text[])",
            chunk_ids,
        )
        return {r["chunk_id"]: (r["source_doc_id"] or "") for r in rows_}


    rnd = random.Random(20261015)
    n = 0
    baseline_hits = 0
    random_hit_sum = 0.0
    sorted_hits = 0
    pool_gold_before = 0  # 现状 top-10 池子里含 gold 的查询数
    pool_gold_after = 0  # 扩边后的池子里含 gold 的查询数
    candidates_total = 0
    detail_rows: list[dict] = []
    try:
        for q in queries:
            req = RetrievalRequest(
                query=q["query_text"],
                top_k=10,
                use_query_enhancement=False,
                use_reranking=False,
                use_bm25_fallback=False,
                use_multimodal=False,
            )
            res = await retriever.retrieve(req, enhanced_query=q["query_text"])
            top_ids = [sc.chunk.chunk_id for sc in res.chunks]
            gold_doc = q["relevant_source_doc_id"]
            if not top_ids:
                continue
            if n == 0:
                placeholders = count_placeholder_chunks(res.chunks)
                if placeholders * 2 >= len(top_ids):
                    raise SystemExit(
                        "[FATAL] 检索结果里占位块占多数"
                        f"（{placeholders}/{len(top_ids)}）：说明 session_factory 没生效 —— "
                        "`_placeholder_chunk` 的 source_doc_id 就是 chunk_id，"
                        "任何按来源的统计都会静默全 0。先修构造参数，别读这份报告。"
                    )
            doc_map = await _doc_map(top_ids)
            top_chunks = [(cid, doc_map.get(cid, "")) for cid in top_ids]
            n += 1

            # baseline：现状 top-5（不扩边）
            base_docs = [d for _, d in top_chunks[:TOP_K_FINAL]]
            if gold_doc in base_docs:
                baseline_hits += 1
            base_ids = [cid for cid, _ in top_chunks]
            if gold_doc in {d for _, d in top_chunks}:
                pool_gold_before += 1

            # 扩边候选：与种子同 (organ, section_path) 的跨文档块（与 p38 --coverage 同口径）
            cand_ids: list[str] = []
            seen = set(base_ids)
            for cid in base_ids[:top_k_seeds]:
                row = await conn.fetchrow(
                    "SELECT organ, section_path FROM knowledge_chunks WHERE chunk_id = $1",
                    cid,
                )
                if row is None:
                    continue
                group = await conn.fetch(
                    "SELECT chunk_id, source_doc_id FROM knowledge_chunks"
                    " WHERE kb_version = $1 AND organ = $2"
                    " AND COALESCE(section_path,'') = COALESCE($3,'')",
                    kb_version,
                    row["organ"],
                    row["section_path"],
                )
                for g in group:
                    if g["chunk_id"] not in seen:
                        seen.add(g["chunk_id"])
                        cand_ids.append(g["chunk_id"])
            if len(cand_ids) > max_candidates:
                cand_ids = rnd.sample(cand_ids, max_candidates)
            candidates_total += len(cand_ids)
            if not cand_ids:
                random_hit_sum += 1.0 if gold_doc in base_docs else 0.0
                if gold_doc in base_docs:
                    sorted_hits += 1
                continue

            # 池子 = 原 top-10 ∪ 扩边候选（**生产的真实形态**：邻居 append 后由 rerank 取 5；
            # 这里用同一把尺子模拟"排序"这一步，因为探针跑的是 rerank 关）
            pool_ids = list(base_ids) + [c for c in cand_ids if c not in set(base_ids)]
            pool_rows = await conn.fetch(
                "SELECT chunk_id, source_doc_id, content FROM knowledge_chunks"
                " WHERE chunk_id = ANY($1::text[])",
                pool_ids,
            )
            by_id = {r["chunk_id"]: r for r in pool_rows}
            pool = [
                (cid, by_id[cid]["source_doc_id"] or "", by_id[cid]["content"] or "")
                for cid in pool_ids
                if cid in by_id
            ]
            if not pool:
                random_hit_sum += 1.0 if gold_doc in base_docs else 0.0
                if gold_doc in base_docs:
                    sorted_hits += 1
                continue

            # 扩边后的池子是否含 gold（扩边能带来的召回上限）
            if gold_doc in {d for _, d, _ in pool}:
                pool_gold_after += 1

            # 排序臂：把**整个池子**按 query 相似度重排取 5（模拟生产的 rerank 取 top-5）
            vecs = embed_texts(
                [q["query_text"], *[t for _, _, t in pool]],
                bge_url=f"http://{settings.bge_m3_host}:{settings.bge_m3_port}",
            )
            qv, cv = vecs[0], vecs[1:]
            top = cosine_topk([qv], cv, min(TOP_K_FINAL, len(cv)))[0]
            corpus_rows = [{"source_doc_id": d} for _, d, _ in pool]
            if doc_level_hit_at_k(top, corpus_rows, gold_doc, TOP_K_FINAL):
                sorted_hits += 1

            # 任取臂：从同一池子里随机取 5（多种子平均）＝"不排序"的噪声基线
            per_q = 0.0
            for _ in range(RANDOM_SAMPLES):
                pick = rnd.sample(range(len(pool)), min(TOP_K_FINAL, len(pool)))
                if gold_doc in {pool[i][1] for i in pick}:
                    per_q += 1.0
            random_hit_sum += per_q / RANDOM_SAMPLES

            if len(detail_rows) < 20:
                detail_rows.append(
                    {
                        "query_id": q.get("query_id"),
                        "pool_size": len(pool),
                        "candidates": len(cand_ids),
                        "baseline_hit": gold_doc in base_docs,
                        "pool_has_gold": gold_doc in {d for _, d, _ in pool},
                    }
                )
    finally:
        await conn.close()

    baseline_hit = baseline_hits / n if n else 0.0
    random_hit = random_hit_sum / n if n else 0.0
    sorted_hit = sorted_hits / n if n else 0.0
    pool_before = pool_gold_before / n if n else 0.0
    pool_after = pool_gold_after / n if n else 0.0
    code, why = verdict_of(baseline_hit, random_hit, sorted_hit, pool_before, pool_after)
    return {
        "probe": "p39_graph_neighbor_ranking_sim",
        "kb_version": kb_version,
        "golden": str(GOLDEN),
        "n_queries": n,
        "config": {
            "top_k_final": TOP_K_FINAL,
            "max_candidates_per_query": max_candidates,
            "top_k_seeds": top_k_seeds,
            "random_samples": RANDOM_SAMPLES,
            "rule": "跨文档同章节 (organ, section_path) 组内候选；与 p38 --coverage 同口径",
        },
        "preregistered_criteria": {
            "P0_min_pool_gain": P0_MIN_POOL_GAIN,
            "P1_tolerance": P1_TOLERANCE,
            "P2_min_lift": P2_MIN_LIFT,
            "note": "P0 是首跑后补的：首跑只有 P1/P2 → 给出虚假 GO（池子召回 0.9853→0.9853 明明零增益）",
        },
        "hit_at_5": {
            "baseline_no_expansion": round(baseline_hit, 4),
            "random_5_from_pool": round(random_hit, 4),
            "sorted_5_by_cosine": round(sorted_hit, 4),
        },
        "pool_recall": {
            "top10_contains_gold": round(pool_gold_before / n, 4) if n else 0.0,
            "expanded_pool_contains_gold": round(pool_gold_after / n, 4) if n else 0.0,
            "avg_candidates_per_query": round(candidates_total / n, 1) if n else 0.0,
        },
        "verdict": {"code": code, "why": why},
        "sample_details": detail_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument("--max-candidates", type=int, default=40, help="每查询候选上限（控嵌入成本）")
    ap.add_argument("--top-k-seeds", type=int, default=3)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/graph_neighbor_ranking.json"))
    args = ap.parse_args()

    t0 = time.monotonic()
    try:
        payload = asyncio.run(
            _collect(args.kb_version, args.max_candidates, args.top_k_seeds)
        )
    except SystemExit as exc:
        print(str(exc))
        return 2
    except Exception as exc:
        print(f"[FATAL] 仿真失败：{type(exc).__name__}: {exc}")
        return 2

    payload["generated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    payload["wall_s"] = round(time.monotonic() - t0, 1)
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    h = payload["hit_at_5"]
    p = payload["pool_recall"]
    print(f"查询 {payload['n_queries']} 条｜平均候选 {p['avg_candidates_per_query']} 个/查询")
    print(f"hit@5：baseline {h['baseline_no_expansion']} / 任取 {h['random_5_from_pool']} / "
          f"按余弦排序 {h['sorted_5_by_cosine']}")
    print(f"池子召回：现状 top-10 {p['top10_contains_gold']} → 扩边后 {p['expanded_pool_contains_gold']}")
    print(f"判定 = **{payload['verdict']['code']}**：{payload['verdict']['why']}")
    print(f"报告：{out}  耗时 {payload['wall_s']}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
