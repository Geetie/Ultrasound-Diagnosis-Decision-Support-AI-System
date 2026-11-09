"""冲突见证块补捞（2026-11-09）。

## 要解决的缺口

阶段3 的并列渲染做出来了，但**在生产默认配置下不触发**：

- `DEFAULT_RAG_TOP_K = 5`（`specialist/base.py:116`）
- `rag_rerank_output_top_k = 5`（`settings.py:531`）
- 而 C-TIRADS 那句「>15 mm 建议 FNA」在 BM25 里排**第 32 位**

→ 两套语料的对照条目**永远进不了 top-5** → 并列块空 → 能力"做出来了但没生效"。

## 为什么不直接提高 top_k

提到 60 能解决，但代价是 prompt 长度 ×12，且会灌入大量无关块。
**这不是"多看几块"的问题，是"该看的块根本没被检索到"的问题。**

## 本模块的做法：定向补捞

1. **先**按正常流程取 top-k（不变）
2. **然后**检查：已召回块里，`source_set` 的种类是否覆盖了"同一事实的多个来源"
3. 若只有 1 个来源，且该来源的事实**有对照来源存在**（由 `source_conflict` 的
   事实模式决定要查什么），就**用该事实的检索式定向补捞**另一来源的块
4. 补捞结果**不参与排名**，只作为「并列证据」附加到 prompt

**关键设计**：补捞的是**事实**，不是**文档**。
即"我需要 4A 类 FNA 阈值的另一个来源"，而不是"我需要诊疗规范这份文档"。

## 铁律

- 补捞失败 → 静默返回空（不能因为补捞不到就阻断主流程）
- 补到的块**不进排名**、不影响 top-k 结果、不改变已有排序
- 数量设上限（默认 3），防止 prompt 爆炸
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("app.rag.witness_backfill")

#: 补捞上限：一次最多补几块。超过这个数 prompt 收益递减、token 成本线性上升。
DEFAULT_MAX_BACKFILL = 3


@dataclass
class BackfillResult:
    """补捞结果。``chunks`` 只用于并列渲染，**不参与排名**。"""

    chunks: list[Any] = field(default_factory=list)
    reason: str = ""
    queries_used: list[str] = field(default_factory=list)

    @property
    def applied(self) -> bool:
        return bool(self.chunks)


def _source_sets_of(chunks: list[Any]) -> set[str]:
    """从已检索块里收集 source_set。"""
    out: set[str] = set()
    for c in chunks:
        chunk = getattr(c, "chunk", c)
        meta = getattr(chunk, "metadata", None) or {}
        ss = meta.get("source_set") if isinstance(meta, dict) else None
        if ss:
            out.add(str(ss))
    return out


def needs_backfill(
    chunks: list[Any],
    *,
    enabled_source_sets: list[str] | None = None,
) -> tuple[bool, str]:
    """判断是否值得补捞。

    Returns:
        ``(是否需要, 原因码)``。原因码用于日志与 trace，便于诊断"为什么没并列"。
    """
    if not chunks:
        return False, "no_chunks"

    present = _source_sets_of(chunks)
    if not present:
        # 没有 source_set 元数据的语料，无法判断多来源 → 不补捞（也无从补）
        return False, "no_source_set_metadata"

    if len(present) >= 2:
        # 已覆盖多个来源 → 交给 source_conflict 去判有没有分歧，不需要补
        return False, "multi_source_already_present"

    if enabled_source_sets is not None and len(enabled_source_sets) < 2:
        # 医院只启用了一套 → 本来就不该并列
        return False, "single_source_enabled"

    return True, "single_source_recalled"


def build_witness_queries(query: str, present_source_sets: set[str]) -> list[str]:
    """为"缺失的那一侧"生成补捞检索式。

    **只生成检索式，不生成事实内容** —— 事实仍由 `source_conflict` 从块原文抽。
    这样保证"不硬编码临床数字"这条铁律不被破坏。

    策略：把用户查询与"分级/阈值"类关键词拼接，定位另一套语料的对应条目。
    """
    # 常见会冲突的事实维度（来自 source_conflict._FACT_PATTERNS 的语义，不是数字）
    _DIMENSIONS = (
        "恶性风险 分级",
        "FNA 穿刺 阈值 大小",
        "随访 间隔",
    )
    out: list[str] = []
    base = (query or "").strip()
    if not base:
        return out
    for dim in _DIMENSIONS:
        out.append(f"{base} {dim}")
    return out


async def backfill_witness(
    *,
    query: str,
    chunks: list[Any],
    retrieve_fn: Any,
    enabled_source_sets: list[str] | None = None,
    max_backfill: int = DEFAULT_MAX_BACKFILL,
) -> BackfillResult:
    """补捞「另一套语料的对照块」。

    Args:
        query: 用户原始查询。
        chunks: 已检索到的块。
        retrieve_fn: 一个 ``async (query: str, top_k: int) -> list[chunk]`` 可调用。
            由调用方注入，避免本模块依赖 Retriever 的具体类型。
        enabled_source_sets: 医院启用的语料集合；None 表示全开。
        max_backfill: 最多补几块。

    Returns:
        ``BackfillResult`` —— 失败时 ``chunks`` 为空且 ``reason`` 说明原因，
        **绝不抛异常**（补捞是增强，不是主流程）。
    """
    try:
        need, reason = needs_backfill(
            chunks, enabled_source_sets=enabled_source_sets
        )
        if not need:
            return BackfillResult(reason=reason)

        present = _source_sets_of(chunks)
        queries = build_witness_queries(query, present)
        if not queries:
            return BackfillResult(reason="no_queries_generated")

        # 已有块的 id，用于去重
        seen_ids: set[str] = set()
        for c in chunks:
            cid = getattr(c, "chunk_id", None) or getattr(
                getattr(c, "chunk", c), "chunk_id", None
            )
            if cid:
                seen_ids.add(str(cid))

        picked: list[Any] = []
        used: list[str] = []
        for q in queries:
            if len(picked) >= max_backfill:
                break
            try:
                cand = await retrieve_fn(q, max_backfill * 2)
            except Exception as exc:  # silent-ok: 补捞失败不影响主流程
                logger.warning(
                    "witness_backfill_retrieve_failed",
                    query=q[:40],
                    error=str(exc),
                )
                continue
            used.append(q)
            for c in cand or []:
                cid = getattr(c, "chunk_id", None) or getattr(
                    getattr(c, "chunk", c), "chunk_id", None
                )
                if not cid or str(cid) in seen_ids:
                    continue
                # 只要**其他 source_set** 的块
                chunk = getattr(c, "chunk", c)
                meta = getattr(chunk, "metadata", None) or {}
                ss = meta.get("source_set") if isinstance(meta, dict) else None
                if ss and str(ss) not in present:
                    seen_ids.add(str(cid))
                    picked.append(c)
                    if len(picked) >= max_backfill:
                        break

        if not picked:
            return BackfillResult(
                reason="no_other_source_found", queries_used=used
            )
        logger.info(
            "witness_backfill_applied",
            n=len(picked),
            present=sorted(present),
            queries=len(used),
        )
        return BackfillResult(
            chunks=picked, reason="backfilled", queries_used=used
        )
    except Exception as exc:  # silent-ok: 补捞是增强，任何异常都不能影响主流程
        logger.warning("witness_backfill_failed", error=str(exc))
        return BackfillResult(reason=f"error:{type(exc).__name__}")


__all__ = [
    "BackfillResult",
    "DEFAULT_MAX_BACKFILL",
    "backfill_witness",
    "build_witness_queries",
    "needs_backfill",
]
