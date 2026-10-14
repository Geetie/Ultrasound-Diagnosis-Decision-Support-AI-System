"""Langfuse 评测集采集（19 号计划：把「双引擎评测集 ≥700 例」变成可持续运营的资产）。

采集源 = **业务库**（不是 trace 接口）
--------------------------------------
19 号要的是「输入 + 输出 + **正确口径**」三件套，其中"正确口径"只存在于医生的
修正反馈里（`feedback_records` 表，`feedback_type='correction'`，payload 键
`field/original_value/corrected_value/reason`，口径与 `app/services/correction_rate_monitor.py`
一致）。因此本脚本以业务库为唯一事实源：

| 用途 | 表 / 模型 | 关键列 |
|---|---|---|
| 输入 | `diagnosis_results`（`DiagnosisResult`） | `clinical_text` / `organ` / `patient_context` / `image_ids` |
| 输出 | 同上，`findings` JSONB 里的 `fusion_result.content` | 结论正文；`confidence` |
| 正确口径 | `feedback_records`（`FeedbackRecord`） | `feedback_type='correction'` → `content.{field,original_value,corrected_value,reason}` |
| **检索上下文** | `knowledge_chunks`（`KnowledgeChunkModel`） | 按 `diagnosis_results.knowledge_ids` 取 chunk 正文（**L2 忠实度判官的必要输入**） |
| 去重键 | `DiagnosisSession.patient_id` + `dicom_images`（`DicomImage`） | 患者 ID（会话为准，回退影像） + `sop_instance_uid` → `image_id` → `storage_path`；口径 = **同患者同影像** |

去重键 = `(patient_id, 影像标识)`：**同一患者的同一张影像只入集一次**（多帧/多次判读
不算新样本）。影像标识缺失者**显式计入 `undedupable`**（不静默丢弃、也不假装去重过）。

⚠️ 关于 `image_sha256`：业务代码里**不存在**字节级 sha256（A.61 全仓核实）。
本脚本用 `DicomImage.sop_instance_uid`（DICOM 原生稳定标识）优先，退化到 `image_id` /
`storage_path`，并在报告里如实标注用了哪一级（`dedup_basis`）。

用法（服务器，`PYTHONPATH=~/uai/src`）：
    python scripts/langfuse/collect_eval_set.py \
        --out ~/uai/data/eval/double_engine_eval_set.jsonl \
        --report ~/uai/reports/langfuse_eval_set.json

退出码：0 = 采集完成（含未达目标，进度如实打印）；1 = 达到 ≥700 目标；2 = 缺输入/库不可达。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

DEFAULT_TARGET_TOTAL = 700
DEFAULT_TARGET_DOCTOR_LABELED = 50

#: 每条样本最多保留几个检索块、每块最多多少字符（L2 判官的输入；防止 JSONL 膨胀）
_MAX_CONTEXT_CHUNKS = 5
_MAX_CONTEXT_CHARS = 600


def _conclusion_of(findings: object) -> str:
    """从 `diagnosis_results.findings` 里取**结论正文**（与前端消费同一字段）。"""
    if not isinstance(findings, dict):
        return ""
    fusion = findings.get("fusion_result")
    if isinstance(fusion, dict):
        content = fusion.get("content")
        if isinstance(content, str) and content.strip():
            return content
    # 退化：报告正文（`rendered`）不在此处，按 recommendations 兜底（如实标注 source）
    rec = findings.get("recommendations")
    return rec if isinstance(rec, str) else ""


def _dedup_key(patient_id: str, basis_value: str, request_id: str) -> tuple[str, str]:
    """去重键（**纯函数**，便于单测）：口径 = 同患者同影像。

    | 输入 | 键 | reason |
    |---|---|---|
    | 有患者 ID + 有影像标识 | `患者|影像标识` | `patient_image` |
    | 无患者 ID + 有影像标识 | `img:影像标识` | `image_only`（**降级**：同一患者的多张影像不再合并，计数偏大） |
    | 无影像标识 | `req:request_id` | `no_basis`（退化到请求级，调用方计入 `undedupable`） |

    为什么要显式返回 `reason`：A.61 版只判断"键长什么样"，于是「`patient_id` 恒为空」这个
    真 bug 被"看起来有键"掩盖了（2026-10-14 复核才暴露，见 16 号 A.62.11）。
    """
    if basis_value:
        if patient_id:
            return f"{patient_id}|{basis_value}", "patient_image"
        return f"img:{basis_value}", "image_only"
    return f"req:{request_id}", "no_basis"


def _image_basis(image_row: object) -> tuple[str, str]:
    """返回 (影像标识, 依据名)。优先级：SOPInstanceUID > image_id > storage_path。"""
    for attr, name in (
        ("sop_instance_uid", "sop_instance_uid"),
        ("image_id", "image_id"),
        ("storage_path", "storage_path"),
    ):
        val = getattr(image_row, attr, None)
        if isinstance(val, str) and val.strip():
            return val.strip(), name
    return "", "none"


async def _collect(args) -> tuple[list[dict], dict]:
    from sqlalchemy import select

    from app.config.database import AsyncSessionLocal
    from app.models.database import (
        DiagnosisResult,
        DiagnosisSession,
        DicomImage,
        FeedbackRecord,
        KnowledgeChunkModel,
    )

    rows: list[dict] = []
    dedup_basis = defaultdict(int)
    undedupable = 0
    skipped_no_image = 0
    patient_id_missing = 0
    dedup_reason: dict[str, int] = defaultdict(int)

    async with AsyncSessionLocal() as session:
        # ① 修正反馈：request_id → 正确口径（可能多条，全部保留）
        fb_rows = (await session.execute(select(FeedbackRecord))).scalars().all()
        corrections: dict[str, list[dict]] = defaultdict(list)
        for fb in fb_rows:
            if str(getattr(fb, "feedback_type", "")) != "correction":
                continue
            content = getattr(fb, "content", None)
            if not isinstance(content, dict):
                continue
            if content.get("original_value") is None or content.get("corrected_value") is None:
                continue
            corrections[str(fb.request_id)].append(
                {
                    "field": str(content.get("field") or ""),
                    "original_value": str(content.get("original_value")),
                    "corrected_value": str(content.get("corrected_value")),
                    "reason": str(content.get("reason") or ""),
                    "reviewer_id": str(getattr(fb, "reviewer_id", "") or "unknown"),
                    "feedback_level": str(getattr(fb, "feedback_level", "") or ""),
                }
            )

        # ② 影像：image_id → (影像标识, 依据)
        img_rows = (await session.execute(select(DicomImage))).scalars().all()
        image_basis: dict[str, tuple[str, str]] = {
            str(r.image_id): _image_basis(r) for r in img_rows
        }
        # ②a 会话 → 患者：**`DiagnosisResult` 没有 `patient_id` 列**（患者在 `DiagnosisSession` 上）。
        # A.61 版只读 `patient_context.patient_id`（探针数据里为空）→ `patient_id` 恒空 →
        # 去重退化成「按影像」而不是设计口径「同患者同影像」（2026-10-14 复核发现，见 16 号 A.62.11）。
        sess_rows = (
            await session.execute(
                select(DiagnosisSession.session_id, DiagnosisSession.patient_id)
            )
        ).all()
        session_patient: dict[str, str] = {
            str(sid): str(pid or "") for sid, pid in sess_rows
        }
        image_patient: dict[str, str] = {
            str(r.image_id): str(getattr(r, "patient_id", "") or "") for r in img_rows
        }

        # ②b 检索块：chunk_id → 正文（L2 忠实度判官的输入；KB 规模 ~1.3k 块，直接全量取）
        chunk_rows = (
            await session.execute(
                select(KnowledgeChunkModel.chunk_id, KnowledgeChunkModel.content)
            )
        ).all()
        chunk_text = {str(cid): (content or "") for cid, content in chunk_rows}

        # ③ 判读结果（输出来源）
        stmt = select(DiagnosisResult)
        if args.since:
            stmt = stmt.where(DiagnosisResult.created_at >= args.since)
        res_rows = (await session.execute(stmt)).scalars().all()

        for r in res_rows:
            ctx = getattr(r, "patient_context", None)
            patient_id = ""
            if isinstance(ctx, dict):
                patient_id = str(ctx.get("patient_id") or "")
            basis_name = "none"
            basis_value = ""
            basis_image = ""
            for iid in getattr(r, "image_ids", None) or []:
                ident, basis = image_basis.get(str(iid), ("", "none"))
                if ident:
                    basis_name, basis_value, basis_image = basis, ident, str(iid)
                    break
            # 患者 ID 三级回退：判读自带的患者上下文 → **会话**（权威）→ 影像记录
            if not patient_id:
                patient_id = session_patient.get(str(getattr(r, "session_id", "")), "")
            if not patient_id and basis_image:
                patient_id = image_patient.get(basis_image, "")
            if not patient_id:
                patient_id_missing += 1
            # 「双引擎」评测样本必须有影像：无影像的判读（纯文本腿）**不进评测集**，
            # 但必须**显式计数**（静默丢样本与静默失败同罪）。
            if not getattr(r, "image_ids", None):
                skipped_no_image += 1
                continue
            dedup_key, reason = _dedup_key(patient_id, basis_value, str(r.request_id))
            dedup_reason[reason] += 1
            if reason == "no_basis":
                # 有影像但取不到稳定标识（sop/image_id/storage_path 全空）—— 退化到请求级，
                # 显式计入 undedupable（说明这批数据没法按"同患者同影像"去重）
                undedupable += 1
            dedup_basis[basis_name] += 1

            corrections_for = corrections.get(str(r.request_id), [])
            # 检索上下文（判官输入）：按 knowledge_ids 顺序取，最多 N 块、每块截断
            ctx_ids = [str(cid) for cid in (getattr(r, "knowledge_ids", None) or [])]
            ctx_text = [
                chunk_text[cid][:_MAX_CONTEXT_CHARS]
                for cid in ctx_ids[:_MAX_CONTEXT_CHUNKS]
                if chunk_text.get(cid)
            ]
            rows.append(
                {
                    "dedup_key": dedup_key,
                    "patient_id": patient_id,
                    "request_id": str(r.request_id),
                    "session_id": str(r.session_id),
                    "organ": str(getattr(r, "organ", "") or ""),
                    "clinical_text": str(getattr(r, "clinical_text", "") or ""),
                    "conclusion": _conclusion_of(getattr(r, "findings", None)),
                    "confidence": float(getattr(r, "confidence", 0.0) or 0.0),
                    "status": str(getattr(r, "status", "") or ""),
                    "context_ids": ctx_ids[:_MAX_CONTEXT_CHUNKS],
                    "context": ctx_text,
                    "corrections": corrections_for,
                    "source": "doctor_correction" if corrections_for else "diagnosis_only",
                    "created_at": (
                        r.created_at.isoformat() if getattr(r, "created_at", None) else None
                    ),
                }
            )

    # ④ 去重：同一 (patient, 影像) 只保留**最新**一次判读；有医生修正的优先保留
    def _sort_key(item: dict) -> tuple:
        return (item["created_at"] or "", bool(item["corrections"]))

    best: dict[str, dict] = {}
    for item in rows:
        cur = best.get(item["dedup_key"])
        if cur is None or _sort_key(item) > _sort_key(cur):
            best[item["dedup_key"]] = item
    deduped = sorted(best.values(), key=lambda x: x["created_at"] or "")

    stats = {
        "total": len(rows),
        "deduped": len(deduped),
        "undedupable": undedupable,
        "skipped_no_image": skipped_no_image,
        "context_present": sum(1 for x in deduped if x["context"]),
        # 资产质量指标：**不同患者数**决定「700 例」到底代表多少独立病例；
        # `patient_id_missing` 是口径告警（>0 说明去重退化成"按影像"，计数会偏大）
        "patient_count": len({x["patient_id"] for x in deduped if x["patient_id"]}),
        "patient_id_missing": patient_id_missing,
        "dedup_reason": dict(dedup_reason),
        "doctor_labeled": sum(1 for x in deduped if x["corrections"]),
        "dedup_basis": dict(dedup_basis),
        "targets": {
            "total": args.target,
            "doctor_labeled": args.target_doctor_labeled,
        },
    }
    return deduped, stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(Path.home() / "uai/data/eval/double_engine_eval_set.jsonl"))
    ap.add_argument("--report", default=str(Path.home() / "uai/reports/langfuse_eval_set.json"))
    ap.add_argument("--target", type=int, default=DEFAULT_TARGET_TOTAL)
    ap.add_argument("--target-doctor-labeled", type=int, default=DEFAULT_TARGET_DOCTOR_LABELED)
    ap.add_argument("--since", default=None, help="ISO8601；只取该时间之后的判读")
    ap.add_argument("--limit", type=int, default=0, help=">0 时只保留前 N 条（冒烟用）")
    args = ap.parse_args()

    try:
        deduped, stats = asyncio.run(_collect(args))
    except Exception as exc:  # 库不可达/字段漂移都必须**显式失败**，不静默给 0
        print(f"采集失败：{type(exc).__name__}: {exc}")
        return 2

    if args.limit:
        deduped = deduped[: args.limit]
        stats["limited_to"] = args.limit

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for row in deduped:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    report = {
        "generated_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
        "source": "business_db(diagnosis_results + feedback_records + dicom_images)",
        **stats,
        "out": str(out),
    }
    rp = Path(args.report).expanduser()
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    t_total, t_doc = stats["targets"]["total"], stats["targets"]["doctor_labeled"]
    print(f"评测集：{stats['deduped']} 条（原始判读 {stats['total']}，"
          f"无影像跳过 {stats['skipped_no_image']}，有影像但无稳定标识 {stats['undedupable']}，"
          f"含检索上下文 {stats['context_present']}）→ {out}")
    print(f"资产质量：不同患者 {stats['patient_count']} 人 · "
          f"去重口径 {stats['dedup_reason']}"
          f"（`image_only`>0 表示患者 ID 缺失、去重已降级为按影像）")
    if stats["patient_id_missing"]:
        print(f"  ⚠️ {stats['patient_id_missing']} 条判读取不到患者 ID —— 已按会话/影像回退，"
              "仍缺的会体现在 `image_only` 计数里（口径告警，不是静默）")
    print(f"进度：样本 {stats['deduped']}/{t_total}（{stats['deduped'] / t_total:.1%}）·"
          f" 医生标注坏例 {stats['doctor_labeled']}/{t_doc}"
          f"（{stats['doctor_labeled'] / t_doc:.1%}）")
    print(f"去重依据分布：{stats['dedup_basis']}   报告：{rp}")
    return 1 if stats["deduped"] >= t_total else 0


if __name__ == "__main__":
    sys.exit(main())
