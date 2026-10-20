"""A.70.7：**降级成因**只读排查（451/1071 条请求没有产出结论）。

**问题**：全库 42% 的判读是降级答案（`llm_unavailable`），比"模板回显"更影响可用性 ——
回显至少还有输出，降级是**什么都没给**。

**两条证据线（都只读）**

1. **记录侧**：从结论文本里抽异常类型（`llm_unavailable: <异常名>`）+ 按天/器官分布。
   注意：这里的异常名是**包装层类型**（多为 `InferenceError`），不是根因；
2. **日志侧**：扫 `~/uai/logs/*.log`，按"根因签名"计数（超时 / 显存 / 连接失败 / 5xx / 队列满…），
   并给出每天命中次数 —— 用来判断"某几个时段集中坏"还是"一直坏"。

**不做**：不改配置、不重启、不清理日志（纯读）。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/p49_degraded_cause.py --report ~/uai/reports/degraded_cause.json

退出码：0 出数；2 库不可达。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import re
import sys
from collections import defaultdict

#: 从降级文本里抽异常名：`[thyroid] llm_unavailable: InferenceError; manual review required`
_EXC_NAME = re.compile(r"llm_unavailable:\s*([A-Za-z_][\w.]*)")

#: 降级的**种类**（英文技术串形态；A.59 之后部分已改中文可读文案）
_DEGRADED_KINDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("llm_unavailable", re.compile(r"llm_unavailable", re.I)),
    # A.70.7 实测：这两类是**内容问题**（模型输出里没有可解析的分级/EF），
    # 与"服务挂了"完全不同，必须分开数，否则会误判成"服务不稳"。
    ("grade_unparseable", re.compile(r"grade_unparseable", re.I)),
    ("lvef_unparseable", re.compile(r"lvef_unparseable", re.I)),
    ("evidence_insufficient", re.compile(r"evidence_insufficient", re.I)),
    ("parse_failed", re.compile(r"parse_failed", re.I)),
    ("specialist_unavailable", re.compile(r"specialist_unavailable", re.I)),
    ("pipeline_unavailable", re.compile(r"pipeline_unavailable", re.I)),
)


def degraded_kind(text: str) -> str:
    """降级种类；空结论单独记 ``empty``（**它不是同一种故障**，要分开数）。纯函数。"""
    t = text or ""
    if not t.strip():
        return "empty"
    for name, pattern in _DEGRADED_KINDS:
        if pattern.search(t):
            return name
    if "manual review required" in t.lower():
        return "other_english"
    return "not_degraded"

#: 日志里的根因签名（按顺序匹配，命中即归类）
_LOG_SIGNATURES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("cuda_oom", re.compile(r"(CUDA out of memory|OutOfMemoryError|out of memory)", re.I)),
    ("timeout", re.compile(r"(TimeoutError|timed out|timeout)", re.I)),
    ("conn_refused", re.compile(r"(Connection refused|ConnectError|ConnectionError)", re.I)),
    ("http_5xx", re.compile(r"(\b50[0234]\b|Bad Gateway|Service Unavailable)", re.I)),
    ("vllm_error", re.compile(r"(vllm|AsyncLLMEngine|EngineDeadError)", re.I)),
    ("llm_unavailable", re.compile(r"llm_unavailable", re.I)),
    ("specialist_llm_failed", re.compile(r"specialist_llm_failed", re.I)),
)

#: 日志行里的日期（`2026-10-04 12:34:56` 或 `2026-10-04T12:34:56`）
_LOG_DATE = re.compile(r"(2026-\d{2}-\d{2})")


def exc_type_of(text: str) -> str:
    """从降级结论里抽异常名（抽不到返回 ``unknown``）。纯函数。"""
    m = _EXC_NAME.search(text or "")
    return m.group(1) if m else "unknown"


def summarize_records(items: list[dict]) -> dict:
    """记录侧汇总：异常类型 / 按天 / 按器官。纯函数（守卫钉住）。"""
    by_exc: dict[str, int] = defaultdict(int)
    by_kind: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    by_organ: dict[str, int] = defaultdict(int)
    for x in items:
        by_exc[str(x.get("exc") or "unknown")] += 1
        by_kind[str(x.get("kind") or "unknown")] += 1
        by_day[str(x.get("day") or "(未知)")] += 1
        by_organ[str(x.get("organ") or "(未知)")] += 1
    return {
        "total": len(items),
        "by_kind": dict(sorted(by_kind.items(), key=lambda kv: -kv[1])),
        "by_exc": dict(sorted(by_exc.items(), key=lambda kv: -kv[1])),
        "by_day": dict(sorted(by_day.items())),
        "by_organ": dict(sorted(by_organ.items(), key=lambda kv: -kv[1])),
    }


def classify_log_line(line: str) -> list[str]:
    """一行日志命中的签名（可能多个）。纯函数（守卫钉住）。"""
    return [name for name, pattern in _LOG_SIGNATURES if pattern.search(line)]


def summarize_logs(lines: list[str]) -> dict:
    """日志侧汇总：每个签名总次数 + 按天。纯函数（守卫钉住）。"""
    counts: dict[str, int] = defaultdict(int)
    by_day: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for line in lines:
        hits = classify_log_line(line)
        if not hits:
            continue
        day_match = _LOG_DATE.search(line)
        day = day_match.group(1) if day_match else "(无日期)"
        for name in hits:
            counts[name] += 1
            by_day[day][name] += 1
    return {
        "with_signature": sum(counts.values()),
        "by_signature": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        "by_day": {d: dict(v) for d, v in sorted(by_day.items())},
    }


async def _collect_records() -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "langfuse"))
    from collect_eval_set import _conclusion_of

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
        kind = degraded_kind(text)
        if kind == "not_degraded":
            continue
        items.append(
            {
                "request_id": str(request_id),
                "organ": str(organ or ""),
                "day": created_at.strftime("%Y-%m-%d") if created_at else "",
                "kind": kind,
                "exc": exc_type_of(text),
                "excerpt": (text or "")[:120],
            }
        )
    return items


def _scan_logs(
    log_dir: pathlib.Path, max_bytes: int
) -> tuple[list[str], list[str], list[str]]:
    """读日志文件（每个最多读末尾 max_bytes），返回 (行, 已读文件, 读失败文件)。

    读失败**必须留痕**（零静默纪律）：权限/文件被截断/编码异常都可能发生，
    静默跳过会让"日志里没有这个错误"变成假结论。
    """
    files = sorted(log_dir.glob("*.log"))
    lines: list[str] = []
    used: list[str] = []
    skipped: list[str] = []
    for path in files:
        try:
            size = path.stat().st_size
            with path.open("rb") as fh:
                if max_bytes and size > max_bytes:
                    fh.seek(size - max_bytes)
                data = fh.read()
        except OSError as exc:  # silent-ok: 读失败已记入 skipped，并在报告 unreadable_files 列出
            skipped.append(f"{path.name}:{type(exc).__name__}")
            continue
        used.append(path.name)
        lines.extend(data.decode("utf-8", errors="replace").splitlines())
    return lines, used, skipped


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/degraded_cause.json")
    )
    ap.add_argument("--log-dir", default=str(pathlib.Path.home() / "uai/logs"))
    ap.add_argument("--max-bytes", type=int, default=40 * 1024 * 1024, help="每个日志读末尾多少字节")
    args = ap.parse_args()

    try:
        items = asyncio.run(_collect_records())
    except Exception as exc:
        print(f"[FATAL] 取降级记录失败：{type(exc).__name__}: {exc}")
        return 2

    records = summarize_records(items)
    lines, used, skipped = _scan_logs(pathlib.Path(args.log_dir).expanduser(), args.max_bytes)
    logs = summarize_logs(lines)

    report = {
        "probe": "p49_degraded_cause",
        "readonly": True,
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "records": records,
        "logs": {
            **logs,
            "files": used,
            "lines_scanned": len(lines),
            "unreadable_files": skipped,
        },
        "samples": items[:5],
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"无结论记录 {records['total']} 条；**按种类**：{records['by_kind']}")
    print(f"   异常类型：{records['by_exc']}")
    print(f"   按天（前 6）：{dict(list(records['by_day'].items())[:6])}")
    print(f"   按器官：{records['by_organ']}")
    print(f"日志：扫 {len(used)} 个文件 / {len(lines)} 行 → 命中签名 {logs['with_signature']} 次")
    print(f"   签名分布：{logs['by_signature']}")
    print(f"报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
