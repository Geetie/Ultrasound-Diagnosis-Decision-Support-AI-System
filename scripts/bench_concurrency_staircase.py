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
    """读队列积压深度（Redis 故障不影响压测本身，降级为含 error 的 dict）。

    连接参数**从 app.settings 取**（而不是硬编码）：容器 Redis 带密码且
    key 前缀由 ``redis_key_prefix`` 决定，硬编码会得到"连不上/长度恒为 0"的
    假数据——审查报告里这类假数据比没有数据更糟。
    """
    try:
        import redis.asyncio as aioredis

        from app.config.redis_keys import RedisKey
        from app.config.settings import settings

        # 连接参数一律取 settings.redis_url（含端口/库号/密码），不硬编码——
        # 审计报告里"队列长度恒为 0"这类假数据比没有数据更糟。
        client = aioredis.from_url(settings.redis_url, decode_responses=True)
        try:
            return {
                "pending_queue_len": await client.llen(RedisKey.DIAGNOSIS_PENDING_QUEUE),
                "dlq_len": await client.llen(RedisKey.DIAGNOSIS_DLQ),
            }
        finally:
            await client.aclose()
    except Exception as exc:  # pragma: no cover - 环境依赖
        return {"error": f"{type(exc).__name__}: {exc}"}


async def login(client: httpx.AsyncClient, username: str, password: str) -> str:
    """登录取 token；遇 429 按 Retry-After 退避重试。

    登录端点限流是 5/min 且无 token 时桶按 client_ip 聚合（审查报告 P1-3：
    同一出口 IP 的多名医生会被当成一个人），所以 50 个账号一次性并发登录必然
    吃 429——这本身就是被审查的缺陷，脚本侧只能退避规避。
    """
    last_status = None
    for attempt in range(8):
        resp = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}, timeout=60.0
        )
        if resp.status_code == 429:
            last_status = 429
            wait = float(resp.headers.get("Retry-After", "13"))
            await asyncio.sleep(max(wait, 13.0))
            continue
        resp.raise_for_status()
        token = resp.json().get("access_token")
        if not token:
            raise RuntimeError(f"登录未返回 access_token: {resp.text[:200]}")
        return token
    raise RuntimeError(f"登录持续 429（{username}, last={last_status}）")


async def ensure_users(count: int, password: str) -> list[str]:
    """预建 ``count`` 个压测医生账号（幂等）。

    为什么需要多用户：``POST /api/v1/diagnosis/request`` 的限流桶是
    **(client_ip, user_id)** 级，容量 10/60s。单用户打 50 并发只会被限流挡在
    门口，测不到管线；"多名医生同时使用"的真实形态是**多用户各提交 1 条**，
    每个桶都有独立额度——这才是本审查要测的并发面。
    口令哈希在进程内算好，只把哈希写库（明文不进命令行/日志）。
    """
    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings
    from app.security.auth import get_password_hash

    hashed = get_password_hash(password)
    dsn = to_asyncpg_dsn(settings.database_url)
    conn = await asyncpg.connect(dsn)
    try:
        for i in range(count):
            username = f"{USERNAME}-{i:02d}"
            await conn.execute(
                """
                INSERT INTO users (username, email, full_name, hashed_password,
                                   disabled, role, created_at, updated_at)
                VALUES ($1, $2, $3, $4, false, 'ULTRASOUND_DOCTOR', now(), now())
                ON CONFLICT (username) DO UPDATE
                  SET hashed_password = EXCLUDED.hashed_password,
                      disabled = false,
                      updated_at = now()
                """,
                username,
                f"{username}@localhost",
                f"BENCH DOCTOR {i:02d}",
                hashed,
            )
    finally:
        await conn.close()
    return [f"{USERNAME}-{i:02d}" for i in range(count)]


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


async def direct_enqueue(count: int) -> list[dict]:
    """绕过 HTTP 入口，直接把 ``count`` 条诊断灌进 worker 队列。

    为什么需要这条路径：``/api/v1/auth/login`` 限流 5/min（且无 token 时桶按
    client_ip 聚合，见审查报告 P1-3），``/api/v1/diagnosis/request`` 限流
    10/min/用户——**HTTP 口径最多只能合法打出 10 并发**，测不到 20/50 的管线
    行为。直接写库 + RPUSH 与 worker 的真实入口完全一致（worker 消费的就是这个
    队列），测的是同一段代码路径。

    前置：``diagnosis_results`` 行要先落库（管线按 request_id 读它）。
    """
    import asyncpg
    import uuid

    from app.config.dsn import to_asyncpg_dsn
    from app.config.redis_keys import RedisKey
    from app.config.settings import settings

    stamp = int(time.time())
    conn = await asyncpg.connect(to_asyncpg_dsn(settings.database_url))
    ids: list[str] = []
    try:
        for i in range(count):
            request_id = f"req_bench_{stamp}_{i:03d}_{uuid.uuid4().hex[:6]}"
            await conn.execute(
                """
                INSERT INTO diagnosis_results
                    (request_id, session_id, organ, tier, clinical_text,
                     patient_context, confidence, status, created_at)
                VALUES ($1, $2, 'thyroid', 'FULL', $3, $4::jsonb, 0.0, 'processing', now())
                """,
                request_id,
                SESSION_ID,
                CLINICAL_TEXTS[i % len(CLINICAL_TEXTS)],
                # asyncpg 无法从 dict 推断 JSONB 类型（真机报 DataError）→ 显式序列化
                json.dumps({"age": 45, "gender": "female", "conditions": []}),
            )
            ids.append(request_id)
    finally:
        await conn.close()

    import redis.asyncio as aioredis

    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.rpush(RedisKey.DIAGNOSIS_PENDING_QUEUE, *ids)
    finally:
        await client.aclose()
    t0 = time.perf_counter()
    return [
        {
            "ok": True,
            "http_status": None,
            "request_id": rid,
            "submit_s": round((time.perf_counter() - t0) / max(1, len(ids)), 4),
            "enqueue_status": "processing",
            "mode": "direct_enqueue",
        }
        for rid in ids
    ]


async def run_level(
    client: httpx.AsyncClient,
    tokens: list[str],
    level: int,
    poll_deadline: float,
    mode: str = "http",
) -> dict:
    print(f"\n=== 阶梯并发 = {level}（模式={mode}）===", flush=True)
    before = await redis_queues()
    t_wall = time.perf_counter()
    if mode == "direct":
        submissions = await direct_enqueue(level)
    else:
        # 每条请求用不同医生账号（round-robin）：模拟"多名医生同时使用"，
        # 绕开单用户 10/min 的限流桶，使测到的是管线而不是限流器。
        submissions = await asyncio.gather(
            *(submit(client, tokens[i % len(tokens)], i) for i in range(level))
        )
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
            poll(client, tokens[i % len(tokens)], s["request_id"], poll_deadline)
            for i, s in enumerate(accepted)
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
        "mode": mode,
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


def _dump(rows: list[dict], args, n_users: int, users: list[str]) -> None:
    """把当前已完成的档位写成报告文件（每档调用一次）。"""
    report = {
        "bench": "concurrency_staircase_18000",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": args.api,
        "session_id": SESSION_ID,
        "limits": [int(x) for x in args.levels.split(",")],
        "distinct_users": n_users,
        "direct_from": args.direct_from,
        "rate_limit_note": (
            "HTTP 口径：/api/v1/diagnosis/request 限流 capacity=10 refill=10/60"
            "（桶按 client_ip+user_id），/api/v1/auth/login 限流 5/min；"
            f">={args.direct_from} 并发档改走 direct_enqueue（写 diagnosis_results 行 "
            "+ RPUSH worker 队列），与 worker 真实入口同路径，绕开限流以测管线上限"
        ),
        "levels": rows,
        "partial": len(rows) < len([int(x) for x in args.levels.split(",")]),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--levels", default="1,5,10,20,50")
    parser.add_argument("--cooldown", type=float, default=30.0)
    parser.add_argument("--poll-deadline", type=float, default=300.0)
    parser.add_argument("--output", default="reports/concurrency_perf_report.json")
    parser.add_argument("--api", default=API)
    parser.add_argument("--users", type=int, default=0, help="多医生账号数（0=单用户复用一个 token）")
    parser.add_argument(
        "--direct-from",
        type=int,
        default=0,
        help="≥该并发档改走 direct_enqueue（绕过 HTTP 限流，仅用于压管线上限）",
    )
    args = parser.parse_args()

    password = PW_FILE.read_text(encoding="utf-8").strip()
    levels = [int(x) for x in args.levels.split(",")]

    limits = httpx.Limits(max_connections=64, max_keepalive_connections=32)
    async with httpx.AsyncClient(base_url=args.api, limits=limits, timeout=120.0) as client:
        if args.users > 0:
            users = await ensure_users(args.users, password)
        else:
            users = [USERNAME]
        # 登录端点自身限流 5/min（桶按 client_ip 聚合）——分批 + 间隔，避免把
        # "登录限流" 混进压测口径。
        tokens: list[str] = []
        for start in range(0, len(users), 4):
            batch = users[start : start + 4]
            tokens.extend(await asyncio.gather(*(login(client, u, password) for u in batch)))
            if start + 4 < len(users):
                await asyncio.sleep(13)
        print(f"登录成功 {len(tokens)} 个账号（{users[0]} … {users[-1]}）", flush=True)

        rows = []
        for level in levels:
            mode = "direct" if (args.direct_from and level >= args.direct_from) else "http"
            rows.append(await run_level(client, list(tokens), level, args.poll_deadline, mode))
            # 每档跑完就落盘：后续档位崩了也不丢已完成的档（同源教训：别只在最后写）
            _dump(rows, args, len(users), users)
            if level != levels[-1]:
                print(f"  冷却 {args.cooldown}s …", flush=True)
                await asyncio.sleep(args.cooldown)

    report = {
        "bench": "concurrency_staircase_18000",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "api": args.api,
        "session_id": SESSION_ID,
        "limits": levels,
        "distinct_users": len(users),
        "direct_from": args.direct_from,
        "rate_limit_note": (
            "HTTP 口径：/api/v1/diagnosis/request 限流 capacity=10 refill=10/60"
            "（桶按 client_ip+user_id），/api/v1/auth/login 限流 5/min；"
            f">={args.direct_from} 并发档改走 direct_enqueue（写 diagnosis_results "
            "行 + RPUSH worker 队列），与 worker 真实入口同路径，绕开限流以测管线上限"
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
