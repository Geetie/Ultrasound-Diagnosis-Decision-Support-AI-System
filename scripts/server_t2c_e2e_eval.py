"""T2c：US-365K caption 端到端诊断评测（真机，并发版）。

v3 重写（用户指出串行版太慢）：asyncio + 线程池并发 6，
51 例一轮 ~10 分钟（v2 串行需 ~50 分钟）。

三列对照：①输入所见（caption）②AI 诊断结论（result 顶层
patient_explanation/malignancy_risk/differential_diagnoses）
③判官裁决（vLLM 7B RAGAS NLI：AI 结论是否被输入所见支持）。

口径限制（如实声明）：caption 非金标准诊断标签，本评测测的是
"端到端链路对真实报告文本的可用性与可溯源性"，不是诊断准确率。

用法（真机 ~/uai/src）：
    python scripts/server_t2c_e2e_eval.py --output reports/t2c_e2e_report.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import random
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

BASE = "http://127.0.0.1:18000"
JUDGE = "http://127.0.0.1:8002"
JUDGE_MODEL = "Qwen2.5-7B-Instruct"
ORGAN_PAT = {
    "thyroid": re.compile(r"\bthyroid\b", re.IGNORECASE),
    "liver": re.compile(r"\b(liver|hepatic)\b", re.IGNORECASE),
    "cardiac": re.compile(r"\b(echocardiograph|cardiac|heart)\b", re.IGNORECASE),
}
_CONCURRENCY = 6
_POLL_DEADLINE = 240


def _post_json(url: str, body: dict, token: str | None = None, timeout: int = 60) -> dict:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _get_json(url: str, token: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _judge_nli(caption: str, conclusion: str) -> tuple[int, str]:
    body = json.dumps(
        {
            "model": JUDGE_MODEL,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        "Given the ultrasound finding report (context) and an "
                        "AI diagnosis conclusion (statement), judge ONLY whether "
                        "the statement is entailed by the context. Reply with a "
                        'single JSON: {"verdict": 1 or 0, "reason": "<=40 words"}\n\n'
                        f"Context: {caption}\n\nStatement: {conclusion}"
                    ),
                }
            ],
            "temperature": 0,
            "max_tokens": 256,
        }
    ).encode()
    req = urllib.request.Request(
        f"{JUDGE}/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        text = json.load(resp)["choices"][0]["message"]["content"]
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return 0, "no-json"
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        logger.warning("_judge_nli: 未预期异常，按原逻辑降级", exc_info=True)
        import json_repair

        v = json_repair.loads(m.group(0))
    return int(v.get("verdict", 0)), str(v.get("reason", ""))[:200]


def _pick_conclusion_text(result: dict) -> str:
    """结论文本取 diagnosis.content（final_response.content，正式结论）。

    2026-10-01 实测修正：patient_explanation 是可选的患者话术（设计上
    不入正式报告），多数请求为 None——正式结论在 findings JSONB 的
    fusion_result.content（本次接线后才有）。
    """
    parts: list[str] = []
    dx = result.get("diagnosis")
    if isinstance(dx, dict):
        fusion = dx.get("fusion_result")
        if isinstance(fusion, dict):
            content = fusion.get("content")
            if isinstance(content, str) and content.strip():
                parts.append(content.strip())
    if not parts:
        v = result.get("recommendations")
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    if not parts:
        v = result.get("patient_explanation")
        if isinstance(v, str) and v.strip():
            parts.append(v.strip())
    for key in ("malignancy_risk", "measurement", "differential_diagnoses"):
        v = result.get(key)
        if v:
            parts.append(f"{key}: {json.dumps(v, ensure_ascii=False)[:220]}")
    acts = result.get("clinical_actions")
    if isinstance(acts, list) and acts:
        parts.append("clinical_actions: " + "; ".join(str(a) for a in acts[:4]))
    if not parts:
        rr = result.get("review_reason")
        if rr:
            parts.append(f"[SG降级] {rr}")
    return "\n".join(parts)[:900]


def _run_one(
    token: str, idx: int, organ: str, caption: str
) -> dict:
    item: dict = {
        "query_id": f"t2c-{idx:03d}",
        "organ": organ,
        "caption": caption,
        "session_id": f"t2c-session-{idx:03d}",
    }
    try:
        submitted = _post_json(
            f"{BASE}/api/v1/diagnosis/request",
            {
                "session_id": item["session_id"],
                "image_ids": [],
                "clinical_text": caption,
                "target_organ": organ,
                "response_tier": "full",
                "patient_context": {"age": 50, "gender": "female", "conditions": []},
            },
            token=token,
        )
        rid = submitted.get("request_id")
        item["request_id"] = rid
        deadline = time.time() + _POLL_DEADLINE
        final = None
        while time.time() < deadline:
            r = _get_json(f"{BASE}/api/v1/diagnosis/result/{rid}", token)
            # 终态全集以管线实际输出为准：needs_review 也是终态
            # （真机 2026-10-01 实测：cardiac 例 1.9s 即到 needs_review，
            # 旧口径漏判导致对着已完成的请求傻等 240s 报 poll_timeout——
            # 9 例中 6 例"超时"全是这个评测器 bug 的假象）。
            if r.get("status") in ("completed", "failed", "timeout", "needs_review"):
                final = r
                break
            time.sleep(2)
        item["status"] = final["status"] if final else "poll_timeout"
        if final and final["status"] in ("completed", "needs_review"):
            item["ai_conclusion"] = _pick_conclusion_text(final)
            item["requires_manual_review"] = final.get("requires_manual_review")
            item["processing_time_ms"] = final.get("processing_time_ms")
            if item["ai_conclusion"].strip():
                verdict, reason = _judge_nli(caption, item["ai_conclusion"])
                item["judge_verdict"] = verdict
                item["judge_reason"] = reason
            else:
                item["judge_verdict"] = 0
                item["judge_reason"] = "empty-conclusion"
    except Exception as exc:
        logger.warning("_run_one: 未预期异常，按原逻辑降级", exc_info=True)
        item["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
    return item


async def _ensure_prerequisites(session_organs: list[tuple[str, str]]) -> None:
    """照 server_link_smoke.sh 口径建患者/会话/同意（幂等，诊断端点强校验）。"""
    import asyncpg

    from app.config.dsn import to_asyncpg_dsn
    from app.config.settings import settings

    conn = await asyncpg.connect(to_asyncpg_dsn(settings.database_url))
    try:
        await conn.execute(
            "INSERT INTO patients (patient_id, name, gender) "
            "VALUES ('t2c-patient', 'T2C-EVAL', 'unknown') "
            "ON CONFLICT (patient_id) DO NOTHING"
        )
        for sid, organ in session_organs:
            await conn.execute(
                """
                INSERT INTO sessions (session_id, patient_id, device_id, status,
                    target_organs, organ_statuses, knowledge_base_version,
                    created_at, updated_at, consent_status_at_creation)
                VALUES ($1, 't2c-patient', 't2c-device', 'active',
                    $2::jsonb, '{}'::jsonb, 'kb_v1.0.0', now(), now(), 'granted')
                ON CONFLICT (session_id) DO NOTHING
                """,
                sid,
                json.dumps([organ]),
            )
        await conn.execute(
            """
            INSERT INTO consent_records (record_id, patient_id, status, granted_at,
                consent_version, created_at)
            SELECT 9000000000002, 't2c-patient', 'granted', now(), '1.0', now()
            WHERE NOT EXISTS (
                SELECT 1 FROM consent_records WHERE record_id = 9000000000002)
            """
        )
    finally:
        await conn.close()


async def main() -> int:
    parser = argparse.ArgumentParser(description="T2c 端到端评测（并发版）")
    parser.add_argument("--output", required=True)
    parser.add_argument("--per-organ", type=int, default=17)
    parser.add_argument("--sample", type=int, default=None, help="总样本数上限（覆盖 per-organ）")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help="并发数（默认取 _CONCURRENCY=6；vLLM continuous batching + "
        "16 worker 下并发无积压，2026-10-01 修正此前默认 1 的配置失效）",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    random.seed(args.seed)

    jsonl = str(Path.home() / "uai/data/us365k/test.resolved.jsonl")
    print("[1/4] 抽样 caption...")
    buckets: dict[str, list[str]] = {"thyroid": [], "liver": [], "cardiac": []}
    with open(jsonl, encoding="utf-8") as f:
        for line in f:
            cap = json.loads(line).get("caption", "")
            if not (60 <= len(cap) <= 400):
                continue
            for organ, pat in ORGAN_PAT.items():
                if pat.search(cap) and len(buckets[organ]) < args.per_organ * 3:
                    buckets[organ].append(cap)
    queries: list[tuple[str, str]] = []
    for organ, caps in buckets.items():
        for c in random.sample(caps, min(args.per_organ, len(caps))):
            queries.append((organ, c))
    print(f"  {len(queries)} 例")

    print("[2/4] 前置数据（sessions 幂等）...")
    await _ensure_prerequisites(
        [(f"t2c-session-{i:03d}", organ) for i, (organ, _c) in enumerate(queries)]
    )

    pw = (Path.home() / "uai/.smoke_pw").read_text(encoding="utf-8").strip()
    token = _post_json(
        f"{BASE}/api/v1/auth/login",
        {"username": "smoke-doctor", "password": pw},
    ).get("access_token")
    if not token:
        raise SystemExit("登录失败")

    if args.sample:
        queries = queries[: args.sample]
    workers = max(1, args.concurrency or _CONCURRENCY)
    print(f"[3/4] 并发 {workers} 跑 {len(queries)} 例...")
    t0 = time.perf_counter()
    results: list[dict] = []
    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            loop.run_in_executor(pool, _run_one, token, i, organ, cap)
            for i, (organ, cap) in enumerate(queries)
        ]
        for done, fut in enumerate(asyncio.as_completed(futures), start=1):
            results.append(await fut)
            ok = sum(1 for r in results if r.get("judge_verdict") == 1)
            comp = sum(1 for r in results if r.get("status") == "completed")
            print(
                f"  {done}/{len(queries)} completed={comp} supported={ok}",
                flush=True,
            )
    elapsed = time.perf_counter() - t0

    print("[4/4] 汇总...")
    scored = [r for r in results if "judge_verdict" in r]
    completed = [r for r in results if r.get("status") == "completed"]
    supported = sum(1 for r in scored if r["judge_verdict"] == 1)
    report = {
        "task": "T2c 端到端诊断评测（US-365K caption 作所见输入，并发 6）",
        "dataset_block_note": "ALN-ULTRA Zenodo 15003119 files=0（发布不完整）",
        "n_total": len(queries),
        "n_completed": len(completed),
        "n_scored": len(scored),
        "judge_support_rate": round(supported / len(scored), 4) if scored else None,
        "manual_review_rate": round(
            sum(1 for r in completed if r.get("requires_manual_review")) / len(completed),
            4,
        )
        if completed
        else None,
        "avg_processing_ms": round(
            sum(r.get("processing_time_ms") or 0 for r in completed) / len(completed), 1
        )
        if completed
        else None,
        "wall_clock_seconds": round(elapsed, 1),
        "conclusion": "三列对照=输入所见(caption) / AI 结论 / 判官裁决。"
        "caption 非金标准，不得解读为诊断准确率。",
        "results": sorted(results, key=lambda r: r["query_id"]),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(
        f"完成: completed={len(completed)}/{len(queries)} "
        f"support={report['judge_support_rate']} "
        f"avg_proc={report['avg_processing_ms']}ms wall={elapsed:.0f}s → {out}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
