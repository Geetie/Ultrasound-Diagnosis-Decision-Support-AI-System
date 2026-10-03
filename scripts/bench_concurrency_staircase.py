"""并发与性能审查 · 阶梯压测（2026-10-03）。

对**主服务 18000 的诊断全链路**施加阶梯并发（默认 1/5/10/20/50），每档记录：
提交延迟 P50/P95/P99、端到端（提交→终态）P50/P95/P99、HTTP 错误率、429 限流
率、队列积压（Redis pending_queue / DLQ 长度）、各终态分布。

口径说明（**必读，否则会误读数据**）
--------------------------------
1. 本脚本走**公开入口** ``POST /api/v1/diagnosis/request``。该端点有端点内限流
   （``RATE_LIMITS["/api/v1/diagnosis/request"]`` = capacity 10 / refill 10/60，
   口径：每分钟 10 次）。因此 ≥20 档会出现 429 —— 这是**设计内的过载保护**，
   不是缺陷；报告里单列 ``rejected_429``，并只用**被接受**的样本算管线时延。
2. 真正想看的信号是：被接受的请求在更高档位下**端到端时延是否膨胀**。
   管线 worker 池（``int_worker_pool_size``，.env 可调）是串行瓶颈，
   排队会让 e2e 明显拉长 —— 这正是并发缺陷的暴露点。
3. 每档之间冷却 ``--cooldown`` 秒（默认 30），避免上一档的残留任务污染下一档。
4. 压测期间**不停 8002**（主生成），以保持链路真实性。

用法（真机 ~/uai/src）::

    ~/uai/venv/bin/python scripts/bench_concurrency_staircase.py \
        --levels 1,5,10,20,50 --output reports/concurrency_perf_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from pathlib import Path

import httpx

API = "http://127.0.0.1:18000"
USERNAME = "smoke-doctor"
PW_FILE = Path(os.path.expanduser("~/uai/.smoke_pw"))
SESSION_ID = "smoke-session-0001"
REDIS_HOST, REDIS_PORT = "127.0.0.1", 16379
QUEUE_KEY = "ultrasound:diagnosis:pending_queue"
DLQ_KEY = "ultrasound:diagnosis:dlq"

# 自造文本（非真实患者数据）
CLINICAL_TEXTS = [
    "自造测试文本：甲状腺右叶低回声结节，大小约 0.8x0.6cm，边界清，形态规则。",
    "自造测试文本：肝右叶稍高回声团块，约 2.1x1.8cm，边界欠清，周边见晕征。",
    "自造测试文本：左室壁运动幅度普遍减低，LVEF 约 42%，二尖瓣轻度反流。",
    "自造测试文本：甲状腺左叶混合回声结节，约 1.5x1.2cm，内见胶体结晶。",
    "自造测试文本：肝内胆管轻度扩张，最宽约 0.6cm，胆总管上段内径约 0.9cm。",
]
TERMINAL = {"completed", "failed", "timeout", "needs_review", "cancelled"}


def pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(len(ordered) * p)) - 1))
    return round(ordered[idx], 3)


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "mean_s": round(statistics.mean(values), 3),
        "p50_s": pct(values, 0.50),
        "p95_s": pct(values, 0.95),
        "p99_s": pct(values, 0.99),
        "max_s": round(max(values), 3),
    }


async def redis_queues() -> dict:
    """读队列积压深度（Redis 故障不影响压测本身，降级为 None）。"""
    try:
        import redis.asyncio as aioredis

        client = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
        try:
            return {
                "pending_queue_len": await client.llen(QUEUE_KEY),
                "dlq_len": await client.llen(DLQ_KEY),
            }
        finally:
            await client.aclose()
    except Exception as exc:  # pragma: no cover - 环境依赖
        return {"error": f"{type(exc).__name__}: {exc}"}


async def login(client: httpx.AsyncClient) -> str:
    password = PW_FILE.read_text(encoding="utf-8").strip()
    resp = await client.post(
        "/api/v1/auth/login", json={"username": USERNAME, "password": password}, timeout=30.0
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"登录未返回 access_token: {resp.text[:200]}")
    return token


async def submit(client: httpx.AsyncClient, token: str, idx: int) -> dict:
    payload = {
        "session_id": SESSION_ID,
        "image_ids": [],
        "clinical_text": CLINICAL_TEXTS[idx % len(CLINICAL_TEXTS)],
        "target_organ": "thyroid",
        # 必须显式给 conditions（省略 ≠ 空列表，省略会让 SG 走保守分支）
        "patient_context": {"age": 45, "gender": "female", "conditions": []},
    }
    t0 = time.perf_counter()
    try:
        resp = await client.post(
            "/api/v1/diagnosis/request",
            json=payload,
            headers={"Authorization": f"Bearer {token}"},
            timeout=60.0,
        )
    except Exception as exc:
        return {
            "ok": False,
            "http_status": None,
            "error": f"{type(exc).__name__}: {exc}",
            "submit_s": round(time.perf_counter() - t0, 3),
        }
    submit_s = time.perf_counter() - t0
    if resp.status_code != 202 and resp.status_code != 200:
        return {
            "ok": False,
            "http_status": resp.status_code,
            "error": resp.text[:160],
            "submit_s": round(submit_s, 3),
        }
    body = resp.json()
    return {
        "ok": True,
        "http_status": resp.status_code,
        "request_id": body.get("request_id"),
        "submit_s": round(submit_s, 3),
        "enqueue_status": body.get("status"),
        "requeued": body.get("requeued"),
    }


async def poll(client: httpx.AsyncClient, token: str, request_id: str, deadline_s: float) -> dict:
    """轮询终态；返回端到端时延与终态。"""
    t0 = time.perf_counter()
    status = None
    while time.perf_counter() - t0 < deadline_s:
        await asyncio.sleep(2.0)
        try:
            resp = await client.get(
                f"/api/v1/diagnosis/result/{request_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0,
            )
        except Exception:
            continue
        if resp.status_code != 200:
            continue
        status = resp.json().get("status")
        if status in TERMINAL:
            break
    return {"final_status": status, "e2e_s": round(time.perf_counter() - t0, 3)}


async def run_level(client: httpx.AsyncClient, token: str, level: int, poll_deadline: float) -> dict:
    print(f"\n=== 阶梯并发 = {level} ===", flush=True)
    before = await redis_queues()
    t_wall = time.perf_counter()
    submissions = await asyncio.gather(*(submit(client, token, i) for i in range(level)))
    submit_wall = time.perf_counter() - t_wall

    accepted = [s for s in submissions if s["ok"]]
    rejected = [s for s in submissions if not s["ok"]]
    print(
        f"  提交完成：接受 {len(accepted)}/{level}，拒绝 {len(rejected)}"
        f"（429={sum(1 for s in rejected if s.get('http_status') == 429)}），"
        f"提交墙钟 {submit_wall:.2f}s",
        flush=True,
    )

    results = await asyncio.gather(
        *(
            poll(client, token, s["request_id"], poll_deadline)
            for s in accepted
        )
    )
    wall = time.perf_counter() - t_wall
    after = await redis_queues()

    e2e_all = [r["e2e_s"] for r in results if r.get("final_status") in TERMINAL]
    submit_lat = [s["submit_s"] for s in accepted]
    timeouts = [r for r in results if r.get("final_status") not in TERMINAL]
    status_hist: dict[str, int] = {}
    for r in results:
        key = str(r.get("final_status"))
        status_hist[key] = status_hist.get(key, 0) + 1

    row = {
        "concurrency": level,
        "submitted": level,
        "accepted": len(accepted),
        "rejected": len(rejected),
        "rejected_429": sum(1 for s in rejected if s.get("http_status") == 429),
        "rejected_other": [
            {"http_status": s.get("http_status"), "error": s.get("error")}
            for s in rejected
            if s.get("http_status") != 429
        ],
        "submit_latency": summarize(submit_lat),
        "submit_wall_s": round(submit_wall, 3),
        "e2e_latency_accepted": summarize(e2e_all),
        "poll_timeouts": len(timeouts),
        "final_status_hist": status_hist,
        "queue_before": before,
        "queue_after": after,
        "wall_s": round(wall, 3),
        "throughput_rps": round(len(e2e_all) / wall, 3) if wall else 0.0,
    }
    print(
        f"  端到端(仅接受样本) p50={row['e2e_latency_accepted'].get('p50_s')}s "
        f"p95={row['e2e_latency_accepted'].get('p95_s')}s "
        f"p99={row['e2e_latency_accepted'].get('p99_s')}s "
        f"超时={row['poll_timeouts']} 队列={after.get('pending_queue_len')} "
        f"终态={status_hist}",
        flush=True,
    )
    return row


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="1,5,10,20,50")
    parser.add_argument("--cooldown", type=float, default=30.0)
    parser.add_argument("--poll-deadline", type=float, default=300.0)
    parser.add_argument("--output", default="reports/concurrency_perf_report.json")
    parser.add_argument("--api", default=API)
    args = parser.parse_args()

    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(base_url=args.api, limits=limits, timeout=60.0) as client:
        token = await login(client)
        print(f"登录成功 user={USERNAME}", flush=True)

        rows = []
        for level in [int(x) for x in args.levels.split(",")]:
            rows.append(await run_level(client, token, level, args.poll_deadline))
            if level != [int(x) for x in args.levels.split(",")][-1]:
                print(f"  冷却 {args.cooldown}s …", flush=True)
                await asyncio.sleep(args.cooldown)

    report = {
        "bench": "concurrency_staircase_18000",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": args.api,
        "session_id": SESSION_ID,
        "limits": [int(x) for x in args.levels.split(",")],
        "rate_limit_note": (
            "POST /api/v1/diagnosis/request 端点限流 capacity=10 refill=10/60"
            "（每分钟 10 次），≥20 档的 429 属设计内过载保护；"
            "管线时延只用被接受样本统计"
        ),
        "levels": rows,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDONE -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
