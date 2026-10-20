"""A.73：清扫**长期卡在"处理中"**的判读（默认 dry-run，可回滚，先备份）。

**背景（真机取证）**：411 条判读里有 **87 条**的 `findings` 永远停在 `{"status": "processing"}`，
另有 **20 条** `findings` 为 `NULL` —— 医生打开报告是**空白**，且状态永远显示"处理中"。
根因是写入口先落占位、而终态更新在异常/进程崩溃时丢失（A.73 已把失败路径改为写可读正文）。

**本脚本做两件事**（都只针对"超时未完成"的行）：
1. 把 `status` 标为 `failed`、`confidence=0`；
2. 写入**可读正文**（`findings.fusion_result.content`），让报告不再是空白。

**安全约束**
* 默认 dry-run，`--apply` 才写；
* `--minutes`（默认 30）之内**不动**（可能正在跑）；
* `--backup` 写 JSONL（含原 findings），备份为空则拒绝执行；
* 只匹配 `findings` 为 NULL 或 `{"status": "processing"}` 的行 —— **不碰已有结论的行**。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/ops/sweep_stale_processing.py
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/ops/sweep_stale_processing.py \
        --apply --backup ~/uai/data/eval/stale_processing_20261020.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from datetime import datetime, timedelta, timezone

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from app.services.degraded_texts import stale_processing_text  # noqa: E402


def is_stale(findings: object, created_at: datetime | None, now: datetime, minutes: int) -> bool:
    """是否"超时未完成"（`findings` 为 NULL 或只有 processing 占位）。纯函数（守卫钉住）。"""
    if created_at is None:
        return False
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    if created_at > now - timedelta(minutes=minutes):
        return False
    if findings is None:
        return True
    return isinstance(findings, dict) and set(findings) == {"status"} and findings.get(
        "status"
    ) == "processing"


async def _run(apply: bool, minutes: int, backup: pathlib.Path, limit: int) -> dict:
    from sqlalchemy import select, update

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.created_at,
                    DiagnosisResult.findings,
                    DiagnosisResult.confidence,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

        targets = [r for r in rows if is_stale(r[2], r[1], now, minutes)]
        if limit:
            targets = targets[:limit]

        stats = {
            "scanned": len(rows),
            "stale": len(targets),
            "applied": bool(apply),
            "swept": 0,
            "backup": str(backup),
            "samples": [
                {
                    "request_id": str(r[0]),
                    "created_at": r[1].isoformat() if r[1] else None,
                    "findings": r[2],
                }
                for r in targets[:3]
            ],
        }
        if not apply or not targets:
            return stats

        backup.parent.mkdir(parents=True, exist_ok=True)
        with backup.open("w", encoding="utf-8") as fh:
            for r in targets:
                fh.write(
                    json.dumps(
                        {
                            "request_id": str(r[0]),
                            "created_at": r[1].isoformat() if r[1] else None,
                            "findings": r[2],
                            "confidence": float(r[3] or 0.0),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )
        if not backup.is_file() or backup.stat().st_size == 0:
            raise RuntimeError("备份文件为空，拒绝清扫")

        text = stale_processing_text(minutes)
        for r in targets:
            await session.execute(
                update(DiagnosisResult)
                .where(DiagnosisResult.request_id == str(r[0]))
                .values(
                    status="failed",
                    confidence=0.0,
                    findings={
                        "fusion_result": {
                            "content": text,
                            "degraded": True,
                            "reason": "stale_processing",
                        },
                        "previous_findings": r[2],
                    },
                )
            )
        await session.commit()
        stats["swept"] = len(targets)
        return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的写库（默认 dry-run）")
    ap.add_argument("--minutes", type=int, default=30, help="超过多少分钟算卡死（默认 30）")
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 条（冒烟）")
    ap.add_argument(
        "--backup",
        default=str(pathlib.Path.home() / f"uai/data/eval/stale_processing_{time.strftime('%Y%m%d')}.jsonl"),
    )
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/stale_sweep.json")
    )
    args = ap.parse_args()

    try:
        stats = asyncio.run(
            _run(args.apply, args.minutes, pathlib.Path(args.backup).expanduser(), args.limit)
        )
    except Exception as exc:
        print(f"[FATAL] 清扫失败：{type(exc).__name__}: {exc}")
        return 2

    print(
        f"清扫模式（{'写库' if stats['applied'] else 'dry-run'}）：扫描 {stats['scanned']} 条，"
        f"**卡死 {stats['stale']} 条**"
    )
    for s in stats["samples"]:
        print(f"   样例 {s['request_id']} created={s['created_at']} findings={s['findings']}")
    if stats["applied"]:
        print(f"   **已清扫 {stats['swept']} 条**（写入可读正文 + status=failed），备份：{stats['backup']}")

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
