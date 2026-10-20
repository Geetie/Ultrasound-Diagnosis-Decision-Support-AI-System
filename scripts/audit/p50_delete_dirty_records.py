"""A.70.8：**删除**已打标的历史脏记录（用户 2026-10-20 决定：不留，改记文档）。

⚠️ **这是不可逆操作**。三条硬约束写在代码里，避免"手一抖全删"：

1. **只删被标记的**：选择条件是 `findings.data_quality.prompt_echo` **存在**（A.70.6 打的标），
   **不重新判定**——标记是唯一事实源，避免"判定规则漂移后删错一批"；
2. **必须先备份**：`--backup` 指向一个 JSONL，删除前把**完整 findings** 写进去；
   `--apply` 时备份为空/写失败 → **直接拒绝执行**；
3. **默认 dry-run**：不带 `--apply` 只统计，不删。

**依赖检查**（不自动级联）：删前统计"会变成孤儿的 `feedback_records`"条数并打印；
孤儿反馈**不自动删**（医生的修正反馈比一条脏判读值钱），要不要清由人决定。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p50_delete_dirty_records.py            # dry-run
    ~/uai/venv/bin/python scripts/audit/p50_delete_dirty_records.py --apply \
        --backup ~/uai/data/eval/deleted_dirty_20261020.jsonl

退出码：0 成功；2 库不可达 / 备份失败 / 依赖检查异常。
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

DATA_QUALITY_KEY = "data_quality"
PROMPT_ECHO_KEY = "prompt_echo"


def marked_reason(findings: object) -> str | None:
    """取打标原因（没有则是 None）。纯函数（守卫钉住）。"""
    if not isinstance(findings, dict):
        return None
    dq = findings.get(DATA_QUALITY_KEY)
    if not isinstance(dq, dict):
        return None
    reason = dq.get(PROMPT_ECHO_KEY)
    return str(reason) if reason else None


def build_backup_rows(rows: list[tuple]) -> list[dict]:
    """把待删记录整理成备份行（**含完整 findings**，便于日后恢复）。纯函数。

    ``rows`` 元素为 ``(request_id, organ, created_at, findings)``。
    """
    out: list[dict] = []
    for request_id, organ, created_at, findings in rows:
        out.append(
            {
                "request_id": str(request_id),
                "organ": str(organ or ""),
                "created_at": created_at.isoformat() if created_at else None,
                "findings": findings,
            }
        )
    return out


async def _run(apply: bool, backup_path: pathlib.Path, limit: int) -> dict:
    from sqlalchemy import delete, func, select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult, FeedbackRecord

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.created_at,
                    DiagnosisResult.findings,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()
        total = len(rows)
        targets = [r for r in rows if marked_reason(r[3])]
        if limit:
            targets = targets[:limit]

        ids = [str(r[0]) for r in targets]
        orphan_feedback = 0
        if ids:
            orphan_feedback = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(FeedbackRecord)
                        .where(FeedbackRecord.request_id.in_(ids))
                    )
                ).scalar_one()
            )

        stats = {
            "total_rows": total,
            "marked_rows": len(targets),
            "orphan_feedback": orphan_feedback,
            "deleted": 0,
            "applied": bool(apply),
            "backup": str(backup_path),
            "reasons": {},
        }
        for r in targets:
            reason = marked_reason(r[3]) or "unknown"
            stats["reasons"][reason] = stats["reasons"].get(reason, 0) + 1

        if not apply:
            return stats

        # —— 备份必须成功，否则拒绝删 ——
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_rows = build_backup_rows(targets)
        with backup_path.open("w", encoding="utf-8") as fh:
            for row in backup_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        if not backup_path.is_file() or backup_path.stat().st_size == 0:
            raise RuntimeError("备份文件为空，拒绝删除")

        deleted = 0
        batch = 200
        for start in range(0, len(ids), batch):
            chunk = ids[start : start + batch]
            result = await session.execute(
                delete(DiagnosisResult).where(DiagnosisResult.request_id.in_(chunk))
            )
            await session.commit()
            deleted += int(result.rowcount or 0)
        stats["deleted"] = deleted

        remaining = (
            await session.execute(
                select(func.count()).select_from(DiagnosisResult)
            )
        ).scalar_one()
        stats["remaining_rows"] = int(remaining)
        return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="真的删（默认 dry-run）")
    ap.add_argument(
        "--backup",
        default=str(pathlib.Path.home() / f"uai/data/eval/deleted_dirty_{time.strftime('%Y%m%d')}.jsonl"),
        help="删除前的完整备份（JSONL，含 findings）；--apply 时必填且必须写成功",
    )
    ap.add_argument("--limit", type=int, default=0, help=">0 时最多删 N 条（冒烟）")
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/dirty_deletion.json")
    )
    args = ap.parse_args()

    try:
        stats = asyncio.run(_run(args.apply, pathlib.Path(args.backup).expanduser(), args.limit))
    except Exception as exc:  # 库不可达 / 备份失败：显式失败，绝不"删一半"
        print(f"[FATAL] {type(exc).__name__}: {exc}")
        return 2

    print(
        f"删除模式（{'写库' if stats['applied'] else 'dry-run'}）："
        f"库内共 {stats['total_rows']} 条，**带标记 {stats['marked_rows']} 条**"
    )
    print(f"   原因分布：{stats['reasons']}")
    print(f"   将变成孤儿的 feedback 记录：{stats['orphan_feedback']} 条（不自动删）")
    if stats["applied"]:
        print(f"   **已删除 {stats['deleted']} 条**，剩余 {stats.get('remaining_rows')} 条")
        print(f"   备份：{stats['backup']}")

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
