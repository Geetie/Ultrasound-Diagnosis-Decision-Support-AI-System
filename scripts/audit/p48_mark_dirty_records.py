"""A.70.6：给历史脏记录**打标保留**（不删数据，可回滚）。

**用户 2026-10-20 拍板**：「打标保留」——不删历史判断，只在记录上留一个可见的标记，
让报表/评测/前端都能识别"这条是模板回显"，同时保留原始数据以备审计。

**怎么打标（不改 schema）**：写进记录自己的 `findings.data_quality`：

    "data_quality": {"prompt_echo": "placeholder_chunk_id",
                     "marked_at": "2026-10-20 14:05:00",
                     "marked_by": "p48_mark_dirty_records/A.70.6"}

* **不动** `findings.fusion_result` / `recommendations` 等业务字段 —— 前端读的是那几个键，
  多一个 `data_quality` 不影响渲染（且给了前端"这条要提示"的可能）；
* **默认 dry-run**：只统计"会给多少条打标"，不写库；`--apply` 才写；
* **可回滚**：`--unmark` 只移除 `data_quality.prompt_echo`（保留 `data_quality` 里其它键）；
* **幂等**：重复跑不会重复计数（已打标且原因相同的跳过）。

用法（服务器）：
    # 看会给多少条打标
    ~/uai/venv/bin/python scripts/audit/p48_mark_dirty_records.py
    # 真写
    ~/uai/venv/bin/python scripts/audit/p48_mark_dirty_records.py --apply
    # 回滚
    ~/uai/venv/bin/python scripts/audit/p48_mark_dirty_records.py --unmark --apply

退出码：0 成功；2 库不可达。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402

from app.inference.output_guard import detect_prompt_echo  # noqa: E402

#: 打标写入的键名（前端/报表按它识别）
DATA_QUALITY_KEY = "data_quality"
PROMPT_ECHO_KEY = "prompt_echo"
MARKED_BY = "p48_mark_dirty_records/A.70.6"

_DEGRADED_MARKERS = ("llm_unavailable", "manual review required", "inferenceerror")


def is_degraded(text: str) -> bool:
    """降级提示不是结论。纯函数。"""
    low = (text or "").lower()
    return any(m in low for m in _DEGRADED_MARKERS)


def mark_payload(findings: object, reason: str, marked_at: str) -> dict:
    """在 findings 上**加**打标，返回新 dict（不改入参）。纯函数（守卫钉住）。

    只动 `data_quality` 一个键，其余业务字段原样保留。
    """
    base = dict(findings) if isinstance(findings, dict) else {}
    dq = dict(base.get(DATA_QUALITY_KEY) or {})
    dq[PROMPT_ECHO_KEY] = reason
    dq["marked_at"] = marked_at
    dq["marked_by"] = MARKED_BY
    base[DATA_QUALITY_KEY] = dq
    return base


def unmark_payload(findings: object) -> tuple[dict, bool]:
    """移除打标，返回 ``(新 dict, 是否真的改过)``。纯函数（守卫钉住）。

    `data_quality` 里若还有别的键（别的工具打的），**保留**；空了才删整个键。
    """
    base = dict(findings) if isinstance(findings, dict) else {}
    dq = dict(base.get(DATA_QUALITY_KEY) or {})
    changed = dq.pop(PROMPT_ECHO_KEY, None) is not None
    for extra in ("marked_at", "marked_by"):
        if changed:
            dq.pop(extra, None)
    if dq:
        base[DATA_QUALITY_KEY] = dq
    else:
        base.pop(DATA_QUALITY_KEY, None)
    return base, changed


def already_marked(findings: object, reason: str) -> bool:
    """是否已用同一原因打过标（幂等判断）。纯函数。"""
    dq = (findings or {}).get(DATA_QUALITY_KEY) if isinstance(findings, dict) else None
    return isinstance(dq, dict) and dq.get(PROMPT_ECHO_KEY) == reason


async def _run(apply: bool, unmark: bool, limit: int) -> dict:
    from sqlalchemy import select, update

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.findings,
                    DiagnosisResult.created_at,
                ).order_by(DiagnosisResult.created_at.desc())
            )
        ).all()

    stats = {
        "scanned": 0,
        "degraded_skipped": 0,
        "clean_skipped": 0,
        "already_marked": 0,
        "to_mark": 0,
        "marked": 0,
        "unmarked": 0,
        "reasons": {},
        "samples": [],
    }
    marked_at = time.strftime("%Y-%m-%d %H:%M:%S")
    pending: list[tuple[str, dict]] = []

    for request_id, findings, _created in rows:
        stats["scanned"] += 1
        text = _conclusion_of(findings)
        if not text.strip():
            stats["degraded_skipped"] += 1
            continue
        # ⚠️ 顺序很重要：**先判回显，再判降级**。
        # A.70.9 实测：`grade_unparseable: … ; manual review required` 既是"解析失败"又含
        # 占位符正文；若先按降级跳过，这批脏记录就会漏标（首版漏了 52 条）。
        echo_hit = detect_prompt_echo(text)
        if not echo_hit and is_degraded(text):
            stats["degraded_skipped"] += 1
            continue

        if unmark:
            new_findings, changed = unmark_payload(findings)
            if changed:
                stats["unmarked"] += 1
                pending.append((str(request_id), new_findings))
            continue

        reason = echo_hit
        if not reason:
            stats["clean_skipped"] += 1
            continue
        stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1
        if already_marked(findings, reason):
            stats["already_marked"] += 1
            continue
        stats["to_mark"] += 1
        if len(stats["samples"]) < 3:
            stats["samples"].append({"request_id": str(request_id), "reason": reason})
        pending.append((str(request_id), mark_payload(findings, reason, marked_at)))
        if limit and len(pending) >= limit:
            break

    if apply and pending:
        for request_id, new_findings in pending:
            await session.execute(
                update(DiagnosisResult)
                .where(DiagnosisResult.request_id == request_id)
                .values(findings=new_findings)
            )
        await session.commit()
        if unmark:
            stats["unmarked_written"] = len(pending)
        else:
            stats["marked"] = len(pending)

    stats["applied"] = bool(apply)
    stats["pending_writes"] = len(pending)
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    ap.add_argument("--unmark", action="store_true", help="回滚：移除 prompt_echo 打标")
    ap.add_argument("--limit", type=int, default=0, help=">0 时最多处理 N 条（冒烟）")
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/dirty_marking.json")
    )
    args = ap.parse_args()

    try:
        stats = asyncio.run(_run(args.apply, args.unmark, args.limit))
    except Exception as exc:  # 库不可达必须显式失败
        print(f"[FATAL] 打标失败：{type(exc).__name__}: {exc}")
        return 2

    mode = "回滚" if args.unmark else "打标"
    print(
        f"{mode}模式（{'写库' if args.apply else 'dry-run'}）：扫描 {stats['scanned']} 条，"
        f"降级跳过 {stats['degraded_skipped']}、干净跳过 {stats['clean_skipped']}"
    )
    if args.unmark:
        print(f"   需移除打标 {stats['pending_writes']} 条，已写 {stats.get('unmarked_written', 0)}")
    else:
        print(f"   脏记录 {stats['to_mark'] + stats['already_marked']} 条"
              f"（已打过标 {stats['already_marked']}、本次待打 {stats['to_mark']}），"
              f"已写 {stats['marked']}")
        print(f"   原因分布：{stats['reasons']}")

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
