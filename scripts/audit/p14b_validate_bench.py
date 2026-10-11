#!/usr/bin/env python3
"""A.51 D 项前置：fusion.validate 时延基准复测（先量后改）。

口径：真实判读 N 轮（含 1 轮预热），从 findings.timings_ms 提取
`fusion.validate_ms`（LayeredValidator 全层校验，Self-RAG LLM 在其中，
Layer2/3 已 asyncio.gather 并行）、`fusion.fuse_ms`、`pipeline.total_ms`。
验收线：validate P99 < 1.5s（21 号路线图「fusion-validate 降载」）。

先落现状数字，再决定是否接线孤儿预算 pipeline_fusion_budget_s（超时 →
needs_review 优雅降级）。本探针只测，不改任何状态。

运行（真机）：~/uai/venv/bin/python scripts/audit/p14b_validate_bench.py
环境变量：VALIDATE_RUNS（默认 8）
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import statistics
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from _audit_common import BASE, login, pw, summary
from p14_dispatcher_bench import one_run

N_RUNS = int(os.environ.get("VALIDATE_RUNS", "8"))
TARGET_P99_MS = 1500.0


def _pct(vals: list[float], q: float) -> float:
    if not vals:
        return 0.0
    ordered = sorted(vals)
    idx = min(len(ordered) - 1, max(0, round(q * (len(ordered) - 1))))
    return ordered[idx]


async def main() -> int:
    doctor = login("smoke-doctor", pw("smoke_pw"))
    tok = doctor["access_token"]
    admin = login("smoke-admin", pw("smoke_admin_pw"))
    pid = f"probe-validate-{int(time.time())}"
    import httpx

    async with httpx.AsyncClient() as client:
        await client.post(f"{BASE}/fhir/Patient",
                          json={"patient_id": pid, "name": "AUDIT VALIDATE BENCH",
                                "gender": "unknown"},
                          headers={"Authorization": f"Bearer {tok}"}, timeout=30.0)
        await client.post(f"{BASE}/api/v1/consent",
                          json={"patient_id": pid, "action": "grant",
                                "operator_id": "smoke-admin"},
                          headers={"Authorization": f"Bearer {admin['access_token']}"},
                          timeout=30.0)
        await one_run(client, tok, pid, 0)  # 预热（热态口径）
        runs = [await one_run(client, tok, pid, i + 1) for i in range(N_RUNS)]

    print(f"== fusion.validate 时延基准（N={N_RUNS}，热态）==")
    validate_vals: list[float] = []
    fuse_vals: list[float] = []
    total_vals: list[float] = []
    rows: list[dict] = []
    for r in runs:
        tm = r["timings_ms"] or {}
        v = tm.get("fusion.validate_ms")
        f = tm.get("fusion.fuse_ms")
        t = tm.get("pipeline.total_ms")
        rows.append({"idx": r["idx"], "status": r["status"], "wall_s": r["wall_s"],
                     "validate_ms": v, "fuse_ms": f, "total_ms": t,
                     "validate_degraded": tm.get("fusion.validate_degraded")})
        print(f"  run{r['idx']}: status={r['status']} wall={r['wall_s']}s "
              f"validate={v} fuse={f} total={t} degraded={tm.get('fusion.validate_degraded')}")
        if isinstance(v, (int, float)):
            validate_vals.append(float(v))
        if isinstance(f, (int, float)):
            fuse_vals.append(float(f))
        if isinstance(t, (int, float)):
            total_vals.append(float(t))

    completed = sum(1 for r in runs if r["status"] == "completed")
    stats: dict = {"n": len(validate_vals), "completed": completed}
    if validate_vals:
        stats.update({
            "p50_ms": round(statistics.median(validate_vals), 1),
            "p95_ms": round(_pct(validate_vals, 0.95), 1),
            "p99_ms": round(_pct(validate_vals, 0.99), 1),
            "max_ms": round(max(validate_vals), 1),
            "min_ms": round(min(validate_vals), 1),
        })
        verdict = "PASS" if stats["p99_ms"] < TARGET_P99_MS else "NOT_MET"
        print(f"  validate: P50={stats['p50_ms']}ms P95={stats['p95_ms']}ms "
              f"P99={stats['p99_ms']}ms（验收 <{TARGET_P99_MS}ms：{verdict}）")
    if fuse_vals:
        stats["fuse_p50_ms"] = round(statistics.median(fuse_vals), 1)
    if total_vals:
        stats["total_p50_ms"] = round(statistics.median(total_vals), 1)

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "n_runs": N_RUNS,
        "target_p99_ms": TARGET_P99_MS,
        "verdict": "PASS" if validate_vals and stats.get("p99_ms", 1e9) < TARGET_P99_MS else "NOT_MET",
        "stats": stats,
        "runs": rows,
    }
    out_path = pathlib.Path.home() / "uai" / "reports" / (
        f"validate_bench_{time.strftime('%Y%m%d_%H%M%S')}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  -> {out_path}")
    return summary("A.51 fusion-validate 基准")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
