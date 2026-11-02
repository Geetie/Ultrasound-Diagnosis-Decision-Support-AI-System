"""证据溯源：chunk_id → 来源文档 / 章节（A.126 / G23）。

## 缺口

结果契约的 `evidence.knowledge_ids` 只有裸 `chunk_id`（如 `kb_v1.6.0:d3#0042`），
工作区把 ID 原样列给医生，`report_service` 也拼成 ID 串 —— **没有来源文档名与章节**，
而且界面自己写着"暂未提供点击跳转"。医生无法据此回到指南原文核对。

## 做法

`knowledge_chunks` 表本来就有 `source_doc_id` 与 `section_path`（检索与评测一直在用），
只是没被读路径取出来。这里做两件事：

1. `load_evidence_sources(db, chunk_ids)`：一次 `IN` 查询，返回
   ``{chunk_id: {"source_doc_id": ..., "section_path": ...}}``；**查不到的 chunk 不编造**
   （缺键 → 前端回落显示裸 ID）。
2. 送达层只做投影（白名单两个字段），不把整行元数据泼给前端。

失败语义：DB 查询异常时返回空 dict 并留 warning —— 溯源是**增强信息**，不能让
"结果接口 500"这种更严重的后果发生（与 A.88 的旁路字段同一种取舍）。
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog

logger = structlog.get_logger("app.services.evidence_sources")

MAX_IDS = 50


async def load_evidence_sources(
    db: Any, chunk_ids: Iterable[str] | None
) -> dict[str, dict[str, str]]:
    """按 chunk_id 批量取来源文档/章节；查不到/异常 → 返回已取到的部分（可能为空）。"""
    ids = [str(c) for c in (chunk_ids or []) if str(c).strip()]
    if not ids:
        return {}
    ids = list(dict.fromkeys(ids))[:MAX_IDS]

    from sqlalchemy import select

    from app.models.database import KnowledgeChunkModel

    try:
        result = await db.execute(
            select(
                KnowledgeChunkModel.chunk_id,
                KnowledgeChunkModel.source_doc_id,
                KnowledgeChunkModel.section_path,
            ).where(KnowledgeChunkModel.chunk_id.in_(ids))
        )
        rows = result.all()
    except Exception as exc:  # silent-ok: 溯源是增强信息，失败只留 warning 并回落裸 ID
        logger.warning(
            "evidence_sources_query_failed",
            error=str(exc)[:160],
            error_type=type(exc).__name__,
            requested=len(ids),
        )
        return {}

    out: dict[str, dict[str, str]] = {}
    for chunk_id, doc_id, section_path in rows:
        entry: dict[str, str] = {}
        if doc_id:
            entry["source_doc_id"] = str(doc_id)
        if section_path:
            entry["section_path"] = str(section_path)
        if entry:
            out[str(chunk_id)] = entry
    return out
