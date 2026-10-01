import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/mnt/ee16/home/student03/uai/src")
sys.path.insert(0, "/mnt/ee16/home/student03/uai/src/scripts")

import httpx
from openai import AsyncOpenAI
from t1_eval_faithfulness import (
    _GENERATION_SYSTEM,
    _GENERATION_USER_TMPL,
    _extract_json,
    _filter_meta_statements,
    _is_refusal,
    _render_nli_prompt,
    _render_statement_prompt,
    heuristic_numeric_check,
)
from t1_eval_retrieval import bm25_topk, cosine_topk, embed_texts, rrf_fuse

GEN_URL = "http://127.0.0.1:8002"
JUDGE_URL = "http://127.0.0.1:8006"
GEN_MODEL = "Qwen2.5-7B-Instruct"
JUDGE_MODEL = "Qwen3-8B"
N = 40
CONC = 16
TOP_K = 5
CALL_TIMEOUT = 30


async def chat(client, url, model, messages, max_tokens=256):
    r = await client.post(url + "/v1/chat/completions",
        json={"model": model, "messages": messages, "temperature": 0.0,
              "max_tokens": max_tokens}, timeout=CALL_TIMEOUT)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


async def eval_one(gen, judge, sem, qi, q, corpus, corpus_texts, fused,
                   results, lock, done, err_streak, last_err):
    async with sem:
        top5 = fused[qi][:TOP_K]
        context = "\n\n".join(corpus_texts[i] for i in top5)
        item = {"query_id": q["query_id"], "question": q["query_text"],
                "context_chunk_ids": [corpus[i]["chunk_id"] for i in top5]}
        try:
            answer = await chat(gen, GEN_URL, GEN_MODEL, [
                {"role": "system", "content": _GENERATION_SYSTEM},
                {"role": "user", "content": _GENERATION_USER_TMPL.format(
                    context=context, question=q["query_text"])}], max_tokens=512)
            item["answer"] = answer
            stmt_out = _extract_json(await chat(judge, JUDGE_URL, JUDGE_MODEL, [
                {"role": "user", "content": _render_statement_prompt(q["query_text"], answer)}]))
            statements = _filter_meta_statements(
                stmt_out.get("statements", []) if isinstance(stmt_out, dict) else [])
            item["num_statements"] = len(statements)
            if _is_refusal(answer) or not statements:
                item["faithfulness"] = 1.0
                item["no_statements"] = True
                if _is_refusal(answer):
                    item["refusal"] = True
            else:
                nli_out = _extract_json(await chat(judge, JUDGE_URL, JUDGE_MODEL, [
                    {"role": "user", "content": _render_nli_prompt(context, statements)}],
                    max_tokens=256))
                verdicts = nli_out.get("statements", []) if isinstance(nli_out, dict) else []
                supported = sum(1 for v in verdicts
                                if isinstance(v, dict) and v.get("verdict") == 1)
                item["faithfulness"] = supported / len(statements) if statements else 1.0
                item["verdicts"] = verdicts
            ok, missing = heuristic_numeric_check(answer, context)
            item["numeric_consistent"] = ok
            item["missing_numbers"] = missing
        except Exception:
            import traceback
            item["error"] = traceback.format_exc()[-400:]
        async with lock:
            done[0] += 1
            if "error" in item:
                etype = item["error"].splitlines()[-1][:60]
                err_streak[0] = err_streak[0] + 1 if etype == last_err[0] else 1
                last_err[0] = etype
                if err_streak[0] >= 3:
                    print(f"FATAL: 3 consecutive same errors ({etype}), aborting.", flush=True)
                    print(f"FATAL last error: {item['error'][:300]}", flush=True)
                    import os
                    os._exit(2)
            else:
                err_streak[0], last_err[0] = 0, ""
            print(f"  {done[0]}/{N}", flush=True)
        results.append(item)


async def main():
    gen = AsyncOpenAI(base_url=GEN_URL + "/v1", api_key="EMPTY", timeout=CALL_TIMEOUT)
    judge = AsyncOpenAI(base_url=JUDGE_URL + "/v1", api_key="EMPTY", timeout=CALL_TIMEOUT)
    gen_models = {m.id for m in (await gen.models.list()).data}
    judge_models = {m.id for m in (await judge.models.list()).data}
    print("vllm gen models:", sorted(gen_models), "judge models:", sorted(judge_models), flush=True)
    if GEN_MODEL not in gen_models:
        print(f"FATAL: {GEN_MODEL} not found on {GEN_URL}.", flush=True)
        raise SystemExit(2)
    if JUDGE_MODEL not in judge_models:
        print(f"FATAL: {JUDGE_MODEL} not found on {JUDGE_URL}.", flush=True)
        raise SystemExit(2)

    raw = json.loads(Path("/mnt/ee16/home/student03/uai/src/data/golden/kb_qa_golden_v1.json").read_text(encoding="utf-8"))
    corpus = raw["corpus"]
    queries = raw["queries"][:N]
    corpus_texts = [c["text"] for c in corpus]
    query_texts = [q["query_text"] for q in queries]
    print("embedding...", flush=True)
    corpus_vecs = embed_texts(corpus_texts, "http://127.0.0.1:8004")
    query_vecs = embed_texts(query_texts, "http://127.0.0.1:8004")
    dense_pairs = [[(i, 1.0) for i, _ in h] for h in cosine_topk(query_vecs, corpus_vecs, 100)]
    fused_raw = rrf_fuse(dense_pairs, bm25_topk(query_texts, corpus_texts, 100), 100, k_const=60)
    fused = [[i for i, _s in ranking] for ranking in fused_raw]
    gen_client = httpx.AsyncClient(timeout=CALL_TIMEOUT)
    judge_client = httpx.AsyncClient(timeout=CALL_TIMEOUT)
    sem = asyncio.Semaphore(CONC)
    results, lock, done = [], asyncio.Lock(), [0]
    err_streak, last_err = [0], [""]
    t0 = time.perf_counter()
    await asyncio.gather(*(eval_one(
        gen_client, judge_client, sem, i, q, corpus, corpus_texts, fused,
        results, lock, done, err_streak, last_err) for i, q in enumerate(queries)))
    elapsed = time.perf_counter() - t0
    await gen_client.aclose()
    await judge_client.aclose()

    scored = [r for r in results if "faithfulness" in r]
    failed = [r for r in results if "error" in r]
    scores = [r["faithfulness"] for r in scored]
    if scored and len(failed) / len(results) > 0.3:
        print(f"FATAL: failure rate {len(failed)}/{len(results)} > 30%, see errors above", flush=True)
        raise SystemExit(1)
    report = {"sample": N, "scored": len(scored), "failed": len(failed),
        "methodology": "RAGAS faithfulness (official prompts) + asyncio concurrent",
        "mean_faithfulness": sum(scores) / len(scores) if scores else None,
        "low_scores": sorted([(r["query_id"], round(r["faithfulness"], 2)) for r in scored if r["faithfulness"] < 0.8]),
        "total_latency_s": round(elapsed, 1), "results": results}
    out = Path("/mnt/ee16/home/student03/uai/src/reports/t1_faith_qwen3judge.json")
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE mean=", report["mean_faithfulness"], "scored=", len(scored),
          "elapsed=", round(elapsed, 1), "s", flush=True)

asyncio.run(main())
