"""A.70.9：**每日数据质量监控**（只读）—— 空结论率 / 分级缺失率 / 模板回显率。

**为什么需要**：A.70.7 查明 9/28–9/30 的模型服务故障让 328 条请求变成"空结论"，
而**当时没有任何告警**——断了两天，只有事后翻库才发现。这个脚本就是补那个洞。

**四个待监控的量**（窗口内，默认最近 7 天）

| 指标 | 含义 | 阈值（超了告警） |
|---|---|---|
| `empty_rate` | 结论为空的占比（服务故障/超时） | > **20%** |
| `unparseable_rate` | 有输出但**没有可解析的分级/EF** | > **10%** |
| `echo_rate` | 结论是**提示词示例照抄** | > **1%** |
| `degraded_rate` | 显式降级串（llm_unavailable 等） | > **5%** |

**退出码给 cron 用**：0 = 正常；**1 = 有告警**（cron 邮件/告警接到这条）；2 = 库不可达。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p51_daily_quality_monitor.py --days 7
    # crontab 示例（每天 08:10）：
    # 10 8 * * * /mnt/ee16/home/student03/uai/venv/bin/python \
    #   /mnt/ee16/home/student03/uai/src/scripts/audit/p51_daily_quality_monitor.py --days 1 \
    #   >> /mnt/ee16/home/student03/uai/logs/quality_monitor.log 2>&1
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402
from p49_degraded_cause import degraded_kind  # noqa: E402  (同一把尺子：降级怎么分类)

from app.inference.output_guard import detect_prompt_echo  # noqa: E402

#: 告警阈值（跑之前写死；改阈值必须同步台账）
THRESHOLDS: dict[str, float] = {
    "empty_rate": 0.20,
    "unparseable_rate": 0.10,
    "echo_rate": 0.01,
    "degraded_rate": 0.05,
}

_UNPARSEABLE_KINDS = ("grade_unparseable", "lvef_unparseable")


def in_window(created_at: datetime | None, cutoff: datetime) -> bool:
    """是否落在窗口内（None 视为不在窗口内，不静默算进去）。纯函数。

    ⚠️ 真机踩坑：库里的 `created_at` 是**带时区**的（UTC），而 `datetime.now()` 是 naive，
    直接比较会抛 `TypeError: can't compare offset-naive and offset-aware datetimes`。
    这里统一以 `created_at` 的时区为准（naive 行则视为与 cutoff 同区）。
    """
    if created_at is None:
        return False
    if (created_at.tzinfo is None) != (cutoff.tzinfo is None):
        cutoff = cutoff.replace(tzinfo=created_at.tzinfo)
    return created_at >= cutoff


def count_of(items: list[dict]) -> dict:
    """按类别计数。``items`` 每项含 ``kind`` 与 ``echo``（打标原因或 None）。纯函数。"""
    out = {
        "total": len(items),
        "empty": 0,
        "unparseable": 0,
        "echo": 0,
        "degraded": 0,
        "clean": 0,
    }
    for x in items:
        kind = str(x.get("kind") or "")
        if x.get("echo"):
            out["echo"] += 1
        if kind == "empty":
            out["empty"] += 1
        elif kind in _UNPARSEABLE_KINDS:
            out["unparseable"] += 1
        elif kind == "not_degraded":
            out["clean"] += 1
        else:
            out["degraded"] += 1
    return out


def rates_of(counts: dict) -> dict:
    """把计数换成占比（分母为窗口内总数；为 0 时全部 None）。纯函数。"""
    total = counts.get("total") or 0
    if not total:
        return {k: None for k in THRESHOLDS}
    return {
        "empty_rate": round(counts["empty"] / total, 4),
        "unparseable_rate": round(counts["unparseable"] / total, 4),
        "echo_rate": round(counts["echo"] / total, 4),
        "degraded_rate": round(counts["degraded"] / total, 4),
    }


def evaluate(rates: dict, thresholds: dict | None = None) -> dict:
    """按阈值判定是否告警。纯函数（守卫钉住）。"""
    th = dict(thresholds or THRESHOLDS)
    alerts: list[str] = []
    for key, limit in th.items():
        value = rates.get(key)
        if value is not None and value > limit:
            alerts.append(f"{key}={value} > {limit}")
    return {"alerts": alerts, "ok": not alerts}


async def _collect(days: int) -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.created_at,
                    DiagnosisResult.findings,
                )
            )
        ).all()

    items: list[dict] = []
    for _request_id, created_at, findings in rows:
        if not in_window(created_at, cutoff):
            continue
        text = _conclusion_of(findings)
        dq = (findings or {}).get("data_quality") if isinstance(findings, dict) else None
        echo = (dq or {}).get("prompt_echo") if isinstance(dq, dict) else None
        # A.73：清扫器写的"可读失败说明"（degraded=True）不能算干净结论
        kind = degraded_kind(text)
        if kind == "not_degraded" and isinstance(findings, dict):
            fr = findings.get("fusion_result")
            if isinstance(fr, dict) and fr.get("degraded") is True:
                kind = "degraded_readable"
        items.append(
            {
                "kind": kind,
                "echo": echo or (detect_prompt_echo(text) if text.strip() else None),
            }
        )
    return items


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=7, help="窗口天数（默认 7）")
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/quality_monitor.json")
    )
    args = ap.parse_args()

    try:
        items = asyncio.run(_collect(args.days))
    except Exception as exc:
        print(f"[FATAL] 监控失败：{type(exc).__name__}: {exc}")
        return 2

    counts = count_of(items)
    rates = rates_of(counts)
    verdict = evaluate(rates)
    report = {
        "probe": "p51_daily_quality_monitor",
        "readonly": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "window_days": args.days,
        "counts": counts,
        "rates": rates,
        "thresholds": THRESHOLDS,
        **verdict,
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    status = "OK" if verdict["ok"] else "ALERT"
    print(
        f"[{status}] 近 {args.days} 天 {counts['total']} 条："
        f"空结论 {counts['empty']}（{rates['empty_rate']}）、"
        f"缺分级 {counts['unparseable']}（{rates['unparseable_rate']}）、"
        f"回显 {counts['echo']}（{rates['echo_rate']}）、"
        f"降级 {counts['degraded']}（{rates['degraded_rate']}）、干净 {counts['clean']}"
    )
    for alert in verdict["alerts"]:
        print(f"  ⚠️ {alert}")
    print(f"报告：{out}")
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
