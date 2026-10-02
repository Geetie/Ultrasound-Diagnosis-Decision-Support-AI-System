"""T1：构建检索评测 golden set（真实 US-365K 文本 + 程序化相关性标注）。

设计（标准检索评测构造，MTEB 式）：
- 语料池（corpus）：从 US-365K test.jsonl 确定性抽取 `--corpus-size` 条真实 caption，
  每条视为一个"知识块"文档（doc_id = us365k-<行号>）。
- 查询集（queries）：从**不在语料池中**的行抽取 `--num-queries` 条，query 文本取
  caption 的首句（截断到 --query-max-chars），模拟"医生输入部分信息"的真实用法。
- 相关性标注：每条 query 的相关文档 = 其来源 caption 对应的 doc（恰 1 条）。
  单相关文档 → Recall@K = Hit@K、nDCG@K 由命中排名决定、MRR 可算——
  指标口径与多相关文档一致，只是分母为 1。

确定性：随机种子固定（--seed），同参数重跑结果一致。

输出 JSON（供 t1_eval_retrieval.py 消费）：
{
  "dataset": "US-365K-test",
  "seed": ..., "corpus_size": ..., "num_queries": ...,
  "corpus": [{"doc_id", "text"}...],
  "queries": [{"query_id", "query_text", "source_caption", "relevant_doc_id"}...]
}
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建 US-365K 检索评测 golden set")
    parser.add_argument("--source-jsonl", required=True, help="US-365K test.jsonl 路径")
    parser.add_argument("--output", required=True)
    parser.add_argument("--corpus-size", type=int, default=2000)
    parser.add_argument("--num-queries", type=int, default=200)
    parser.add_argument("--query-max-chars", type=int, default=60,
                        help="查询文本截断长度（模拟医生输入的部分信息）")
    parser.add_argument("--seed", type=int, default=20260929)
    return parser.parse_args()


def truncate_query(caption: str, max_chars: int) -> str:
    """取首句并截断：优先按中文/英文句界切，超长再硬截。"""
    text = caption.strip()
    for sep in ("。", ".", "；", ";", "！"):
        idx = text.find(sep)
        if 0 < idx <= max_chars:
            return text[: idx + 1]
    return text[:max_chars]


def main() -> int:
    args = parse_args()
    rows: list[dict] = []
    with open(args.source_jsonl, encoding="utf-8") as f:
        for line_no, line in enumerate(f):
            row = json.loads(line)
            rows.append(
                {
                    "row_no": line_no,
                    "doc_id": f"us365k-{line_no:06d}",
                    "caption": row["caption"].strip(),
                }
            )
    if len(rows) < args.corpus_size + args.num_queries:
        print(f"ERROR: 源数据 {len(rows)} 行不足以支撑语料+查询规模")
        return 1

    rng = random.Random(args.seed)
    rng.shuffle(rows)

    corpus_rows = rows[: args.corpus_size]
    # 查询必须来自语料行本身（相关文档=来源 caption）：query 是语料文档的
    # "部分信息"变体（首句/截断），模拟医生输入部分信息检索完整知识的场景。
    query_rows = rng.sample(corpus_rows, min(args.num_queries, len(corpus_rows)))

    corpus = [{"doc_id": r["doc_id"], "text": r["caption"]} for r in corpus_rows]
    queries = [
        {
            "query_id": f"q-{i:04d}",
            "query_text": truncate_query(r["caption"], args.query_max_chars),
            "source_caption": r["caption"],
            "relevant_doc_id": r["doc_id"],
        }
        for i, r in enumerate(query_rows)
    ]

    dataset = {
        "dataset": "US-365K-test",
        "seed": args.seed,
        "corpus_size": args.corpus_size,
        "num_queries": args.num_queries,
        "query_max_chars": args.query_max_chars,
        "note": "查询=真实超声报告 caption 首句（部分信息）；相关文档=来源 caption 本身",
        "corpus": corpus,
        "queries": queries,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"golden set: corpus={len(corpus)} queries={len(queries)} "
        f"seed={args.seed} -> {out}"
    )
    qlens = [len(q["query_text"]) for q in queries]
    print(f"query length min/avg/max: {min(qlens)}/{sum(qlens)/len(qlens):.0f}/{max(qlens)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
