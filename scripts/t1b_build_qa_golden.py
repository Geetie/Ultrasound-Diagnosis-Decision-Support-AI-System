"""T1c：QA 型 golden set 构造（对标 BEIR/MS MARCO 查询-文档构造法）。

与自指式 golden（chunk 首句截断）不同：query 取**真实患者问题**（对话数据的
title 字段，即患者提问的短摘要），gold = 其来源文档的全部 chunk（source_doc
级相关性，使 chunk 变体重建后同一 golden 仍可用）。

数据来源：~/uai/data/kb_sources/dialogue_docs.jsonl（t1b 产物，Apache 2.0），
content 格式 "患者提问：{title}。{ask}\\n医生回答：{answer}"。
corpus 来自 PG knowledge_chunks（指定 kb_version），保证 chunk_id 与真机索引一致。

用法（真机 ~/uai/src）：
    PYTHONPATH=~/uai/src python scripts/t1b_build_qa_golden.py \
        --kb-version kb_v1.0.0 \
        --output data/golden/kb_qa_golden_v1.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

_QUESTION_RE = re.compile(r"患者提问：(.+?)\n医生回答：", re.DOTALL)
_DEPT_RE = re.compile(r"^dialogue_(.+?)_\d+$")
_QUERY_MAX_CHARS = 60  # 与 v1 自指 golden 的 query_max_chars 口径一致
_MIN_QUERY_CHARS = 8  # 过滤过泛问题（如"怎么办"）


def extract_question(content: str) -> str | None:
    m = _QUESTION_RE.search(content)
    if not m:
        return None
    return m.group(1).strip()


async def load_chunks(dsn: str, kb_version: str) -> list[dict]:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            """
            SELECT chunk_id, source_doc_id, content
            FROM knowledge_chunks WHERE kb_version = $1
            ORDER BY chunk_id
            """,
            kb_version,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description="QA 型 golden set 构造")
    parser.add_argument("--kb-version", default="kb_v1.0.0")
    parser.add_argument(
        "--dialogue-jsonl",
        default=str(Path.home() / "uai/data/kb_sources/dialogue_docs.jsonl"),
    )
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=20260929)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    import random

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings

    # 1) 读对话文档，抽问题（title 段），按科分层
    docs: list[dict] = []
    lines = Path(args.dialogue_jsonl).read_text(encoding="utf-8").splitlines()
    for line in lines:
        doc = json.loads(line)
        q = extract_question(doc.get("content", ""))
        if not q:
            continue
        dept_m = _DEPT_RE.match(doc["source_doc_id"])
        if not dept_m:
            continue
        q = re.sub(r"\s+", " ", q)
        if not (_MIN_QUERY_CHARS <= len(q) <= _QUERY_MAX_CHARS):
            continue
        docs.append(
            {
                "source_doc_id": doc["source_doc_id"],
                "dept": dept_m.group(1),
                "query_text": q,
            }
        )

    # 同题去重（title 在数据集内可能重复）
    seen_titles: set[str] = set()
    unique_docs = []
    for d in docs:
        key = d["query_text"].lower()
        if key not in seen_titles:
            seen_titles.add(key)
            unique_docs.append(d)
    print(f"候选问题（解析成功且长度合规且去重）: {len(unique_docs)}")

    # 2) 读 chunks，建立 source_doc_id → chunk_ids 映射
    dsn = to_asyncpg_dsn(settings.database_url)
    chunks = asyncio.run(load_chunks(dsn, args.kb_version))
    src_to_chunks: dict[str, list[dict]] = {}
    for c in chunks:
        src_to_chunks.setdefault(c["source_doc_id"], []).append(c)
    print(f"chunks（{args.kb_version}）: {len(chunks)}")

    # 3) 分层采样：每科尽量均分，且仅保留有 chunk 的文档
    by_dept: dict[str, list[dict]] = {}
    for d in unique_docs:
        if d["source_doc_id"] in src_to_chunks:
            by_dept.setdefault(d["dept"], []).append(d)
    rng = random.Random(args.seed)
    per_dept = max(1, args.sample // max(1, len(by_dept)))
    sampled: list[dict] = []
    for dept in sorted(by_dept):
        pool = by_dept[dept]
        rng.shuffle(pool)
        sampled.extend(pool[:per_dept])
    rng.shuffle(sampled)
    sampled = sampled[: args.sample]
    if not sampled:
        print("ERROR: 采样为空")
        return 1

    # 4) 组装 golden（BEIR 风格：corpus + queries，相关性内联）
    corpus = [
        {
            "chunk_id": c["chunk_id"],
            "source_doc_id": c["source_doc_id"],
            "text": c["content"],
        }
        for c in chunks
    ]
    queries = [
        {
            "query_id": f"qa-{i:04d}",
            "query_text": d["query_text"],
            "dept": d["dept"],
            "source_doc_id": d["source_doc_id"],
            "relevant_doc_id": src_to_chunks[d["source_doc_id"]][0]["chunk_id"],
            "relevant_source_doc_id": d["source_doc_id"],
        }
        for i, d in enumerate(sorted(sampled, key=lambda x: x["source_doc_id"]))
    ]
    out = {
        "dataset": "kb_qa_golden_v1",
        "kb_version": args.kb_version,
        "relevance_level": "source_doc",
        "seed": args.seed,
        "corpus_size": len(corpus),
        "num_queries": len(queries),
        "query_max_chars": _QUERY_MAX_CHARS,
        "note": "query=真实患者问题（对话 title），gold=来源文档全部 chunk（source_doc 级）；构造法对标 BEIR/MS MARCO",
        "corpus": corpus,
        "queries": queries,
    }
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    dept_stat: dict[str, int] = {}
    for q in queries:
        dept_stat[q["dept"]] = dept_stat.get(q["dept"], 0) + 1
    print(f"写出 {out_path}: queries={len(queries)} by_dept={dept_stat}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
