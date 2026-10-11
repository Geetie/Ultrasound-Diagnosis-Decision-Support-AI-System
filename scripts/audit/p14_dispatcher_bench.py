#!/usr/bin/env python3
"""A.50.7 E14 判读性能复测：dispatcher 耗时 P50（验收口径 P50 < 2.5s）。

真实判读 N 次，从 result.findings.timings_ms 提取 dispatcher 阶段耗时，
输出 P50/P95/分布。复测口径：A.39 拆解 dispatcher 4.5~5.0s（无优化）——
本探针只**复测现状数字**供清单决策，不是达标证明。
"""
from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import httpx
from _audit_common import BASE, login, pw

N_RUNS = int(os.environ.get("E14_RUNS", "6"))


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


async def main() -> int:
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
    if dispatch_vals:
        dispatch_vals.sort()
        p50 = statistics.median(dispatch_vals)
        p95 = dispatch_vals[min(len(dispatch_vals) - 1, int(len(dispatch_vals) * 0.95))]
        print(f"  dispatcher P50={p50 / 1000:.2f}s P95={p95 / 1000:.2f}s（验收线 P50<2.5s："
              f"{'PASS' if p50 < 2500 else 'NOT MET — 现状数字供清单'}）")
    if wall_vals:
        print(f"  端到端 wall P50={statistics.median(wall_vals)}s completed={ok_completed}/{N_RUNS}")
    return 0 if ok_completed == N_RUNS else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
