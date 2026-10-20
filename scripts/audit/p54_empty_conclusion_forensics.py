"""A.73：**空结论取证**（只读）—— 358 条"什么都没有"的记录，`findings` 里到底是什么。

**为什么要专门查**：`p53` 把"空结论"统一记成 `empty`，但落库代码有多条完全不同的路径：
* `findings={"status": "processing"}`（写入口占位，**如果更新失败就永远是空**）；
* `findings={"error": ..., "error_type": ...}`（管线异常）；
* `{"fusion_result": ...}` 里 content 为空（融合层没产出）；
* 其他形态。

**修法完全不同**：占位残留要修"更新链路"，管线异常要修"对应那一步"，融合空要修生成/融合。
所以必须先按 `findings` 的**键形状**分类，再抽样看原文。

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p54_empty_conclusion_forensics.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime, timezone

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402


def shape_of(findings: object) -> str:
    """按 findings 的键形状归类。纯函数（守卫钉住）。"""
    if not isinstance(findings, dict) or not findings:
        return "findings_empty"
    keys = set(findings)
    if keys == {"status"}:
        return f"status_only:{findings.get('status')}"
    if "error" in keys:
        return f"error:{findings.get('error_type') or 'unknown'}"
    if "fusion_result" in keys:
        fr = findings.get("fusion_result")
        if not isinstance(fr, dict):
            return "fusion_not_dict"
        return "fusion_no_content" if not (fr.get("content") or "").strip() else "has_content"
    return "other:" + ",".join(sorted(keys)[:3])


async def _collect(limit: int) -> tuple[list[dict], list[dict]]:
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
                    DiagnosisResult.confidence,
                    DiagnosisResult.image_ids,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

    buckets: dict[str, dict] = defaultdict(lambda: {"n": 0, "with_image": 0, "first": None, "last": None})
    samples: list[dict] = []
    for rid, organ, created, findings, conf, imgs in rows:
        text = _conclusion_of(findings)
        if (text or "").strip():
            continue
        shape = shape_of(findings)
        slot = buckets[shape]
        slot["n"] += 1
        if imgs:
            slot["with_image"] += 1
        day = created.strftime("%Y-%m-%d") if created else None
        if day:
            slot["first"] = min(filter(None, [slot["first"], day])) if slot["first"] else day
            slot["last"] = max(filter(None, [slot["last"], day])) if slot["last"] else day
        if len(samples) < limit:
            raw = json.dumps(findings, ensure_ascii=False)[:400] if findings else "null"
            samples.append(
                {
                    "request_id": str(rid),
                    "organ": str(organ or ""),
                    "shape": shape,
                    "confidence": float(conf or 0.0),
                    "images": len(imgs or []),
                    "day": day,
                    "raw_findings": raw,
                }
            )
    out = [{"shape": k, **v} for k, v in sorted(buckets.items(), key=lambda kv: -kv[1]["n"])]
    return out, samples


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/empty_forensics.json")
    )
    ap.add_argument("--samples", type=int, default=6)
    args = ap.parse_args()

    try:
        buckets, samples = asyncio.run(_collect(args.samples))
    except Exception as exc:
        print(f"[FATAL] 取证失败：{type(exc).__name__}: {exc}")
        return 2

    print("== 空结论按 findings 形状分类 ==")
    for b in buckets:
        print(
            f"   {b['shape']:<28} {b['n']:>4} 条（带图 {b['with_image']}）"
            f"  {b['first']} → {b['last']}"
        )
    print("\n== 样本原文（前几条）==")
    for s in samples:
        print(f"   [{s['day']}][{s['organ']}] conf={s['confidence']} 图={s['images']} {s['shape']}")
        print(f"     {s['raw_findings']}")

    report = {
        "probe": "p54_empty_conclusion_forensics",
        "readonly": True,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
        "buckets": buckets,
        "samples": samples,
    }
    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
