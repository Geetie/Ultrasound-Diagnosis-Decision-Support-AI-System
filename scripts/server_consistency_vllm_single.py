"""P0-2 收尾：vLLM 单引擎 + guided decoding 的 780 例分级质量评测。

llama.cpp（原引擎 A）已弃用（CPU-only 构建），双引擎一致率口径不再适用；
改为单引擎口径：guided 格式合法率 + 分级准确率 + 时延。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter
from pathlib import Path

from app.inference.grading_schema import (
    extract_guided_grading,
    grading_json_schema,
)
from app.inference.protocols import InferenceRequest
from app.inference.vllm import VLLMEngine

# PLACEHOLDER_MAIN


async def run_all(args: argparse.Namespace) -> list[dict]:
    raw = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    print(f"cases={len(cases)} concurrency={args.concurrency}")

    engine = VLLMEngine(
        base_url=f"http://{args.vllm_host}:{args.vllm_port}",
        default_model=args.vllm_model,
        timeout_seconds=args.timeout,
    )
    await engine.health_check()
    print("vllm healthy")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []
    lock = asyncio.Lock()
    done = 0

    async def run_case(case: dict) -> None:
        nonlocal done
        async with sem:
            organ = case["organ"]
            schema = grading_json_schema(organ)
            request = InferenceRequest(
                prompt=case["prompt"],
                max_tokens=256,
                temperature=0.0,
                request_id=case["case_id"],
                extra={"guided_json": schema} if schema else {},
            )
            t0 = time.perf_counter()
            try:
                resp = await asyncio.wait_for(
                    engine.generate(request), timeout=args.timeout
                )
                text = resp.text or ""
                latency = round((time.perf_counter() - t0) * 1000, 1)
                grading = extract_guided_grading(text, organ)
                got = (
                    str(grading[0]).strip()
                    if isinstance(grading, tuple) and grading[0]
                    else ""
                )
                expected = str(case.get("expected_grading", "")).strip()
                row = {
                    "case_id": case["case_id"],
                    "organ": organ,
                    "expected": expected,
                    "got": got,
                    "match": got == expected,
                    "latency_ms": latency,
                }
            except Exception as exc:
                row = {
                    "case_id": case["case_id"],
                    "organ": organ,
                    "got": "",
                    "match": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                }
            results.append(row)
            async with lock:
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(cases)}", flush=True)

    await asyncio.gather(*(run_case(c) for c in cases))
    return results


def summarize(results: list[dict]) -> dict:
    total = len(results)
    matched = sum(1 for r in results if r["match"])
    lat = sorted(r["latency_ms"] for r in results if "latency_ms" in r)
    by_organ: dict[str, Counter] = {}
    for r in results:
        c = by_organ.setdefault(r["organ"], Counter())
        c["total"] += 1
        c["match"] += int(r["match"])
    return {
        "engine": "vllm-guided-single",
        "total": total,
        "matched": matched,
        "accuracy": round(matched / total, 4) if total else 0.0,
        "by_organ": {
            k: {"accuracy": round(c["match"] / c["total"], 4), "total": c["total"]}
            for k, c in by_organ.items()
        },
        "latency_ms_p50": lat[len(lat) // 2] if lat else 0,
        "latency_ms_p99": lat[int(len(lat) * 0.99)] if lat else 0,
        "errors": sum(1 for r in results if "error" in r),
        "results": results,
    }


async def amain() -> int:
    parser = argparse.ArgumentParser(
        description="vLLM 单引擎 780 例分级质量评测",
    )
    parser.add_argument("--cases", required=True)
    parser.add_argument("--vllm-host", default="127.0.0.1")
    parser.add_argument("--vllm-port", type=int, default=8002)
    parser.add_argument("--vllm-model", default="Qwen2.5-7B-Instruct")
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--no-guided",
        action="store_true",
        help="disable guided_json (prompt-instruction mode, matches historical baseline)",
    )
    args = parser.parse_args()

    results = await run_all(args)
    report = summarize(results)
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"accuracy={report['accuracy']:.2%} total={report['total']} "
        f"p50={report['latency_ms_p50']}ms p99={report['latency_ms_p99']}ms"
    )
    for k, v in report["by_organ"].items():
        print(f"  {k}: {v['accuracy']:.2%} (n={v['total']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
async def run_all(args: argparse.Namespace) -> list[dict]:
    raw = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cases = raw["cases"] if isinstance(raw, dict) else raw
    print(f"cases={len(cases)} concurrency={args.concurrency}")

    engine = VLLMEngine(
        base_url=f"http://{args.vllm_host}:{args.vllm_port}",
        default_model=args.vllm_model,
        timeout_seconds=args.timeout,
    )
    await engine.health_check()
    print("vllm healthy")

    sem = asyncio.Semaphore(args.concurrency)
    results: list[dict] = []
    lock = asyncio.Lock()
    done = 0

    async def run_case(case: dict) -> None:
        nonlocal done
        async with sem:
            organ = case["organ"]
            schema = grading_json_schema(organ)
            request = InferenceRequest(
                prompt=case["prompt"],
                max_tokens=256,
                temperature=0.0,
                request_id=case["case_id"],
                extra={"guided_json": schema} if schema else {},
            )
            t0 = time.perf_counter()
            try:
                resp = await asyncio.wait_for(
                    engine.generate(request), timeout=args.timeout
                )
                text = resp.text or ""
                latency = round((time.perf_counter() - t0) * 1000, 1)
                grading = extract_guided_grading(text, organ)
                got = (
                    str(grading[0]).strip()
                    if isinstance(grading, tuple) and grading[0]
                    else ""
                )
                expected = str(case.get("expected_grading", "")).strip()
                row = {
                    "case_id": case["case_id"],
                    "organ": organ,
                    "expected": expected,
                    "got": got,
                    "match": got == expected,
                    "latency_ms": latency,
                }
            except Exception as exc:
                row = {
                    "case_id": case["case_id"],
                    "organ": organ,
                    "got": "",
                    "match": False,
                    "error": f"{type(exc).__name__}: {str(exc)[:120]}",
                }
            results.append(row)
            async with lock:
                done += 1
                if done % 100 == 0:
                    print(f"  {done}/{len(cases)}", flush=True)

    await asyncio.gather(*(run_case(c) for c in cases))
    return results
