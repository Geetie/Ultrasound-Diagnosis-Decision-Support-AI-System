"""A.74：**数据链逐环审计**（只读）—— 每条判读死在哪一环，各环死多少。

**为什么要有这个**：A.62~A.73 我一直在算"指标"（faithfulness、引用率），
却没有把**一条请求的完整链路**拆开逐环检查：

    输入 → 检索 → 生成 → 解析(分级/EF) → 融合 → 落库 → 展示

结果就是"看到 2.9%、98.7% 这些数字，但说不清它们死在哪一环"。
本脚本把每一环做成**可判定的检查点**，输出"首个失败环"的分布 —— 修哪一环，一眼可见。

检查点（按顺序，取**第一个**失败环）
1. `input`：临床文本为空/<10 字；带图请求则要求 `image_ids` 非空
2. `retrieval`：`knowledge_ids` 为空（检索没有任何块）
3. `generate`：`findings` 里有 `error`（生成/管线异常）或 `llm_unavailable` 文本
4. `parse`：`grade_unparseable` / `lvef_unparseable`（有输出但抽不出分级/EF）
5. `fusion`：`fusion_result` 存在但 `content` 为空
6. `persist`：`findings` 为 NULL 或只有 `{"status":"processing"}`（终态没写）
7. `display`：能取到正文但 `degraded=True`（可读降级，不是实质结论）
8. `ok`：以上都过

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p55_chain_audit.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import defaultdict

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402

#: 链路环节（顺序即优先级：取第一个失败）
LINKS = ("input", "retrieval", "generate", "parse", "fusion", "persist", "display", "ok")

#: 文本过短阈值（字）
MIN_TEXT_CHARS = 10


def classify(
    clinical_text: str,
    image_ids: list | None,
    knowledge_ids: list | None,
    findings: object,
) -> tuple[str, str]:
    """判定一条记录**首个失败环**与原因。纯函数（守卫钉住）。

    Returns: ``(link, reason)``；``link == "ok"`` 表示全链路通过。
    """
    text = (clinical_text or "").strip()
    if len(text) < MIN_TEXT_CHARS and not (image_ids or []):
        return "input", "no_text_no_image"
    if not (image_ids or []):
        return "input", "no_image"

    if not (knowledge_ids or []):
        return "retrieval", "no_chunks"

    if not isinstance(findings, dict) or not findings:
        return "persist", "findings_null"

    if set(findings) == {"status"} and findings.get("status") == "processing":
        return "persist", "stuck_processing"

    if "error" in findings:
        return "generate", f"error:{findings.get('error_type') or 'unknown'}"

    body = _conclusion_of(findings)
    lowered = body.lower()
    if "llm_unavailable" in lowered:
        return "generate", "llm_unavailable"
    if "grade_unparseable" in lowered:
        return "parse", "grade_unparseable"
    if "lvef_unparseable" in lowered:
        return "parse", "lvef_unparseable"
    if "evidence_insufficient" in lowered:
        return "retrieval", "evidence_insufficient"

    fr = findings.get("fusion_result")
    if isinstance(fr, dict) and not (fr.get("content") or "").strip():
        return "fusion", "fusion_empty"
    if not body.strip():
        return "fusion", "no_content"

    if isinstance(fr, dict) and fr.get("degraded") is True:
        return "display", f"degraded:{fr.get('reason') or 'unknown'}"

    return "ok", ""


def summarize(rows: list[dict]) -> dict:
    """按"首个失败环"汇总。纯函数（守卫钉住）。

    ``rows`` 元素需含 ``link`` / ``reason`` / ``organ`` / ``has_image``。
    """
    by_link: dict[str, int] = defaultdict(int)
    by_reason: dict[str, int] = defaultdict(int)
    by_organ: dict[str, dict] = defaultdict(lambda: {"total": 0, "ok": 0})
    with_image = {"total": 0, "ok": 0}
    for r in rows:
        by_link[r["link"]] += 1
        if r["link"] != "ok":
            by_reason[f"{r['link']}:{r['reason']}"] += 1
        slot = by_organ[str(r.get("organ") or "")]
        slot["total"] += 1
        slot["ok"] += 1 if r["link"] == "ok" else 0
        if r.get("has_image"):
            with_image["total"] += 1
            with_image["ok"] += 1 if r["link"] == "ok" else 0
    total = len(rows)
    return {
        "total": total,
        "by_link": {k: by_link.get(k, 0) for k in LINKS},
        "link_share": {
            k: (round(by_link.get(k, 0) / total, 4) if total else None) for k in LINKS
        },
        "top_reasons": dict(sorted(by_reason.items(), key=lambda kv: -kv[1])[:10]),
        "by_organ": {
            k: {**v, "ok_rate": round(v["ok"] / v["total"], 4) if v["total"] else None}
            for k, v in sorted(by_organ.items())
        },
        "with_image": {
            **with_image,
            "ok_rate": round(with_image["ok"] / with_image["total"], 4)
            if with_image["total"]
            else None,
        },
    }


async def _collect() -> list[dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import DiagnosisResult

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.clinical_text,
                    DiagnosisResult.image_ids,
                    DiagnosisResult.knowledge_ids,
                    DiagnosisResult.findings,
                    DiagnosisResult.status,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

    out: list[dict] = []
    for request_id, organ, ctext, imgs, kids, findings, status in rows:
        link, reason = classify(ctext, imgs, kids, findings)
        out.append(
            {
                "request_id": str(request_id),
                "organ": str(organ or ""),
                "status": str(status or ""),
                "has_image": bool(imgs),
                "n_images": len(imgs or []),
                "n_chunks": len(kids or []),
                "link": link,
                "reason": reason,
            }
        )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default=str(pathlib.Path.home() / "uai/reports/chain_audit.json"))
    args = ap.parse_args()

    try:
        rows = asyncio.run(_collect())
    except Exception as exc:
        print(f"[FATAL] 审计失败：{type(exc).__name__}: {exc}")
        return 2

    summary = summarize(rows)
    print("== 数据链逐环（首个失败环）==")
    for link in LINKS:
        n = summary["by_link"][link]
        share = summary["link_share"][link]
        bar = "█" * min(40, int((share or 0) * 40))
        print(f"   {link:<10} {n:>4} 条 ({share}) {bar}")
    print(f"\n   总计 {summary['total']} 条；带图子集 "
          f"{summary['with_image']['ok']}/{summary['with_image']['total']} 全链路通过"
          f"（{summary['with_image']['ok_rate']}）")
    print("\n== 失败原因 Top10 ==")
    for reason, n in summary["top_reasons"].items():
        print(f"   {reason:<28} {n}")
    print("\n== 按器官（全链路通过率）==")
    for organ, slot in summary["by_organ"].items():
        print(f"   {organ:<10} {slot['ok']}/{slot['total']}（{slot['ok_rate']}）")

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
