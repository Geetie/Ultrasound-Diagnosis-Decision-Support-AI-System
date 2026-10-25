"""A.95：反向补判读 —— 给「有影像但无判读」的 session 补跑，产出可入集样本。

## 为什么要做（解决了一个真机取证出的结构性缺口）

`p74_eval_sample_bottleneck.py` 取证出的根因链：

```
判读总数 656 → 有正文 405 → **有影像 0**  ← 断在这里
反向缺口：277 个 session 有影像但**没有判读**
```

两个方向都断着：
* 有判读的没影像 → 采集器拒收（去重键 + L2 判官上下文都需要影像）；
* 有影像的没判读 → 数据闲置，从没进过评测集。

**本脚本补后一半**：对已入库的 DICOM 反向补判读，直接产出
「有影像 + 有正文」的样本 —— 这是 `p73` 里 Qwen3 升级门控（需 ≥300）的唯一可行路径。

## 设计要点

1. **只补缺，不重跑**：`LEFT JOIN diagnosis_results WHERE request_id IS NULL`，
   已有判读的 session 一个都不碰（不重复烧 LLM）；
2. **串行 + 每例等待终态**：并发会打爆 GPU（48GB 已用 45.4GB）；
3. **每个 session 只提交一次**（取该 session 的全部 image_ids）；
4. **失败逐例记录**，不中断整批（一例挂不该毁掉 276 例）；
5. **默认 dry-run**，`--apply` 才真提交；`--limit` 控制批量。

## 用法

    # 1) 盘点（只读）
    ~/uai/venv/bin/python scripts/audit/p75_backfill_diagnosis.py

    # 2) 真跑（建议先 --limit 5 冒烟）
    ... --apply --limit 5
    ... --apply            # 全量 277

退出码：0 完成 ｜ 2 前置缺失。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from datetime import datetime, timezone

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts" / "audit"))

API = "http://127.0.0.1:18000"
#: 单例等待终态的最长时间（秒）—— 与生产 90s 预算对齐并留余量
WAIT_TIMEOUT_S = 120


async def _pick_sessions(limit: int | None) -> list[dict]:
    """取「有影像但无判读」的 session，附器官与 image_ids。"""
    from sqlalchemy import text

    from app.config.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT s.session_id,
                           COALESCE(s.target_organs, '[]'::jsonb) AS organs,
                           array_agg(d.image_id ORDER BY d.created_at) AS image_ids
                    FROM sessions s
                    JOIN dicom_images d ON d.session_id = s.session_id
                    LEFT JOIN diagnosis_results r ON r.session_id = s.session_id
                    WHERE r.request_id IS NULL
                    GROUP BY s.session_id, s.target_organs
                    ORDER BY s.session_id
                    """
                )
            )
        ).all()
    out = []
    for sid, organs, imgs in rows:
        organ_list = organs if isinstance(organs, list) else []
        organ = (organ_list[0] if organ_list else "thyroid")
        out.append(
            {
                "session_id": str(sid),
                "organ": str(organ),
                "image_ids": [str(x) for x in (imgs or [])],
            }
        )
    return out[:limit] if limit else out


def _http(method: str, path: str, token: str | None, body: dict | None = None):
    """极简 HTTP（复用 _audit_common 的口径，但不依赖它，便于离线单测）。"""
    import httpx

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    with httpx.Client(timeout=30.0) as c:
        r = c.request(method, f"{API}{path}", json=body, headers=headers)
        try:
            return r.status_code, r.json()
        except Exception:
            return r.status_code, {"raw": r.text[:200]}


async def _run_one(case: dict, token: str) -> dict:
    """提交一例判读并等终态。返回该例结果摘要。"""
    t0 = time.monotonic()
    try:
        code, body = _http(
            "POST",
            "/api/v1/diagnosis/request",
            token,
            {
                "session_id": case["session_id"],
                "image_ids": case["image_ids"],
                "target_organ": case["organ"],
                # 无临床文本（这些 session 本来就没有）——如实传空，不编造
                "clinical_text": "",
                "patient_context": {"conditions": []},
            },
        )
        if code >= 400:
            return {"session_id": case["session_id"], "ok": False,
                    "reason": f"submit_{code}: {str(body)[:100]}"}
        rid = body.get("request_id")
        if not rid:
            return {"session_id": case["session_id"], "ok": False, "reason": "no_request_id"}
    except Exception as exc:
        return {"session_id": case["session_id"], "ok": False,
                "reason": f"submit_exc_{type(exc).__name__}"}

    # 轮询终态
    deadline = time.monotonic() + WAIT_TIMEOUT_S
    final: dict = {}
    while time.monotonic() < deadline:
        await asyncio.sleep(2)
        try:
            _c, res = _http("GET", f"/api/v1/diagnosis/result/{rid}", token)
        except Exception as exc:  # silent-ok: 单次轮询失败继续等，下面按超时/终态汇总
            logger.warning("poll_failed rid=%s err=%s", rid, type(exc).__name__)
            continue
        if isinstance(res, dict) and res.get("status") in (
            "completed",
            "needs_review",
            "failed",
            "timeout",
            "cancelled",
        ):
            final = res
            break

    if not final:
        return {"session_id": case["session_id"], "request_id": rid, "ok": False,
                "reason": f"wait_timeout_{WAIT_TIMEOUT_S}s"}

    fr = ((final.get("diagnosis") or {}).get("fusion_result") or {})
    content = fr.get("content") or ""
    return {
        "session_id": case["session_id"],
        "request_id": rid,
        "ok": True,
        "status": final.get("status"),
        "content_len": len(content),
        "unable": "无法判断" in content or not content,
        "elapsed_s": round(time.monotonic() - t0, 1),
    }


async def main(apply: bool, limit: int | None, report: str | None) -> int:
    cases = await _pick_sessions(limit)
    if not cases:
        print("[FATAL] 没有「有影像但无判读」的 session")
        return 2

    from collections import Counter

    organs = Counter(c["organ"] for c in cases)
    n_imgs = sum(len(c["image_ids"]) for c in cases)
    print(f"=== 待补判读：{len(cases)} 个 session（{n_imgs} 张影像）===")
    print(f"  按器官: {dict(organs)}")
    print(f"  预计耗时: {len(cases) * 4 / 60:.0f} 分钟（按 4s/例）")

    if not apply:
        print("\n[dry-run] 未提交任何判读。加 --apply 真跑（建议先 --limit 5）。")
        return 0

    # 登录（用 smoke-doctor，与其它探针一致）
    import _audit_common as A

    doc = A.login("smoke-doctor", A.pw("smoke_pw"))
    token = doc["access_token"]
    print(f"\n=== 开始补判读（{len(cases)} 例，串行）===")

    results: list[dict] = []
    for i, case in enumerate(cases, 1):
        r = await _run_one(case, token)
        results.append(r)
        mark = "✅" if r.get("ok") else "❌"
        extra = (
            f"status={r.get('status')} len={r.get('content_len')} "
            f"unable={r.get('unable')} {r.get('elapsed_s')}s"
            if r.get("ok")
            else r.get("reason", "")
        )
        print(f"  [{i}/{len(cases)}] {mark} {case['session_id']} {extra}")

    ok = [r for r in results if r.get("ok")]
    unable = [r for r in ok if r.get("unable")]
    print(
        f"\n=== 汇总 ===\n"
        f"  成功 {len(ok)} / {len(results)}（失败 {len(results)-len(ok)}）\n"
        f"  其中「无法判断」{len(unable)} 条 → 可用于入集的有正文样本 **{len(ok)-len(unable)}**"
    )

    payload = {
        "probe": "p75_backfill_diagnosis",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "purpose": (
            "补「有影像但无判读」的 session。p74 取证：判读 656 条里 0 条有影像，"
            "另有 277 个 session 有影像但无判读 —— 两个方向都断着。本脚本补后一半。"
        ),
        "n_sessions": len(cases),
        "n_images": n_imgs,
        "organs": dict(organs),
        "attempted": len(results),
        "succeeded": len(ok),
        "failed": len(results) - len(ok),
        "unable_conclusion": len(unable),
        "usable_for_eval": len(ok) - len(unable),
        "results": results,
    }
    if report:
        rp = pathlib.Path(report).expanduser()
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  报告：{rp}")
    return 0


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger("p75")

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的提交判读（默认 dry-run）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--report",
        default=str(pathlib.Path.home() / "uai/reports/backfill_diagnosis.json"),
    )
    a = ap.parse_args()
    sys.exit(asyncio.run(main(a.apply, a.limit, a.report)))
