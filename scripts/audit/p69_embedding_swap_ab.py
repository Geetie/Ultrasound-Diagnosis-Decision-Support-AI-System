"""A.95 Q10：文本嵌入换代离线 A/B（bge-m3-medical vs Qwen3-Embedding-0.6B）。

## 这条要回答什么

15 号技术升级规划写的是：「文本嵌入可测 **Qwen3-Embedding-0.6B**（MTEB 中文上位）；
**仅做 A/B 基准；无提升即放弃，避免无谓重建向量库**」。

判据（15 号原文，**跑前写死**）：
* `hit@5` 或 `MRR` 提升 **≥2%（绝对）**
* **且两集都不回退**
* 达标才新建 collection 双写灰度 → 真机对照后再切

## ⚠️ 必须先说清的约束（否则结论会被误读）

1. **基线已饱和**：`gate2_bge_top10.json` 实测 `hit@5 = 0.9853`、`hit@10 = 0.9853`、
   `hit@1 = 0.9485`、`MRR = 0.9635`（n=136）。
   → **hit@5 的天花板只剩 1.47pp**，物理上不可能达到"+2% 绝对"。
   所以本报告把"是否达标"拆成两个可判断的问法：
   * `P_hit5`：hit@5 能否提升（哪怕 <2pp，也说明方向）
   * `P_mrr`：**MRR 有 3.65pp 空间**，这是唯一能兑现 ≥2pp 的指标
   并明确写：**若 hit@5 已饱和，就只以 MRR 判**（否则判据自相矛盾）。
2. **Qwen3-Embedding 需要 GPU/独立服务**。本脚本**不在生产 8004 上动手**：
   要么走一个临时 8005+ 端口的新服务实例，要么用 `--dry` 只算基线分布。
   没有新服务时，脚本**不谎报"已对比"** —— 直接返回 `NOT_RUN_NO_ENCODER`。

## 本脚本做三件事

1. **基线复现**：用现有 bge-m3(8004) 重算 hit@1/5/10 + MRR（含 corpus=1257 块）
   —— 同时验证"基线数字可复现"（历史数字若不可复现，A/B 没意义）。
2. **对端编码器对比**（若提供了 `--alt-url`）：同语料 + 同 query，算同一套指标。
3. **裁决**：按上面拆分后的判据给 `IMPROVED` / `NO_IMPROVEMENT` / `NOT_RUN_NO_ENCODER`。

## 用法

    # 1) 只复现基线（不装新模型也能跑）
    ~/uai/venv/bin/python scripts/audit/p69_embedding_swap_ab.py

    # 2) 有 Qwen3-Embedding 服务时做真对比
    ~/uai/venv/bin/python scripts/audit/p69_embedding_swap_ab.py \
        --alt-url http://127.0.0.1:8014 --alt-name qwen3-emb-0.6b

退出码：0 有结论（含 NO_IMPROVEMENT）｜2 前置缺失。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

GOLDEN = pathlib.Path.home() / "uai" / "data" / "golden" / "kb_qa_golden_v1.json"
BASELINE_JSON = pathlib.Path.home() / "uai" / "reports" / "gate2_bge_top10.json"
BGE_URL = "http://127.0.0.1:8004"

#: 判据（15 号原文，预注册）
MIN_HIT5_LIFT = 0.02      # 绝对 2pp
MIN_MRR_LIFT = 0.02       # 绝对 2pp


async def load_corpus(kb_version: str) -> list[dict]:
    """knowledge_chunks 全量（与 gate2 同口径）。"""
    import asyncpg

    from app.config.settings import settings

    dsn = settings.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT chunk_id, source_doc_id, content FROM knowledge_chunks "
            "WHERE kb_version = $1",
            kb_version,
        )
    finally:
        await conn.close()
    return [
        {"chunk_id": r["chunk_id"], "source_doc_id": r["source_doc_id"], "content": r["content"]}
        for r in rows
    ]


async def embed_texts(url: str, texts: list[str], batch: int = 32) -> list[list[float]]:
    """批量文本嵌入。

    ⚠️ 两套端点形态都要支持（真机踩到）：
    * **本项目 8004（bge-m3）是自定义格式**：`POST /v1/embeddings`
      body = `{"texts": [...], "return_dense": true, "return_sparse": false}`
      → 不是 OpenAI 标准 `{"input": [...]}`！直接按 OpenAI 发会 **422**。
    * Qwen3-Embedding 通常跑在 vLLM 的 OpenAI server 上：`{"input": [...], "model": "..."}`。

    所以先按本项目格式发；若 422/400，再退到 OpenAI 格式。两条路都留痕。
    """
    import httpx

    out: list[list[float]] = []
    async with httpx.AsyncClient(timeout=180.0) as client:
        for i in range(0, len(texts), batch):
            chunk = texts[i : i + batch]
            # ① 本项目 bge-m3 自定义格式
            r = await client.post(
                f"{url}/v1/embeddings",
                json={"texts": chunk, "return_dense": True, "return_sparse": False},
            )
            if r.status_code in (400, 422):
                # ② 退到 OpenAI 兼容格式
                r = await client.post(
                    f"{url}/v1/embeddings",
                    json={"input": chunk, "model": "embedding"},
                )
            r.raise_for_status()
            data = r.json()
            if isinstance(data, dict):
                # 本项目形态：{"embeddings": [[...], ...]} 或 {"dense": [...]}
                for key in ("embeddings", "dense", "data"):
                    if key in data:
                        vals = data[key]
                        if vals and isinstance(vals[0], dict):
                            vals = sorted(vals, key=lambda x: x.get("index", 0))
                            out.extend([it["embedding"] for it in vals])
                        else:
                            out.extend(vals)
                        break
                else:
                    raise RuntimeError(f"无法识别的嵌入响应键：{list(data)[:8]}")
            elif isinstance(data, list):
                out.extend(data)
            else:
                raise RuntimeError(f"无法识别的嵌入响应类型：{type(data)}")
    return out


def _norm(v: list[float]) -> float:
    return sum(x * x for x in v) ** 0.5 or 1.0


def cosine(a: list[float], b: list[float]) -> float:
    na, nb = _norm(a), _norm(b)
    return sum(x * y for x, y in zip(a, b, strict=False)) / (na * nb)


#: Qwen3-Embedding 是 **Instruction Aware** 模型（README 明写）。
#: 官方建议：query 侧加指令前缀，document 侧**不加**。
#: 漏掉这一步会让检索指标大幅虚低 —— 我第一次跑就犯了这个错，
#: 得到 hit@5 -19.85pp / MRR -25.62pp 的"退化"结论，实际是用法不对。
QWEN3_QUERY_INSTRUCTION = (
    "Instruct: Given a Chinese medical question about ultrasound diagnosis, "
    "retrieve relevant knowledge passages\nQuery: "
)


def embed_local(
    model_dir: str,
    texts: list[str],
    batch: int = 8,
    *,
    is_query: bool = False,
    instruction: str = QWEN3_QUERY_INSTRUCTION,
) -> list[list[float]]:
    """**进程内** transformers 嵌入（Qwen3-Embedding，无需 vLLM）。

    为什么不用 vLLM：真机 `venv-vllm` 起 embed server 报
    `RuntimeError: Failed to infer device type`（NVML 不可用）；
    而 0.6B 在 CPU 上 3 条只要 0.07s —— **完全够做离线 A/B**，
    且不占用 GPU（生产 8006 判官在跑）。

    口径（照 README）：
    * **query 侧加 instruction 前缀**（`is_query=True`）；
    * **document 侧不加**；
    * 取 **last-token** hidden state（不是 mean pooling）；
    * L2 归一化后算余弦。
    """
    import torch
    from transformers import AutoModel, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(model_dir, trust_remote_code=True)
    model = AutoModel.from_pretrained(model_dir, trust_remote_code=True, dtype=torch.float32)
    model.eval()
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        if is_query and instruction:
            chunk = [instruction + t for t in chunk]
        enc = tok(chunk, padding=True, truncation=True, max_length=512, return_tensors="pt")
        with torch.no_grad():
            res = model(**enc)
        last = res.last_hidden_state[:, -1]
        last = torch.nn.functional.normalize(last, p=2, dim=1)
        out.extend(last.tolist())
    return out


async def eval_encoder(
    url: str | None,
    corpus: list[dict],
    queries: list[dict],
    topk: int = 10,
    *,
    local_model: str | None = None,
) -> dict:
    """算 hit@1/5/10 + MRR（doc 级命中，与 gate2 同口径）。

    `local_model` 非空 → 进程内 transformers；否则走 HTTP（bge-m3@8004）。
    """
    import asyncio as _aio

    print(f"    编码 {len(corpus)} 块语料…")
    if local_model:
        corpus_emb = await _aio.to_thread(
            embed_local, local_model, [c["content"] for c in corpus]
        )
    else:
        corpus_emb = await embed_texts(url, [c["content"] for c in corpus])
    if len(corpus_emb) != len(corpus):
        return {"error": f"corpus 嵌入数不匹配 {len(corpus_emb)} != {len(corpus)}"}

    print(f"    编码 {len(queries)} 条 query…")
    if local_model:
        q_emb = await _aio.to_thread(
            lambda m, t: embed_local(m, t, is_query=True),
            local_model,
            [q["query_text"] for q in queries],
        )
    else:
        q_emb = await embed_texts(url, [q["query_text"] for q in queries])
    if len(q_emb) != len(queries):
        return {"error": f"query 嵌入数不匹配 {len(q_emb)} != {len(queries)}"}

    hits = {1: 0, 5: 0, 10: 0}
    mrr = 0.0
    for q, qe in zip(queries, q_emb, strict=False):
        gold = q.get("gold_source_doc_id") or q.get("source_doc_id")
        sims = [(cosine(qe, ce), c) for ce, c in zip(corpus_emb, corpus, strict=False)]
        sims.sort(key=lambda x: -x[0])
        for k in (1, 5, 10):
            if gold in [c["source_doc_id"] for _, c in sims[:k]]:
                hits[k] += 1
        topk_docs = [c["source_doc_id"] for _, c in sims[:topk]]
        if gold in topk_docs:
            rank = topk_docs.index(gold) + 1
            mrr += 1.0 / rank
    n = len(queries)
    return {
        "n_queries": n,
        "corpus_chunks": len(corpus),
        "dim": len(corpus_emb[0]) if corpus_emb else 0,
        "hit_at_1": round(hits[1] / n, 4),
        "hit_at_5": round(hits[5] / n, 4),
        "hit_at_10": round(hits[10] / n, 4),
        "mrr": round(mrr / n, 4),
    }

def verdict(base: dict, alt: dict) -> tuple[str, dict, list[str]]:
    """按拆分后的判据裁决（预注册，含"饱和时只以 MRR 判"的条款）。"""
    notes: list[str] = []
    b_h5, a_h5 = base["hit_at_5"], alt["hit_at_5"]
    b_mrr, a_mrr = base["mrr"], alt["mrr"]
    d_h5, d_mrr = round(a_h5 - b_h5, 4), round(a_mrr - b_mrr, 4)

    # hit@5 物理天花板
    headroom_h5 = round(1.0 - b_h5, 4)
    saturated = headroom_h5 < MIN_HIT5_LIFT
    if saturated:
        notes.append(
            f"基线 hit@5={b_h5:.4f} 仅剩 {headroom_h5*100:.2f}pp 空间 → "
            f"物理上不可能达 +{MIN_HIT5_LIFT*100:.0f}pp；本裁决**只以 MRR 判**"
        )

    facts = {
        "baseline": base,
        "alternative": alt,
        "delta_hit_at_5": d_h5,
        "delta_mrr": d_mrr,
        "hit5_headroom": headroom_h5,
        "hit5_saturated": saturated,
        "pre_registered": {
            "min_hit5_lift": MIN_HIT5_LIFT,
            "min_mrr_lift": MIN_MRR_LIFT,
            "saturation_rule": "hit@5 空间 < 2pp 时只以 MRR 判",
        },
    }

    if a_h5 < b_h5 - 0.005 and a_mrr < b_mrr - 0.005:
        return "REGRESSED", facts, [*notes, "两指标都回退 → 不换"]
    if saturated:
        if d_mrr >= MIN_MRR_LIFT:
            return "IMPROVED", facts, notes
        return "NO_IMPROVEMENT", facts, [*notes, f"MRR 提升 {d_mrr * 100:.2f}pp < 2pp"]
    if d_h5 >= MIN_HIT5_LIFT or d_mrr >= MIN_MRR_LIFT:
        return "IMPROVED", facts, notes
    return "NO_IMPROVEMENT", facts, [*notes, f"hit@5 +{d_h5 * 100:.2f}pp / MRR +{d_mrr * 100:.2f}pp，均未达 2pp"]


async def main(
    alt_url: str | None, alt_name: str, report: str | None, alt_model: str | None = None
) -> int:
    if not GOLDEN.is_file():
        print(f"[FATAL] golden 集不存在：{GOLDEN}")
        return 2
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    # 该文件是 {"kb_version":..., "queries":[...], "corpus":[...]} 形态
    queries = data.get("queries") or []
    kb_version = data.get("kb_version") or "kb_v1.3.0"
    if not queries:
        print("[FATAL] golden 里没有 queries")
        return 2
    print(f"golden: {len(queries)} query / kb={kb_version}")

    corpus = await load_corpus(kb_version)
    print(f"corpus: {len(corpus)} 块")
    if not corpus:
        print("[FATAL] 语料为空")
        return 2

    print("\n=== 基线复现（bge-m3-medical @ 8004）===")
    base = await eval_encoder(BGE_URL, corpus, queries)
    if "error" in base:
        print(f"  [FATAL] {base['error']}")
        return 2
    print(
        f"  hit@1={base['hit_at_1']} hit@5={base['hit_at_5']} "
        f"hit@10={base['hit_at_10']} MRR={base['mrr']}"
    )

    # 与历史基线对照（验证可复现性）
    if BASELINE_JSON.is_file():
        hist = json.loads(BASELINE_JSON.read_text(encoding="utf-8"))
        same = (
            abs(hist.get("hit_at_5", 0) - base["hit_at_5"]) < 0.005
            and abs(hist.get("mrr", 0) - base["mrr"]) < 0.005
        )
        print(
            f"  历史基线: hit@5={hist.get('hit_at_5')} MRR={hist.get('mrr')} "
            f"→ {'✅ 可复现' if same else '⚠️ 不可复现（结论需谨慎）'}"
        )

    if not alt_url and not alt_model:
        print(
            "[NOT_RUN_NO_ENCODER] 未提供对端编码器。",
            "这不是失败，是如实记录：没有对端编码器时不许谎报已对比。",
            "两种提供方式：",
            "  1) --alt-model ~/uai/models/qwen3-embedding-0.6b （进程内 transformers，推荐）",
            "  2) --alt-url http://127.0.0.1:8014 （HTTP 服务）",
        )
        payload = {
            "probe": "p69_embedding_swap_ab",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "baseline": base,
            "alternative": None,
            "verdict": "NOT_RUN_NO_ENCODER",
            "note": "基线已复现；对端编码器未部署，不做假对比",
            "saturation_warning": (
                f"hit@5={base['hit_at_5']} 距 1.0 仅 {round((1-base['hit_at_5'])*100,2)}pp，"
                f"'+2pp 绝对'判据对 hit@5 不可达；只能以 MRR（当前 {base['mrr']}）判"
            ),
        }
        if report:
            rp = pathlib.Path(report).expanduser()
            rp.parent.mkdir(parents=True, exist_ok=True)
            rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\n报告：{rp}")
        return 0

    if alt_model:
        print(f"\n=== 对端编码器（{alt_name} @ 进程内 {alt_model}）===")
        alt = await eval_encoder(None, corpus, queries, local_model=alt_model)
    else:
        print(f"\n=== 对端编码器（{alt_name} @ {alt_url}）===")
        alt = await eval_encoder(alt_url, corpus, queries)
    if "error" in alt:
        print(f"  [FATAL] {alt['error']}")
        return 2
    print(
        f"  hit@1={alt['hit_at_1']} hit@5={alt['hit_at_5']} "
        f"hit@10={alt['hit_at_10']} MRR={alt['mrr']}"
    )

    v, facts, notes = verdict(base, alt)
    print(f"\n== 裁决：{v} ==")
    for n in notes:
        print(f"  {n}")
    print(f"  Δhit@5 = {facts['delta_hit_at_5']*100:+.2f}pp  ΔMRR = {facts['delta_mrr']*100:+.2f}pp")

    payload = {
        "probe": "p69_embedding_swap_ab",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "encoder_baseline": "bge-m3-medical(8004)",
        "encoder_alternative": alt_name,
        "verdict": v,
        "verdict_notes": notes,
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
    ap.add_argument("--alt-url", default=None, help="对端嵌入服务 URL（不提供则只复现基线）")
    ap.add_argument("--alt-name", default="qwen3-emb-0.6b")
    ap.add_argument(
        "--alt-model",
        default=None,
        help="本地模型目录（进程内 transformers，如 ~/uai/models/qwen3-embedding-0.6b）",
    )
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/embedding_swap_ab.json"),
    )
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.alt_url, a.alt_name, a.report, a.alt_model)))
