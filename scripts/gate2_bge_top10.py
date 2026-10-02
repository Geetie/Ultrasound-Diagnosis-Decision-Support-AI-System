# -*- coding: utf-8 -*-
"""Gate 2 或分支评测：bge-m3 纯文本检索 Top10 命中率（≥88% 判定）。

口径：golden set 每条 query_text → bge-m3(8004) 文本嵌入 → 对
kb_v1.3.0 全量 knowledge_chunks 余弦 Top10 → 命中 = gold
source_doc_id 出现在 Top10 的任一 chunk 的 source_doc_id 中。
复用 t1_eval_retrieval（embed_texts/cosine_topk 同步 HTTP）与
t1c_eval_retrieval 的 doc 级命中口径。

产出：reports/gate2_bge_top10.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from t1_eval_retrieval import cosine_topk, embed_texts  # noqa: E402

GOLDEN = Path("/mnt/ee16/home/student03/uai/data/golden/kb_qa_golden_v1.json")
OUT = Path("/mnt/ee16/home/student03/uai/reports/gate2_bge_top10.json")
BGE_URL = "http://127.0.0.1:8004"


async def load_corpus(kb_version: str) -> list[dict]:
    """knowledge_chunks 全量（chunk_id/source_doc_id/content）——同 t1c 口径。"""
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url
    pg_dsn = dsn.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(pg_dsn)
    try:
        rows = await conn.fetch(
            "SELECT chunk_id, source_doc_id, content FROM knowledge_chunks "
            "WHERE kb_version = $1",
            kb_version,
        )
        return [
            {
                "chunk_id": r["chunk_id"],
                "source_doc_id": r["source_doc_id"],
                "text": r["content"],
            }
            for r in rows
        ]
    finally:
        await conn.close()


def doc_level_hit_at_k(
    ranking: list[tuple[int, float]], corpus: list[dict], gold_doc: str, k: int
) -> bool:
    # cosine_topk 返回 [(idx, score), ...]
    return any(
        corpus[idx]["source_doc_id"] == gold_doc for idx, _score in ranking[:k]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--kb-version", default="kb_v1.3.0")
    ap.add_argument("--batch-size", type=int, default=64)
    args = ap.parse_args()

    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    queries = golden["queries"]
    print(f"golden queries: {len(queries)}", flush=True)

    corpus = asyncio.run(load_corpus(args.kb_version))
    print(
        f"corpus chunks: {len(corpus)} (kb_version={args.kb_version})",
        flush=True,
    )

    q_texts = [q["query_text"] for q in queries]
    t0 = time.time()
    q_vecs = embed_texts(q_texts, BGE_URL, batch_size=args.batch_size)
    c_vecs = embed_texts(
        [c["text"] for c in corpus], BGE_URL, batch_size=args.batch_size
    )
    print(f"embedded in {time.time()-t0:.1f}s", flush=True)

    rankings = cosine_topk(q_vecs, c_vecs, k=10)

    hits = {1: 0, 5: 0, 10: 0}
    mrr = 0.0
    for q, ranking in zip(queries, rankings):
        gold_doc = q["relevant_source_doc_id"]
        for k in hits:
            if doc_level_hit_at_k(ranking, corpus, gold_doc, k):
                hits[k] += 1
        for rank, (idx, _score) in enumerate(ranking, start=1):
            if corpus[idx]["source_doc_id"] == gold_doc:
                mrr += 1.0 / rank
                break

    n = len(queries)
    result = {
        "gate": "Gate 2 or-branch: bge-m3 Top10 >= 88%",
        "kb_version": args.kb_version,
        "n_queries": n,
        "corpus_chunks": len(corpus),
        "hit_at_1": round(hits[1] / n, 4),
        "hit_at_5": round(hits[5] / n, 4),
        "hit_at_10": round(hits[10] / n, 4),
        "mrr": round(mrr / n, 4),
        "threshold": 0.88,
        "verdict": "PASS" if hits[10] / n >= 0.88 else "FAIL",
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    OUT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
