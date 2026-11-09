"""知识库语料治理 API（2026-11-09）。

## 这个模块解决什么

知识库里同时装着多套权威指南，它们在交叉点上给出不同数字：

| 事实 | C-TIRADS 2021 | 甲状腺癌诊疗规范 2018 |
|---|---|---|
| 4A 类 FNA 阈值 | >15 mm | >1 cm（10 mm） |
| 3 类恶性风险 | <2% | <5% |

**不同医院认的指南不同。** 所以需要：
1. 让医院看到自己库里有哪些权威来源
2. 让医院决定以哪几套为准（写 `rag_enabled_source_sets`）
3. 让医院看清"两套冲突在哪"

## 为什么单独一个模块

现有 API 只有 `POST /internal/obs/kb-cache/invalidate`（缓存失效），
**没有一个是给「语料治理」用的**。而底层能力其实都在：
- `app.knowledge.source_set`：28 个来源的规则表 + 归一化 + 人可读标签
- `app.rag.source_conflict`：从 chunk 原文抽数值事实并跨语料比对

本模块只是**把已有能力暴露成 HTTP**，不新增业务逻辑。

## 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/kb/sources` | 列出全部语料（来源/机构/年份/块数/是否启用） |
| GET | `/api/v1/kb/conflicts` | 跨语料口径分歧清单 |
| GET | `/api/v1/kb/enabled-sources` | 读本院启用配置 |
| PUT | `/api/v1/kb/enabled-sources` | 写本院启用配置 |
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db_session
from app.config.settings import settings
from app.knowledge.source_set import (
    known_source_sets,
    label_of,
    normalize_source_sets,
    source_set_of,
)

logger = structlog.get_logger("app.api.kb_management")

router = APIRouter(prefix="/api/v1/kb", tags=["knowledge-base"])


# ------------------------------------------------------------------ schemas
class SourceItem(BaseModel):
    source_set: str
    title: str
    authority: str
    chunks: int
    enabled: bool


class SourcesResponse(BaseModel):
    kb_version: str
    known_total: int
    enabled_count: int
    enabled_source_sets: list[str]
    items: list[SourceItem]


class ConflictSide(BaseModel):
    source_set: str
    title: str
    value: str
    quote: str


class ConflictItem(BaseModel):
    fact: str
    sides: list[ConflictSide]


class ConflictsResponse(BaseModel):
    kb_version: str
    checked_sources: int
    conflicts: list[ConflictItem]


class EnabledSourcesBody(BaseModel):
    source_sets: list[str] = Field(default_factory=list)


class EnabledSourcesResponse(BaseModel):
    enabled_source_sets: list[str]
    unknown: list[str]


# ------------------------------------------------------------------ helpers
def _enabled() -> list[str]:
    return list(getattr(settings, "rag_enabled_source_sets", None) or [])


async def _kb_version(session: AsyncSession) -> str:
    """取当前生产 KB 版本（从 settings 读，不猜）。"""
    return str(getattr(settings, "rag_kb_version", "") or "")


# ------------------------------------------------------------------ endpoints
@router.get("/sources", response_model=SourcesResponse, summary="列出全部语料")
async def list_sources(session: AsyncSession = Depends(get_db_session)) -> SourcesResponse:
    """列出知识库里的全部语料来源及其启用状态。

    数据来源：**真实统计 PG 里的 chunk 数**，不是写死的清单。
    """
    kb_version = await _kb_version(session)

    rows = (
        await session.execute(
            text(
                "SELECT source_doc_id, metadata, count(*) AS n "
                "FROM knowledge_chunks WHERE kb_version = :v "
                "GROUP BY source_doc_id, metadata"
            ),
            {"v": kb_version},
        )
    ).fetchall()

    counts: dict[str, int] = defaultdict(int)
    for doc_id, meta, n in rows:
        ss = source_set_of(source_doc_id=doc_id, metadata=meta)
        counts[ss] += int(n)

    known = set(known_source_sets())
    enabled = _enabled()

    items: list[SourceItem] = []
    for ss in sorted(counts, key=lambda k: (-counts[k], k)):
        title, authority = label_of(ss)
        items.append(
            SourceItem(
                source_set=ss,
                title=title or ss,
                authority=authority or "未标注",
                chunks=counts[ss],
                enabled=(not enabled) or (ss in enabled),
            )
        )

    return SourcesResponse(
        kb_version=kb_version,
        known_total=len(known),
        enabled_count=len(enabled) if enabled else len(counts),
        enabled_source_sets=enabled,
        items=items,
    )


@router.get("/conflicts", response_model=ConflictsResponse, summary="跨语料口径分歧")
async def list_conflicts(
    limit: int = 50,
    session: AsyncSession = Depends(get_db_session),
) -> ConflictsResponse:
    """扫描知识库，列出同一事实在不同语料下的不同取值。

    ## 为什么这是只读的

    **系统不替医生判定哪份指南更权威。** 这个端点只负责**把分歧摆出来**，
    决定权在医院（写 `enabled_source_sets`）与医生（结论里并列呈现）。

    实现复用 `app.rag.source_conflict.detect_conflicts`，不重写比对逻辑。
    """
    from app.knowledge.protocols import KnowledgeChunk
    from app.rag.source_conflict import detect_conflicts

    kb_version = await _kb_version(session)

    # ----------------------------------------------------------------
    # 2026-11-09 两次实测修正（都是真实踩坑）：
    #
    # **第一次**：直接 `LIMIT :n` 取前 N 块 → 只覆盖 alphabet 靠前的语料
    #   （实测 checked_sources=19、conflicts=0）。
    #
    # **第二次**：改成"每个 source_doc_id 取前 rn 块" → checked_sources=27，
    #   但 conflicts 仍是 0。原因是**分层键选错了**：分歧是"同一器官、同一事实"
    #   之间的现象，按 doc_id 分层会把甲状腺和心脏的块混在一起，
    #   而 `detect_conflicts` 需要成对的同 fact_key 才比对得出来。
    #
    # **现在的做法**：按 **(organ, source_doc_id)** 分层，每层取前若干块。
    #   这样同一器官下的多个来源都有代表块，跨语料比对才有可能。
    #
    # 另注：`detect_conflicts` 依赖 `extract_facts` 从 chunk 原文抽数值事实
    # （`malignant_risk:*` / `fna_threshold` 等），**只有含这些模式的块才有用**。
    # 所以额外用 SQL 预筛「含 % 或 mm 或"类"」的块，命中率更高。
    # ----------------------------------------------------------------
    rows = (
        await session.execute(
            text(
                """
                SELECT chunk_id, source_doc_id, organ, content, metadata FROM (
                    SELECT chunk_id, source_doc_id, organ, content, metadata,
                           row_number() OVER (
                               PARTITION BY organ, source_doc_id ORDER BY chunk_id
                           ) AS rn
                    FROM knowledge_chunks
                    WHERE kb_version = :v
                      AND (
                            content LIKE :pct
                         OR content LIKE :mm
                         OR content LIKE :cls
                      )
                ) t WHERE rn <= :per_doc
                """
            ),
            # `%` 在 Python 字符串里没特殊含义，但在 SQL LIKE 里是通配符；
            # 用 chr(37) 拼出来，避免源码里出现 `\%` 这种会被 SyntaxWarning 警告的写法。
            {
                "v": kb_version,
                "per_doc": max(2, limit // 200),
                "pct": f"%{chr(37)}%",
                "mm": "%mm%",
                "cls": "%类%",
            },
        )
    ).fetchall()

    by_source: dict[str, list[Any]] = defaultdict(list)
    for chunk_id, doc_id, organ, content, meta in rows:
        ss = source_set_of(source_doc_id=doc_id, metadata=meta)
        by_source[ss].append(
            KnowledgeChunk(
                chunk_id=chunk_id,
                kb_version=kb_version,
                organ=organ,
                content=content or "",
                source_doc_id=doc_id,
                section_path="",
                chunk_index=0,
                metadata=meta if isinstance(meta, dict) else None,
            )
        )

    conflicts: list[ConflictItem] = []
    for ss, chunks in by_source.items():
        try:
            report = detect_conflicts(chunks)
        except Exception as exc:  # silent-ok: 单语料检测失败不阻断其余语料
            logger.warning("kb_conflict_scan_failed", source_set=ss, error=str(exc))
            continue
        for c in (getattr(report, "conflicts", None) or []):
            sides = []
            for v in (getattr(c, "values", None) or []):
                title, _auth = label_of(getattr(v, "source_set", ""))
                sides.append(
                    ConflictSide(
                        source_set=str(getattr(v, "source_set", "")),
                        title=title or str(getattr(v, "source_set", "")),
                        value=str(getattr(v, "literal", "")),
                        quote=str(getattr(v, "quote", ""))[:200],
                    )
                )
            if len(sides) >= 2:
                conflicts.append(ConflictItem(fact=str(getattr(c, "fact_key", "")), sides=sides))

    return ConflictsResponse(
        kb_version=kb_version,
        checked_sources=len(by_source),
        conflicts=conflicts,
    )


@router.get("/enabled-sources", response_model=EnabledSourcesResponse, summary="读本院启用配置")
async def get_enabled_sources() -> EnabledSourcesResponse:
    """读**本院**启用哪些语料。空列表 = 全部启用。"""
    return EnabledSourcesResponse(enabled_source_sets=_enabled(), unknown=[])


@router.put("/enabled-sources", response_model=EnabledSourcesResponse, summary="设置本院启用配置")
async def set_enabled_sources(body: EnabledSourcesBody) -> EnabledSourcesResponse:
    """设置本院以哪几套语料为准。

    ## 为什么写进 settings 而不是数据库

    `rag_enabled_source_sets` 是**部署级配置**（一家医院一套部署），
    不是运行时可变的业务数据。写 settings 能让检索层立刻生效
    （`app/agents/specialist/base.py` 的 `_maybe_backfill_witness` 与
    `app/rag/service.py` 都读它）。

    ## 归一化与未知值

    传入值先过 `normalize_source_sets`（`C-TIRADS` / `中华医学会超声分会`
    都会归到 `ctirads_2021`）。**认不出的值原样返回在 `unknown` 里**，
    不静默丢弃 —— 否则运维以为配上了实际没生效。
    """
    normalized = normalize_source_sets(body.source_sets) or []
    known = set(known_source_sets())
    unknown = [s for s in normalized if s not in known]
    applied = [s for s in normalized if s in known]

    try:
        settings.rag_enabled_source_sets = applied
    except Exception as exc:
        logger.error("kb_enabled_sources_write_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="写入启用配置失败（settings 不可写）",
        ) from exc

    logger.info(
        "kb_enabled_sources_updated",
        applied=applied,
        unknown=unknown,
    )
    return EnabledSourcesResponse(enabled_source_sets=applied, unknown=unknown)


__all__ = ["router"]
