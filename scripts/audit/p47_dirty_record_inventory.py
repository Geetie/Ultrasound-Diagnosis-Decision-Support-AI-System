"""A.70.5：历史脏记录**只读清点** + 隔离清单（不改数据库）。

**为什么只读**：库里 88%（302/344）的判读结论是"复述提示词示例"（A.70.4 实测）。
清理历史数据属于**改数据**，必须拍板；本脚本只做两件事：

1. **清点**：按器官 / 月份 / 原因统计脏记录，并给出证据样本（原文截断）；
2. **出隔离清单**：`request_id + 原因 + 结论指纹`，写成 JSONL —— 评测、报表、后续清理
   都可以直接拿它当"排除名单"，**不依赖任何 schema 变更**。

**守则**：只发 SELECT（脚本里有 `UPDATE`/`DELETE` 会触发守卫失败）；默认不写任何库。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p47_dirty_record_inventory.py \
        --report ~/uai/reports/dirty_records.json \
        --quarantine ~/uai/data/eval/quarantine_20261020.jsonl

退出码：0 出数；2 库不可达（不静默给 0）。
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import pathlib
import sys

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402  (同一把尺子：结论怎么取)

from app.inference.output_guard import detect_prompt_echo  # noqa: E402

#: 降级答案的特征（不是结论）
_DEGRADED_MARKERS = ("llm_unavailable", "manual review required", "inferenceerror")


def is_degraded(text: str) -> bool:
    """是否是降级提示（不是真结论）。纯函数。"""
    low = (text or "").lower()
    return any(m in low for m in _DEGRADED_MARKERS)


def fingerprint(text: str) -> str:
    """结论指纹（前 16 位 sha256）：清单里**不落原文**，只落指纹 + 长度。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]


def summarize(items: list[dict]) -> dict:
    """把逐条记录汇总成统计。纯函数（守卫钉住）。

    ``items`` 元素需含：``organ`` / ``month`` / ``reason``（None 表示干净）/ ``degraded``。
    """
    total = len(items)
    degraded = sum(1 for x in items if x.get("degraded"))
    dirty = sum(1 for x in items if (not x.get("degraded")) and x.get("reason"))
    clean = total - degraded - dirty

    def _share(n: int) -> float | None:
        denom = total - degraded  # 分母：真结论（降级样本没有结论，不该进分母）
        return round(n / denom, 4) if denom else None

    reasons: dict[str, int] = {}
    by_organ: dict[str, dict] = {}
    by_month: dict[str, dict] = {}
    for x in items:
        if x.get("degraded"):
            continue
        organ = str(x.get("organ") or "(未知)")
        month = str(x.get("month") or "(未知)")
        for bucket, key in ((by_organ, organ), (by_month, month)):
            slot = bucket.setdefault(key, {"total": 0, "dirty": 0})
            slot["total"] += 1
            if x.get("reason"):
                slot["dirty"] += 1
        if x.get("reason"):
            reasons[x["reason"]] = reasons.get(x["reason"], 0) + 1

    for slot in by_organ.values():
        slot["dirty_rate"] = round(slot["dirty"] / slot["total"], 4) if slot["total"] else None
    for slot in by_month.values():
        slot["dirty_rate"] = round(slot["dirty"] / slot["total"], 4) if slot["total"] else None

    return {
        "total": total,
        "degraded": degraded,
        "dirty": dirty,
        "clean": clean,
        "dirty_share": _share(dirty),
        "clean_share": _share(clean),
        "reasons": reasons,
        "by_organ": dict(sorted(by_organ.items(), key=lambda kv: -kv[1]["dirty"])),
        "by_month": dict(sorted(by_month.items())),
    }


async def _collect(limit: int) -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.created_at,
                    DiagnosisResult.findings,
                ).order_by(DiagnosisResult.created_at.desc())
            )
        ).all()

    items: list[dict] = []
    for request_id, organ, created_at, findings in rows:
        text = _conclusion_of(findings)
        degraded = (not text.strip()) or is_degraded(text)
        reason = None if degraded else detect_prompt_echo(text)
        items.append(
            {
                "request_id": str(request_id),
                "organ": str(organ or ""),
                "month": created_at.strftime("%Y-%m") if created_at else "",
                "degraded": degraded,
                "reason": reason,
                "len": len(text),
                "fingerprint": fingerprint(text),
                "excerpt": text[:160].replace("\n", " ") if reason else "",
            }
        )
        if limit and len(items) >= limit:
            break
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/dirty_records.json")
    )
    ap.add_argument(
        "--quarantine",
        default="",
        help="隔离清单输出路径（JSONL：request_id + 原因 + 指纹）；留空则只统计",
    )
    ap.add_argument("--limit", type=int, default=0, help=">0 时只取最近 N 条（冒烟）")
    ap.add_argument(
        "--samples",
        type=int,
        default=5,
        help="报告里保留多少条脏记录的证据样本（原文截断）",
    )
    args = ap.parse_args()

    try:
        items = asyncio.run(_collect(args.limit))
    except Exception as exc:  # 库不可达必须显式失败（不静默给 0）
        print(f"[FATAL] 清点失败：{type(exc).__name__}: {exc}")
        return 2

    summary = summarize(items)
    report = {
        "probe": "p47_dirty_record_inventory",
        "readonly": True,
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "guard": "app.inference.output_guard.detect_prompt_echo",
        **summary,
        "samples": [x for x in items if x["reason"]][: args.samples],
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"判读总数 {summary['total']}：降级 {summary['degraded']}、"
          f"**脏记录 {summary['dirty']}（{summary['dirty_share']}）**、干净 {summary['clean']}")
    for organ, slot in list(summary["by_organ"].items())[:5]:
        print(f"   {organ:<10} 脏 {slot['dirty']}/{slot['total']}（{slot['dirty_rate']}）")
    if summary["reasons"]:
        print(f"   原因分布：{summary['reasons']}")

    if args.quarantine:
        qp = pathlib.Path(args.quarantine).expanduser()
        qp.parent.mkdir(parents=True, exist_ok=True)
        with qp.open("w", encoding="utf-8") as fh:
            for x in items:
                if x["reason"]:
                    fh.write(
                        json.dumps(
                            {
                                "request_id": x["request_id"],
                                "reason": x["reason"],
                                "organ": x["organ"],
                                "month": x["month"],
                                "fingerprint": x["fingerprint"],
                            },
                            ensure_ascii=False,
                        )
                        + "\n"
                    )
        print(f"隔离清单：{qp}（{summary['dirty']} 条）")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
