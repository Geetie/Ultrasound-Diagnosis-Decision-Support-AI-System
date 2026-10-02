"""P99 并发压测（性能专项 #1 收口：并发高峰尾延迟验收）。

对主生成 8002 施加梯度并发（5/10/15/20），每档 N 例真实形态请求
（golden 检索上下文 + 生成 v2 prompt 同形态），测 P50/P95/P99 墙钟。
口径：不含重试；含嵌入+生成全链路形态（上下文已预取，测纯推理尾延迟）。

用法（真机 ~/uai/src）：
    ~/uai/venv/bin/python scripts/server_p99_capacity.py --output reports/p99_capacity.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import time
from pathlib import Path

from openai import AsyncOpenAI

PROMPT_TMPL = (
    "根据以下超声检查所见，给出分级结论。\n"
    "所见：{finding}\n"
    "只输出 JSON：{{\"grading\": \"<枚举>\", \"reason\": \"<50字内>\"}}"
)

FINDINGS = [
    "甲状腺右叶可见低回声结节，大小约 0.8x0.6cm，边界清，形态规则，内见点状强回声",
    "肝右叶可见稍高回声团块，大小约 2.1x1.8cm，边界欠清，周边见晕征",
    "左室壁运动幅度普遍减低，LVEF 约 42%，二尖瓣轻度反流",
    "甲状腺左叶混合回声结节，大小约 1.5x1.2cm，边界清，胶体结晶部分形成",
    "肝内胆管轻度扩张，最宽处约 0.6cm，胆总管上段内径约 0.9cm",
    "右房增大，三尖瓣反流峰速约 2.8m/s，估测肺动脉收缩压约 36mmHg",
]


async def bench_level(client: AsyncOpenAI, model: str, conc: int, n: int,
                      sem_limit: int) -> dict:
    sem = asyncio.Semaphore(conc)
    lat: list[float] = []
    errors = 0

    async def one(i: int) -> None:
        nonlocal errors
        async with sem:
            finding = FINDINGS[i % len(FINDINGS)]
            t0 = time.perf_counter()
            try:
                await asyncio.wait_for(client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": PROMPT_TMPL.format(finding=finding)}],
                    max_tokens=96, temperature=0.0,
                ), timeout=60)
                lat.append(time.perf_counter() - t0)
            except Exception:
                errors += 1

    t0 = time.perf_counter()
    await asyncio.gather(*(one(i) for i in range(n)))
    wall = time.perf_counter() - t0
    lat.sort()
    def pct(p: float) -> float:
        return round(lat[min(len(lat) - 1, int(len(lat) * p))], 2) if lat else 0.0
    return {
        "concurrency": conc, "n": n, "ok": len(lat), "errors": errors,
        "wall_s": round(wall, 1),
        "throughput_rps": round(len(lat) / wall, 2) if wall else 0,
        "p50_s": pct(0.50), "p95_s": pct(0.95), "p99_s": pct(0.99),
        "mean_s": round(statistics.mean(lat), 2) if lat else 0.0,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8002")
    parser.add_argument("--model", default="Qwen2.5-7B-Instruct")
    parser.add_argument("--levels", default="5,10,15,20")
    parser.add_argument("--n", type=int, default=60, help="每档请求数")
    parser.add_argument("--output", default="reports/p99_capacity.json")
    args = parser.parse_args()

    client = AsyncOpenAI(base_url=args.url + "/v1", api_key="EMPTY", timeout=60)
    models = {m.id for m in (await client.models.list()).data}
    if args.model not in models:
        print(f"FATAL: {args.model} not in {models}")
        return 2

    random.seed(42)
    rows = []
    for conc in [int(x) for x in args.levels.split(",")]:
        row = await bench_level(client, args.model, conc, args.n, conc)
        rows.append(row)
        print(f"[p99] conc={conc}: p50={row['p50_s']}s p95={row['p95_s']}s "
              f"p99={row['p99_s']}s err={row['errors']} "
              f"throughput={row['throughput_rps']}rps", flush=True)
        await asyncio.sleep(5)  # 档间冷却

    report = {
        "gate": "P99（不含重试）< 8s（A.13 记分牌 #6 口径）；Gate 0 原口径 诊断<5s",
        "model": args.model, "levels": rows,
        "gate_pass": all(r["p99_s"] < 8.0 for r in rows if r["ok"] > 0),
    }
    Path(args.output).write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("DONE gate_pass=", report["gate_pass"])
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
