"""知识来源权威性评分（A.88 T4-④「前两项无实现」的第一项）。

## 为什么需要它

《架构真相》§3.3 把「知识来源权威性评分」登记为**无实现**：检索把指南条文、
教科书段落、既往病例报告当同一类证据排序，医生看到的引用顺序里没有任何
"这条是 ACR 指南、那条是单个病例"的区别。这在答辩场景是硬伤，在临床上也
确实会让人误信一条孤例。

## 口径（可解释、可审计，不编造数值）

权重按**来源类型**给，不是按"这篇文献有多权威"打分（那需要外部评价体系，
本系统没有）：

| 来源类型 | 权重 | 理由 |
|---|---|---|
| ``guideline`` 指南/共识 | 1.00 | 规范化诊疗的直接依据 |
| ``textbook`` 教科书 | 0.95 | 权威但更新慢 |
| ``literature`` 期刊文献 | 0.90 | 证据等级分化大，取中性偏高 |
| ``protocol`` 院内规程 | 0.85 | 本院可执行，但适用范围窄 |
| ``case_reports`` 病例报告 | 0.70 | 孤例，不能当通则 |
| ``unknown`` 未知 | 1.00 | **不猜**：未知来源不做惩罚 |

``unknown`` 给 1.0 而不是最低分，是刻意的：来源元数据缺失是**数据问题**，
不是"这条知识不可信"。给它低分等于用缺失数据当证据去压权重 —— 与
``decay.py`` 里"没有引用时间不等于长期未引用"同一条原则。

## 排序如何"参与"

三种模式（``AuthorityRankingMode``）：

* ``off``：只用权重留痕，不改顺序；
* ``tie_break``（**默认**）：只对**分数完全相同**的相邻条目按权重稳定排序。
  等权 RRF 分几乎不会撞（浮点），所以默认档对现有输出近乎无影响 —— 这是
  刻意选的：它能证明"权重真的接进排序了"，又不改变医生已经在看的结论顺序；
* ``weighted``：``score * weight`` 后整体重排。**会改变临床输出**，必须显式开启，
  且开启前应跑检索评测（本仓库的 hit@10 基线见 16 号附录 A.65）。

任何模式都留痕 ``authority_ranking_applied``（模式 / 权重命中分布 / 次序变动条数）。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum

import structlog

from app.knowledge.protocols import KnowledgeChunk

logger = structlog.get_logger("app.knowledge.authority")


class AuthorityRankingMode(str, Enum):
    """权威性参与排序的方式。"""

    OFF = "off"
    TIE_BREAK = "tie_break"
    WEIGHTED = "weighted"


class SourceKind(str, Enum):
    """知识来源类型（与 ``app/knowledge/desensitization.py`` 的 ``source_kind`` 对齐）。"""

    GUIDELINE = "guideline"
    TEXTBOOK = "textbook"
    LITERATURE = "literature"
    PROTOCOL = "protocol"
    CASE_REPORTS = "case_reports"
    UNKNOWN = "unknown"


#: 来源类型 → 权威性权重。改这里必须同步改模块文档的表格（守卫钉住两处一致）。
SOURCE_AUTHORITY: dict[str, float] = {
    SourceKind.GUIDELINE.value: 1.00,
    SourceKind.TEXTBOOK.value: 0.95,
    SourceKind.LITERATURE.value: 0.90,
    SourceKind.PROTOCOL.value: 0.85,
    SourceKind.CASE_REPORTS.value: 0.70,
    SourceKind.UNKNOWN.value: 1.00,
}

#: 没有元数据时使用的类型（**不猜**，见模块文档）。
DEFAULT_SOURCE_KIND = SourceKind.UNKNOWN.value

#: 从 ``source_doc_id`` / ``section_path`` 推断来源类型的显式前缀表。
#:
#: 只用**显式前缀**，不做"里面出现过 guideline 字样就算指南"这种模糊匹配 ——
#: 那会把 "case_reports/guideline_misuse_2024.json" 误判成指南。
_SOURCE_PREFIXES: tuple[tuple[str, str], ...] = (
    ("guideline", SourceKind.GUIDELINE.value),
    ("textbook", SourceKind.TEXTBOOK.value),
    ("literature", SourceKind.LITERATURE.value),
    ("protocol", SourceKind.PROTOCOL.value),
    ("case_reports", SourceKind.CASE_REPORTS.value),
    ("case_report", SourceKind.CASE_REPORTS.value),
    ("cases", SourceKind.CASE_REPORTS.value),
)


def source_kind_of(chunk: KnowledgeChunk) -> str:
    """判定一条 chunk 的来源类型。纯函数（守卫钉住）。

    顺序：``metadata["source_kind"]`` → ``source_doc_id`` 前缀 →
    ``metadata["doc_type"]`` → ``unknown``。

    **不抛异常**：metadata 可能是任意 JSON（脏数据），任何异常都回退到
    ``unknown`` 并留痕（而不是让一次检索因为脏 metadata 崩掉）。
    """
    try:
        meta = dict(chunk.metadata or {})
        explicit = str(meta.get("source_kind") or "").strip().lower()
        if explicit in SOURCE_AUTHORITY:
            return explicit
        doc_id = str(chunk.source_doc_id or "").strip().lower().lstrip("./")
        for prefix, kind in _SOURCE_PREFIXES:
            if doc_id.startswith(prefix) or doc_id.startswith(f"{prefix}/"):
                return kind
        doc_type = str(meta.get("doc_type") or "").strip().lower()
        if doc_type in SOURCE_AUTHORITY:
            return doc_type
    except Exception as exc:
        logger.warning(
            "authority_source_kind_failed",
            chunk_id=getattr(chunk, "chunk_id", ""),
            error=str(exc),
            error_type=type(exc).__name__,
        )
    return DEFAULT_SOURCE_KIND


def authority_weight(chunk: KnowledgeChunk) -> float:
    """单条 chunk 的权威性权重（见模块文档的表格）。"""
    return SOURCE_AUTHORITY.get(source_kind_of(chunk), 1.0)


def authority_breakdown(chunks: Sequence[KnowledgeChunk]) -> dict[str, int]:
    """按来源类型统计条数（写进留痕，便于回答"这批证据里几成是指南"）。"""
    out: dict[str, int] = {}
    for chunk in chunks:
        kind = source_kind_of(chunk)
        out[kind] = out.get(kind, 0) + 1
    return out


@dataclass(frozen=True)
class RankingOutcome:
    """一次权威性排序的结果（含"改了几条"以便留痕与反证）。"""

    order: list[int]
    mode: str
    changed: int
    breakdown: dict[str, int]


def rank_indices(
    chunks: Sequence[KnowledgeChunk],
    scores: Sequence[float],
    *,
    mode: str | AuthorityRankingMode = AuthorityRankingMode.TIE_BREAK,
) -> RankingOutcome:
    """按模式给出排序后的下标序列。纯函数（守卫钉住，不依赖 pydantic 模型）。

    Args:
        chunks: 候选 chunk（与 ``scores`` 一一对应）。
        scores: 每条的相关性分数（RRF / rerank 分，原样使用）。
        mode: ``off`` / ``tie_break`` / ``weighted``。

    Returns:
        :class:`RankingOutcome`；``order`` 是下标的排列，
        ``changed`` 是"与原顺序不同的位置数"。

    语义细节：
    * 输入按 ``scores`` 降序**稳定**排序作为基线（与原行为一致）；
    * ``tie_break``：只在分数**完全相等**的相邻段内按权重降序重排；
    * ``weighted``：``score * weight`` 降序，权重相同即退化为原顺序。
    """
    resolved = str(getattr(mode, "value", mode) or "").strip().lower()
    if resolved not in {m.value for m in AuthorityRankingMode}:
        resolved = AuthorityRankingMode.TIE_BREAK.value

    base = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    breakdown = authority_breakdown(chunks)
    if resolved == AuthorityRankingMode.OFF.value or len(base) < 2:
        return RankingOutcome(base, resolved, 0, breakdown)

    weights = [authority_weight(chunks[i]) for i in base]

    if resolved == AuthorityRankingMode.WEIGHTED.value:
        # score * weight：分数本身已降序，乘权重后再稳定排序
        reordered = sorted(
            base,
            key=lambda i: scores[i] * authority_weight(chunks[i]),
            reverse=True,
        )
    else:
        # tie_break：只在"分数完全相同"的连续段内按权重重排，其余位置不动
        reordered = list(base)
        start = 0
        while start < len(reordered):
            end = start + 1
            while end < len(reordered) and scores[reordered[end]] == scores[reordered[start]]:
                end += 1
            if end - start > 1:
                segment = reordered[start:end]
                segment.sort(key=lambda i: authority_weight(chunks[i]), reverse=True)
                reordered[start:end] = segment
            start = end

    changed = sum(1 for pos, idx in enumerate(reordered) if pos < len(base) and base[pos] != idx)
    # 权重表用于留痕（避免"算了但没记录用了什么权重"）
    logger.debug(
        "authority_weights_used",
        mode=resolved,
        weights_sample=weights[:5],
        breakdown=breakdown,
    )
    return RankingOutcome(reordered, resolved, changed, breakdown)


__all__ = [
    "DEFAULT_SOURCE_KIND",
    "SOURCE_AUTHORITY",
    "AuthorityRankingMode",
    "RankingOutcome",
    "SourceKind",
    "authority_breakdown",
    "authority_weight",
    "rank_indices",
    "source_kind_of",
]
