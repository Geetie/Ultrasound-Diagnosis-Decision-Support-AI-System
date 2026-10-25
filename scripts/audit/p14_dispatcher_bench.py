#!/usr/bin/env python3
"""A.50.7 E14 判读性能复测：dispatcher 耗时 P50（验收口径 P50 < 2.5s）。

真实判读 N 次，从 result.findings.timings_ms 提取 dispatcher 阶段耗时，
输出 P50/P95/分布。复测口径：A.39 拆解 dispatcher 4.5~5.0s（无优化）——
本探针只**复测现状数字**供清单决策，不是达标证明。

## A.88 改动：报告落盘（`--report`）

**为什么必须落盘**（2026-10-25 证据可复跑性核验）：本探针原先**只打 stdout**
—— `~/uai/reports/` 里连一份 p14 报告都没有，任何人想复核「dispatcher P50 1.34s」
都只能重跑真机。评审最可能要求"给我看原始报告"，而没有文件就只能口头承诺。
现在默认写 `reports/dispatcher_bench.json`（可用 `--report` 覆盖）。

## A.88 改动：P95 的口径说明（不要单独讲 P95）

N=6 时 `int(6*0.95)=5` → 取排序后第 6 个 = **最大值**。历史材料里那个
"P95 2.24s"实际是 **6 次里的 max**，不是统计意义上的 P95。本脚本现在会：
  * 样本不足（`N < 20`）时显式标注 `p95_is_effectively_max=true`；
  * 报告里同时写 `n_runs`，让读者自己判断该不该引用 P95。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))

import httpx
from _audit_common import BASE, login, pw

N_RUNS = int(os.environ.get("E14_RUNS", "6"))

#: 样本数低于此值时，P95 实际等于"最大值"，不得对外单独引用（A.88）。
MIN_RUNS_FOR_P95 = 20


async def one_run(client: httpx.AsyncClient, tok: str, pid: str, idx: int) -> dict:
    sid = ""
    r = await client.post(f"{BASE}/api/v1/sessions",
                          json={"patient_id": pid, "device_id": "probe-e14",
                                "target_organs": ["cardiac"]},
                          headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
    sid = r.json().get("session_id", "")
    t0 = time.monotonic()
    r = await client.post(f"{BASE}/api/v1/diagnosis/request",
                          json={"session_id": sid, "target_organ": "cardiac",
                                "image_ids": [],
                                "clinical_text": f"E14 复测第 {idx} 轮：室间隔与左室后壁运动尚可，未见明显节段性异常。"},
                          headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
    rid = r.json().get("request_id", "")
    # 轮询结果（processing 缓存 TTL 1s，A.39）
    timings: dict = {}
    status = ""
    while time.monotonic() - t0 < 300:
        rr = await client.get(f"{BASE}/api/v1/diagnosis/result/{rid}",
                              headers={"Authorization": f"Bearer {tok}"}, timeout=15.0)
        body = rr.json()
        status = body.get("status", "")
        if status in ("completed", "failed", "cancelled", "timeout"):
            # worker 写回的 findings 包 = 响应的 diagnosis 字段（timings_ms 在顶层）
            findings = body.get("diagnosis")
            if isinstance(findings, dict):
                timings = findings.get("timings_ms") or {}
            break
        await asyncio.sleep(1.0)
    total = round(time.monotonic() - t0, 1)
    disp = {k: v for k, v in timings.items() if "dispatch" in k.lower()}
    return {"idx": idx, "status": status, "wall_s": total,
            "timings_ms": timings, "dispatcher_ms": disp}


async def main(report_path: str | None = None) -> int:
    doctor = login("smoke-doctor", pw("smoke_pw"))
    tok = doctor["access_token"]
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    pid = f"probe-e14-{int(time.time())}"
    async with httpx.AsyncClient() as client:
        await client.post(f"{BASE}/fhir/Patient",
                          json={"patient_id": pid, "name": "AUDIT E14 BENCH", "gender": "unknown"},
                          headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
        await client.post(f"{BASE}/api/v1/consent",
                          json={"patient_id": pid, "action": "grant", "operator_id": "smoke-admin"},
                          headers={"Authorization": f"Bearer {admin['access_token']}"}, timeout=30.0)
        # 预热一轮（LLM/嵌入热态后再计——与 A.39 拆解口径一致：热态数字）
        await one_run(client, tok, pid, 0)
        runs = [await one_run(client, tok, pid, i + 1) for i in range(N_RUNS)]

    print(f"== E14 判读性能复测（N={N_RUNS}，热态） ==")
    dispatch_vals: list[float] = []
    wall_vals: list[float] = []
    for r in runs:
        print(f"  run{r['idx']}: status={r['status']} wall={r['wall_s']}s dispatcher={r['dispatcher_ms']}")
        wall_vals.append(r["wall_s"])
        for v in r["dispatcher_ms"].values():
            if isinstance(v, (int, float)) and v > 0:
                dispatch_vals.append(float(v))
    ok_completed = sum(1 for r in runs if r["status"] == "completed")

    # A.88：P95 口径必须写明"是否只是最大值"（样本 6 时它就是 max）。
    p50 = p95 = None
    if dispatch_vals:
        dispatch_vals.sort()
        p50 = statistics.median(dispatch_vals)
        p95 = dispatch_vals[min(len(dispatch_vals) - 1, int(len(dispatch_vals) * 0.95))]
        print(f"  dispatcher P50={p50 / 1000:.2f}s P95={p95 / 1000:.2f}s（验收线 P50<2.5s："
              f"{'PASS' if p50 < 2500 else 'NOT MET — 现状数字供清单'}）")
    if wall_vals:
        print(f"  端到端 wall P50={statistics.median(wall_vals)}s completed={ok_completed}/{N_RUNS}")

    n = len(dispatch_vals)
    p95_is_max = n < MIN_RUNS_FOR_P95
    if p95_is_max and p95 is not None:
        print(f"  ⚠️ 样本 n={n} < {MIN_RUNS_FOR_P95}：此处 P95 实际等于**最大值**"
              f"（{p95 / 1000:.2f}s），不得单独对外引用；对外只讲 P50。")

    # A.88：报告落盘（原先只打 stdout → 「可复跑」无法取证）。
    if report_path:
        payload = {
            "probe": "p14_dispatcher_bench",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "base_url": BASE,
            "n_runs": n,
            "warmup_runs": 1,
            "completed": ok_completed,
            "dispatcher_p50_ms": round(p50, 2) if p50 is not None else None,
            "dispatcher_p95_ms": round(p95, 2) if p95 is not None else None,
            # 口径护栏：true 时 P95 只是"样本最大值"，不得单独引用
            "p95_is_effectively_max": p95_is_max,
            "min_runs_for_p95": MIN_RUNS_FOR_P95,
            "wall_p50_s": statistics.median(wall_vals) if wall_vals else None,
            "runs": runs,
        }
        os.makedirs(os.path.dirname(report_path) or ".", exist_ok=True)
        with open(report_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        print(f"  报告已写入：{report_path}")

    return 0 if ok_completed == N_RUNS else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="E14 dispatcher 性能复测（A.50.7 / A.88 报告落盘）")
    parser.add_argument(
        "--report",
        default=os.path.join("reports", "dispatcher_bench.json"),
        help="报告输出路径（默认 reports/dispatcher_bench.json）",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.report)))
