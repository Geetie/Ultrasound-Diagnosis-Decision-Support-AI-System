
"""P0-2: vLLM single-engine 780-case grading quality eval (OpenAI SDK)."""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI

from app.inference.grading_definitions import extract_grading_with_conflict
from app.inference.grading_schema import (
    extract_guided_grading,
    grading_json_schema,
)


async def run_all(args):
    raw = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    print(f"cases={len(cases)} concurrency={args.concurrency}")
    client = AsyncOpenAI(
        base_url=f"http://{args.vllm_host}:{args.vllm_port}/v1",
        api_key="EMPTY",
        timeout=args.timeout,
    )
    models = await client.models.list()
    print("vllm healthy:", [m.id for m in models.data][:1])
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    lock = asyncio.Lock()
    done = 0

    async def run_case(case):
        nonlocal done
        async with sem:
            organ = case["organ"]
            schema = grading_json_schema(organ) if not args.no_guided else None
            t0 = time.perf_counter()
            try:
                kwargs = dict(
                    model=args.vllm_model,
                    messages=[{"role": "user", "content": case["prompt"]}],
                    max_tokens=64,
                    temperature=0.0,
                )
                if schema:
                    kwargs["response_format"] = {
                        "type": "json_schema",
                        "json_schema": {"name": "grading", "schema": schema},
                    }
                resp = await asyncio.wait_for(
                    client.chat.completions.create(**kwargs), timeout=args.timeout
                )
                text = resp.choices[0].message.content or ""
                latency = round((time.perf_counter() - t0) * 1000, 1)
                grading = extract_guided_grading(text, organ)
                if not grading:
                    conflict = extract_grading_with_conflict(text, organ)
                    grading = conflict[0] if conflict else None
                got = str(grading).strip() if grading else ""
                expected = str(case.get("expected_grading", "")).strip()
                row = {"case_id": case["case_id"], "organ": organ, "expected": expected, "got": got, "match": got == expected, "latency_ms": latency}
            except Exception as exc:
                row = {"case_id": case["case_id"], "organ": organ, "got": "", "match": False, "error": type(exc).__name__ + ": " + str(exc)[:120]}
            results.append(row)
            async with lock:
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(cases)}", flush=True)

    await asyncio.gather(*(run_case(c) for c in cases))
    return results


def summarize(results):
    total = len(results)
    matched = sum(1 for r in results if r["match"])
    lat = sorted(r["latency_ms"] for r in results if "latency_ms" in r)
    by_organ = {}
    for r in results:
        c = by_organ.setdefault(r["organ"], Counter())
        c["total"] += 1
        c["match"] += int(r["match"])
    return {
        "engine": "vllm-official-openai",
        "total": total,
        "matched": matched,
        "accuracy": round(matched / total, 4) if total else 0.0,
        "by_organ": {k: {"accuracy": round(c["match"] / c["total"], 4), "total": c["total"]} for k, c in by_organ.items()},
        "latency_ms_p50": lat[len(lat) // 2] if lat else 0,
        "latency_ms_p99": lat[int(len(lat) * 0.99)] if lat else 0,
        "errors": sum(1 for r in results if "error" in r),
        "results": results,
    }


async def amain():
    parser = argparse.ArgumentParser(description="780-case grading eval via OpenAI SDK")
    parser.add_argument("--cases", required=True)
    parser.add_argument("--vllm-host", default="127.0.0.1")
    parser.add_argument("--vllm-port", type=int, default=8002)
    parser.add_argument("--vllm-model", default="Qwen2.5-7B-Instruct")
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", required=True)
    parser.add_argument("--no-guided", action="store_true")
    args = parser.parse_args()
    results = await run_all(args)
    report = summarize(results)
    Path(args.output).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"accuracy={report[chr(97)+chr(99)+chr(99)+chr(117)+chr(114)+chr(97)+chr(99)+chr(121)]:.2%} errors={report[chr(101)+chr(114)+chr(114)+chr(111)+chr(114)+chr(115)]} p50={report[chr(108)+chr(97)+chr(116)+chr(101)+chr(110)+chr(99)+chr(121)+chr(95)+chr(109)+chr(115)+chr(95)+chr(112)+chr(53)+chr(48)]}ms")
    for k, v in report["by_organ"].items():
        print(f"  {k}: {v[chr(97)+chr(99)+chr(99)+chr(117)+chr(114)+chr(97)+chr(99)+chr(121)]:.2%} n={v[chr(116)+chr(111)+chr(116)+chr(97)+chr(108)]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
