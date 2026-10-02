"""T1：检索质量评测——Recall@K / MRR / nDCG，三模式（dense / bm25 / hybrid RRF）。

指标口径（业界标准，见 16 号附录 A.3 学习记录）：
- Recall@K：相关文档进入 Top-K 的比例（单相关文档时等价 Hit@K）
- MRR@10：第一条相关结果排名倒数的均值
- nDCG@10：单相关文档时 = log2(2)/log2(rank+1)（rank≤10），否则 0

三种模式：
- dense：bge-m3 嵌入（8004 服务，1024 维）+ 余弦
- bm25：jieba 分词 + BM25Okapi（rank_bm25 缺失时用内置等价实现）
- hybrid：RRF（k=60）融合 dense 与 bm25 的 Top-100 排名——与 app/rag/retriever.py
  的融合方式同构，用于对照"混合检索召回率 ≥90%（Gate 0）"的验收口径

用法（真机 ~/uai/src，需 8004 就绪）：
    python scripts/t1_eval_retrieval.py \
        --golden-set data/golden/us365k_retrieval_golden_v1.json \
        --output reports/t1_retrieval_baseline.json
"""
from __future__ import annotations

import argparse
import json
import math
import urllib.request
from pathlib import Path


def embed_texts(texts: list[str], bge_url: str, batch_size: int = 64) -> list[list[float]]:
    """经 8004 bge-m3 服务嵌入（契约：dense_embeddings 维度一致、条数==texts）。"""
    vectors: list[list[float]] = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        body = json.dumps(
            {"texts": batch, "return_dense": True, "return_sparse": False}
        ).encode("utf-8")
        req = urllib.request.Request(
            f"{bge_url}/v1/embeddings",
            data=body,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        dense = data["dense_embeddings"]
        if len(dense) != len(batch):
            raise RuntimeError(f"embedding count mismatch: {len(dense)} != {len(batch)}")
        vectors.extend(dense)
        if (i // batch_size) % 10 == 0:
            print(f"  embedded {i + len(batch)}/{len(texts)}")
    return vectors


def cosine_topk(
    query_vecs: list[list[float]], corpus_vecs: list[list[float]], k: int
) -> list[list[tuple[int, float]]]:
    """余弦 Top-K（向量已 L2 归一化时点积即余弦；此处按未归一化防御性计算）。"""
    results: list[list[tuple[int, float]]] = []
    for qv in query_vecs:
        qn = math.sqrt(sum(x * x for x in qv)) or 1.0
        scores: list[tuple[int, float]] = []
        for idx, cv in enumerate(corpus_vecs):
            cn = math.sqrt(sum(x * x for x in cv)) or 1.0
            dot = sum(a * b for a, b in zip(qv, cv, strict=False))
            scores.append((idx, dot / (qn * cn)))
        scores.sort(key=lambda x: x[1], reverse=True)
        results.append(scores[:k])
    return results


def _tokenize(text: str) -> list[str]:
    try:
        import jieba

        return [t for t in jieba.lcut(text.lower()) if t.strip()]
    except ImportError:
        # 无 jieba 时退化为字符 bigram（中文检索的保守近似）
        text = text.lower()
        return [text[i : i + 2] for i in range(max(1, len(text) - 1))]


class SimpleBM25:
    """BM25Okapi 等价实现（rank_bm25 缺失时的回退；k1=1.5, b=0.75 与其默认一致）。"""

    def __init__(self, corpus_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_len = [len(toks) for toks in corpus_tokens]
        self.avgdl = sum(self.doc_len) / len(self.doc_len) if self.doc_len else 1.0
        self.tfidf: list[dict[str, float]] = []
        df: dict[str, int] = {}
        for toks in corpus_tokens:
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t in tf:
                df[t] = df.get(t, 0) + 1
            self.tfidf.append(tf)
        self.idf = {
            t: math.log((len(corpus_tokens) - n + 0.5) / (n + 0.5) + 1.0)
            for t, n in df.items()
        }

    def get_scores(self, query_tokens: list[str]) -> list[float]:
        scores = []
        for i, tf in enumerate(self.tfidf):
            dl = self.doc_len[i]
            score = 0.0
            for t in query_tokens:
                f = tf.get(t)
                if not f:
                    continue
                idf = self.idf.get(t, 0.0)
                score += idf * f * (self.k1 + 1) / (
                    f + self.k1 * (1 - self.b + self.b * dl / self.avgdl)
                )
            scores.append(score)
        return scores


def bm25_topk(
    queries: list[str], corpus_texts: list[str], k: int
) -> list[list[tuple[int, float]]]:
    try:
        from rank_bm25 import BM25Okapi

        bm25 = BM25Okapi([_tokenize(t) for t in corpus_texts])
    except ImportError:
        bm25 = SimpleBM25([_tokenize(t) for t in corpus_texts])

    def get(q: str) -> list[tuple[int, float]]:
        return list(enumerate(bm25.get_scores(_tokenize(q))))

    results: list[list[tuple[int, float]]] = []
    for q in queries:
        scored = get(q)
        scored.sort(key=lambda x: x[1], reverse=True)
        results.append(scored[:k])
    return results


def rrf_fuse(
    rank_a: list[list[tuple[int, float]]],
    rank_b: list[list[tuple[int, float]]],
    top_k: int,
    k_const: int = 60,
) -> list[list[tuple[int, float]]]:
    """RRF 融合（与 app/rag/retriever.py 的 _rrf_fuse 同构，k=60）。"""
    fused: list[list[tuple[int, float]]] = []
    for ra, rb in zip(rank_a, rank_b, strict=True):
        scores: dict[int, float] = {}
        for rank, (idx, _s) in enumerate(ra):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k_const + rank + 1)
        for rank, (idx, _s) in enumerate(rb):
            scores[idx] = scores.get(idx, 0.0) + 1.0 / (k_const + rank + 1)
        ordered = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        fused.append(ordered)
    return fused


def compute_metrics(
    rankings: list[list[tuple[int, float]]],
    queries: list[dict],
    k_values: list[int],
) -> dict[str, float]:
    """单相关文档口径：Recall@K=Hit@K、MRR@10、nDCG@10=log2(2)/log2(r+1)。"""
    hits = {f"recall@{k}": 0 for k in k_values}
    mrr_sum = 0.0
    ndcg_sum = 0.0
    for ranking, q in zip(rankings, queries, strict=True):
        for k in k_values:
            top = {idx for idx, _s in ranking[:k]}
            if q["relevant_corpus_idx"] in top:
                hits[f"recall@{k}"] += 1
        rr = 0.0
        ndcg = 0.0
        for rank, (idx, _s) in enumerate(ranking[:10]):
            if idx == q["relevant_corpus_idx"]:
                rr = 1.0 / (rank + 1)
                ndcg = math.log(2) / math.log(rank + 2)
                break
        mrr_sum += rr
        ndcg_sum += ndcg
    n = len(queries)
    out = {k: round(v / n, 4) for k, v in hits.items()}
    out["mrr@10"] = round(mrr_sum / n, 4)
    out["ndcg@10"] = round(ndcg_sum / n, 4)
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="T1 检索质量评测（三模式）")
    parser.add_argument("--golden-set", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--bge-url", default="http://127.0.0.1:8004")
    parser.add_argument("--candidate-depth", type=int, default=100)
    args = parser.parse_args()

    golden = json.loads(Path(args.golden_set).read_text(encoding="utf-8"))
    corpus = golden["corpus"]
    queries = golden["queries"]
    corpus_texts = [c["text"] for c in corpus]
    doc_id_to_idx = {c["doc_id"]: i for i, c in enumerate(corpus)}

    # 相关文档映射到语料下标（查询与语料来自同一确定性抽样，来源必在池中）
    for q in queries:
        q["relevant_corpus_idx"] = doc_id_to_idx[q["relevant_doc_id"]]

    print(f"corpus={len(corpus)} queries={len(queries)}")
    k_values = [5, 10]

    # 1) dense
    print("[1/3] dense (bge-m3)…")
    corpus_vecs = embed_texts(corpus_texts, args.bge_url)
    query_vecs = embed_texts([q["query_text"] for q in queries], args.bge_url)
    dense_rank = cosine_topk(query_vecs, corpus_vecs, args.candidate_depth)
    dense_metrics = compute_metrics(dense_rank, queries, k_values)

    # 2) bm25
    print("[2/3] bm25 (jieba)…")
    bm25_rank = bm25_topk([q["query_text"] for q in queries], corpus_texts, args.candidate_depth)
    bm25_metrics = compute_metrics(bm25_rank, queries, k_values)

    # 3) hybrid RRF
    print("[3/3] hybrid (RRF k=60)…")
    hybrid_rank = rrf_fuse(dense_rank, bm25_rank, args.candidate_depth)
    hybrid_metrics = compute_metrics(hybrid_rank, queries, k_values)

    report = {
        "golden_set": args.golden_set,
        "corpus_size": len(corpus),
        "num_queries": len(queries),
        "candidate_depth": args.candidate_depth,
        "metrics": {
            "dense": dense_metrics,
            "bm25": bm25_metrics,
            "hybrid_rrf": hybrid_metrics,
        },
        "gates": {
            "hybrid_recall@10": "≥ 0.90（Gate 0 混合检索召回率）",
            "dense_recall@10": "≥ 0.88（bge-m3-medical 纯文本降级 Top10）",
        },
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["metrics"], ensure_ascii=False, indent=2))
    print(f"report -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
