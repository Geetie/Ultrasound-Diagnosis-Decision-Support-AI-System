"""A.72：**现状四个硬问题的一次性只读快照**（全部真机数字，不猜）。

回答：
1. 现在把数据喂进去，**能完整产出结论的到底多少**（库内 411 条按种类拆 + 近 7/30 天）；
2. **测试数据长什么样**：临床文本长度、影像条数、影像是否真有文件/是什么模态、检索上下文条数；
3. **知识库各器官的家底**：按器官 × 来源类型（指南/对话）的块数、文档数、平均 token；
4. （归因见 16 号 A.70.7/A.70.9，本脚本只复核"当前还剩多少"）

用法（服务器）：
    ~/uai/venv/bin/python scripts/audit/run_with_env.py scripts/audit/p53_current_state_snapshot.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

_SCRIPTS = pathlib.Path(__file__).resolve().parents[1]
for _p in (str(_SCRIPTS), str(_SCRIPTS / "audit"), str(_SCRIPTS / "langfuse")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from collect_eval_set import _conclusion_of  # noqa: E402
from p49_degraded_cause import degraded_kind  # noqa: E402

#: 「能完整产出结论」= 非空、非降级串、且不是模板回显
_UNPARSEABLE = ("grade_unparseable", "lvef_unparseable")


def usable(conclusion: str) -> bool:
    """是否能算一条"完整结论"。纯函数（守卫钉住）。"""
    text = (conclusion or "").strip()
    if not text:
        return False
    return degraded_kind(text) == "not_degraded"


async def _snapshot(days_focus: int) -> dict:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import (
        DiagnosisResult,
        DicomImage,
        KnowledgeChunkModel,
    )

    out: dict = {}
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    DiagnosisResult.request_id,
                    DiagnosisResult.organ,
                    DiagnosisResult.created_at,
                    DiagnosisResult.clinical_text,
                    DiagnosisResult.image_ids,
                    DiagnosisResult.patient_context,
                    DiagnosisResult.findings,
                    DiagnosisResult.confidence,
                ).order_by(DiagnosisResult.created_at)
            )
        ).all()

        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=days_focus)

        kinds: dict[str, int] = defaultdict(int)
        by_organ: dict[str, dict] = defaultdict(lambda: {"total": 0, "usable": 0})
        recent = {"total": 0, "usable": 0}
        samples: list[dict] = []
        image_ids_all: list[str] = []
        # 「测试数据是不是完整的问话+影像」：分带图/不带图、文本长度档、各档可用率
        shape: dict = {
            "with_image": 0,
            "without_image": 0,
            "usable_with_image": 0,
            "usable_without_image": 0,
            "text_len": {"<30": 0, "30-100": 0, ">100": 0},
        }

        for (rid, organ, created, ctext, imgs, pctx, findings, conf) in rows:
            text = _conclusion_of(findings)
            kind = "usable" if usable(text) else degraded_kind(text)
            kinds[kind] += 1
            slot = by_organ[str(organ or "")]
            slot["total"] += 1
            slot["usable"] += 1 if kind == "usable" else 0

            created_aware = created
            if created_aware is not None and created_aware.tzinfo is None:
                created_aware = created_aware.replace(tzinfo=timezone.utc)
            if created_aware is not None and created_aware >= cutoff:
                recent["total"] += 1
                recent["usable"] += 1 if kind == "usable" else 0

            tl = len(ctext or "")
            bucket = "<30" if tl < 30 else ("30-100" if tl <= 100 else ">100")
            shape["text_len"][bucket] += 1
            if imgs:
                image_ids_all.extend([str(i) for i in imgs])
                shape["with_image"] += 1
                shape["usable_with_image"] += 1 if kind == "usable" else 0
            else:
                shape["without_image"] += 1
                shape["usable_without_image"] += 1 if kind == "usable" else 0
            if len(samples) < 6:
                samples.append(
                    {
                        "request_id": str(rid),
                        "organ": str(organ or ""),
                        "kind": kind,
                        "clinical_text_len": len(ctext or ""),
                        "clinical_text_head": (ctext or "")[:80].replace("\n", " "),
                        "image_ids": len(imgs or []),
                        "patient_context_keys": sorted((pctx or {}).keys())
                        if isinstance(pctx, dict)
                        else [],
                        "conclusion_len": len(text),
                        "confidence": float(conf or 0.0),
                        "findings_keys": sorted(findings.keys())
                        if isinstance(findings, dict)
                        else [],
                    }
                )

        out["diagnosis"] = {
            "total": len(rows),
            "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
            "usable": kinds.get("usable", 0),
            "usable_rate": round(kinds.get("usable", 0) / len(rows), 4) if rows else None,
            "by_organ": {k: v for k, v in sorted(by_organ.items())},
            f"last_{days_focus}d": {
                **recent,
                "usable_rate": round(recent["usable"] / recent["total"], 4)
                if recent["total"]
                else None,
            },
            "shape": shape,
            "samples": samples,
        }

        # 影像：被引用到的影像里，有多少条真在库里、什么模态、多大
        uniq = sorted(set(image_ids_all))
        img_rows = []
        if uniq:
            img_rows = (
                await session.execute(
                    select(
                        DicomImage.image_id,
                        DicomImage.modality,
                        DicomImage.storage_path,
                        DicomImage.size_bytes,
                        DicomImage.width,
                        DicomImage.height,
                    ).where(DicomImage.image_id.in_(uniq))
                )
            ).all()
        modalities: dict[str, int] = defaultdict(int)
        for r in img_rows:
            modalities[str(r[1] or "(空)")] += 1
        out["images"] = {
            "referenced_unique": len(uniq),
            "found_in_table": len(img_rows),
            "missing": len(uniq) - len(img_rows),
            "modalities": dict(modalities),
            "with_storage_path": sum(1 for r in img_rows if (r[2] or "").strip()),
            "with_pixels": sum(1 for r in img_rows if (r[4] or 0) and (r[5] or 0)),
        }

        # 知识库：器官 × 来源类型
        chunks = (
            await session.execute(
                select(
                    KnowledgeChunkModel.organ,
                    KnowledgeChunkModel.source_doc_id,
                    KnowledgeChunkModel.token_count,
                ).where(KnowledgeChunkModel.kb_version == "kb_v1.3.0")
            )
        ).all()
        kb: dict[str, dict] = defaultdict(
            lambda: {"chunks": 0, "docs": set(), "tokens": 0}
        )
        for organ, doc, tok in chunks:
            kind = "guideline" if str(doc).startswith("pdf_") else "dialogue"
            slot = kb[f"{organ}/{kind}"]
            slot["chunks"] += 1
            slot["docs"].add(str(doc))
            slot["tokens"] += int(tok or 0)
        out["kb"] = {
            key: {
                "chunks": v["chunks"],
                "docs": len(v["docs"]),
                "avg_tokens": round(v["tokens"] / v["chunks"], 1) if v["chunks"] else None,
            }
            for key, v in sorted(kb.items())
        }
        out["kb_total"] = {
            "chunks": sum(v["chunks"] for v in kb.values()),
            "docs": len({d for v in kb.values() for d in v["docs"]}),
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--report", default=str(pathlib.Path.home() / "uai/reports/current_state.json")
    )
    ap.add_argument("--days", type=int, default=7, help="近期窗口（默认 7 天）")
    args = ap.parse_args()

    try:
        snap = asyncio.run(_snapshot(args.days))
    except Exception as exc:
        print(f"[FATAL] 快照失败：{type(exc).__name__}: {exc}")
        return 2

    d = snap["diagnosis"]
    print(f"== 1) 能完整出结论的：{d['usable']}/{d['total']}（{d['usable_rate']}）")
    print(f"   种类分布：{d['kinds']}")
    print(f"   近 {args.days} 天：{d[f'last_{args.days}d']}")
    print("   按器官：")
    for organ, slot in d["by_organ"].items():
        rate = round(slot["usable"] / slot["total"], 4) if slot["total"] else None
        print(f"     {organ:<10} {slot['usable']}/{slot['total']}（{rate}）")

    sh = d["shape"]
    print(
        f"   形态：带图 {sh['with_image']} 条（可用 {sh['usable_with_image']}）、"
        f"不带图 {sh['without_image']} 条（可用 {sh['usable_without_image']}）；"
        f"文本长度 {sh['text_len']}"
    )

    img = snap["images"]
    print(f"\n== 2) 影像：被引用 {img['referenced_unique']} 个唯一 ID，"
          f"表里找到 {img['found_in_table']}，缺失 {img['missing']}")
    print(f"   模态：{img['modalities']}；有存储路径 {img['with_storage_path']}；"
          f"有宽高 {img['with_pixels']}")

    print("\n== 3) 知识库（kb_v1.3.0）：")
    for key, slot in snap["kb"].items():
        print(f"   {key:<24} 块 {slot['chunks']:>4} / 文档 {slot['docs']:>4} / 均 {slot['avg_tokens']} tok")
    print(f"   合计：块 {snap['kb_total']['chunks']} / 文档 {snap['kb_total']['docs']}")

    print("\n== 4) 样本形态（前 6 条）：")
    for s in d["samples"]:
        print(
            f"   [{s['organ']}] {s['kind']:<18} 文本 {s['clinical_text_len']:>4} 字 | "
            f"影像 {s['image_ids']} 张 | 结论 {s['conclusion_len']:>4} 字 | conf {s['confidence']}"
        )

    out = pathlib.Path(args.report).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告：{out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
