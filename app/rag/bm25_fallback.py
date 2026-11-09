"""T-GA-RAG-03: BM25 fallback + shared BM25 index.

This module provides two cooperating classes:

- BM25Index: a process-wide, lazy-loaded BM25Okapi index over the
  knowledge_chunks table. It is shared between the hybrid retriever
  (sparse leg) and the fallback path so we never build the index twice.

- BM25Fallback: a thin policy object that decides when to trigger
  the BM25-only fallback and runs the search.

Tokenization: we use a simple deterministic strategy (CJK continuous
character sequences + alphanumeric splits). This avoids adding jieba
as a hard dependency. Quality is good enough for short medical queries
and is fully testable. A future iteration can swap in jieba without
changing the public API.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import structlog
from rank_bm25 import BM25Okapi
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.knowledge.protocols import KnowledgeChunk
from app.knowledge.source_set import included_by_enabled, source_set_of
from app.models.database import KnowledgeChunkModel
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalResult,
    ScoredChunk,
)

logger = structlog.get_logger("app.rag.bm25_fallback")


# Tokenization patterns.
_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_NON_WORD_RE = re.compile(r"[^A-Za-z0-9]+")


def tokenize(text: str) -> list[str]:
    """Simple CJK+alphanumeric tokenizer with 2-char window for CJK.

    Examples:
        "低回声结节"        -> ["低回声结节", "低回声", "回声结", "结节"]
        "TI-RADS 4A nodule" -> ["ti", "rads", "4a", "nodule"]

    CJK handling: keep the full phrase (helps exact-substring match) AND
    emit a 2-character sliding window (improves recall for partial matches).
    """
    if not text:
        return []
    text = text.lower()
    tokens: list[str] = []
    for cjk_match in _CJK_RE.finditer(text):
        cjk = cjk_match.group(0)
        tokens.append(cjk)
        # 2-char sliding window for partial-match recall
        for i in range(len(cjk) - 1):
            tokens.append(cjk[i:i + 2])
    # Replace non-alphanumeric with spaces, then split.
    ascii_cleaned = _NON_WORD_RE.sub(" ", text)
    for tok in ascii_cleaned.split():
        if tok:
            tokens.append(tok)
    return tokens


@dataclass
class _BuiltIndex:
    """Internal holder for a built BM25 index + metadata."""
    bm25: BM25Okapi
    chunk_ids: list[str]            # index position -> chunk_id
    chunks: list[KnowledgeChunk]     # index position -> chunk
    organs: list[str]                # index position -> organ
    kb_version: str | None
    built_at: float
    #: 索引位置 → source_set（2026-11-09 加）。
    #:
    #: 为什么在**建索引时**算好而不是查询时现算：一条请求要过几千个文档位置，
    #: 每次 ``source_set_of()`` 都要走一遍规则表 + 异常兜底；建索引时算一次
    #: （``_build_locked`` 里）就把这块成本挪到冷启动，热路径只剩字符串比较。
    #: 代价是**新增规则要重建索引才生效** —— 建索引本来就在启动期/切库时发生，
    #: 可接受；``scripts/kb_backfill_source_set.py`` 会打印重建提示。
    source_sets: list[str] = field(default_factory=list)


@dataclass
class BM25Hit:
    """A single BM25 search hit."""
    chunk_id: str
    score: float
    chunk: KnowledgeChunk


class BM25Index:
    """Lazy BM25 index over knowledge_chunks.

    Threading: not thread-safe; uses asyncio.Lock for concurrent awaits.

    The index is built on first use (or on kb_version change) and cached
    in memory. A `max_chunks` cap is enforced; rows beyond the cap are
    truncated with a WARN log so operators can spot knowledge-base bloat.
    """

    def __init__(
        self,
        session_factory: Callable[[], AsyncSession] | Callable[[], Awaitable[AsyncSession]],
        max_chunks: int | None = None,
    ):
        self._session_factory = session_factory
        self._max_chunks = max_chunks or settings.rag_bm25_max_chunks
        self._built: _BuiltIndex | None = None
        self._lock = asyncio.Lock()

    async def ensure_built(self, kb_version: str | None = None) -> None:
        """Build the index if not built or if kb_version changed.

        Idempotent and concurrency-safe via an asyncio.Lock.
        """
        async with self._lock:
            if self._built is not None:
                if kb_version is None or self._built.kb_version == kb_version:
                    return
                logger.info(
                    "bm25_index_rebuild_due_to_version_change",
                    old=self._built.kb_version,
                    new=kb_version,
                )
            await self._build_locked(kb_version)

    async def _build_locked(self, kb_version: str | None) -> None:
        session = self._session_factory()
        try:
            stmt = select(
                KnowledgeChunkModel.chunk_id,
                KnowledgeChunkModel.kb_version,
                KnowledgeChunkModel.organ,
                KnowledgeChunkModel.content,
                KnowledgeChunkModel.meta,
                KnowledgeChunkModel.embedding,
                KnowledgeChunkModel.source_doc_id,
                KnowledgeChunkModel.section_path,
                KnowledgeChunkModel.token_count,
            ).order_by(KnowledgeChunkModel.chunk_id)
            # --------------------------------------------------------------
            # 2026-11-09 修 P0：两条检索腿在搜**不同的语料**
            #
            # 缺陷（阶段2 Agent 实测）：
            #   dense 腿（Qdrant）= 5042 点   ← kb_v1.4.0 + kb_v1.5.0
            #   sparse 腿（本索引）= 9561 行  ← PG 全表（9 个 kb_version）
            #   **只在 BM25 里的 chunk = 4525 条**，dense 永远搜不到
            #
            # 后果是机制性的：
            #   · BM25 在 5818 篇旧版本副本上算 IDF → **IDF 被系统性压低**
            #   · top-k 混入旧版本副本，经 RRF 后被逻辑去重折叠 → 召回位浪费
            #   · 两条腿的 rank 不可比 → **RRF 公式的前提（rank 语义一致）不成立**
            #
            # 修法：未显式指定 kb_version 时，**取 PG 里最新版本**，与 dense 对齐。
            # 用 max(kb_version) 而非配置文件，是因为多版本共存时"最新"由数据本身决定。
            # --------------------------------------------------------------
            # 只在显式指定时过滤；未指定时**先查全量再在内存里裁剪到最新版本**。
            # 这样不增加额外 DB 查询（测试里的轻量 mock 只需实现 `all()`），
            # 且"最新版本"由数据本身决定（多版本共存时不能靠配置文件猜）。
            if kb_version is not None:
                stmt = stmt.where(KnowledgeChunkModel.kb_version == kb_version)
            stmt = stmt.limit(self._max_chunks)
            result = await session.execute(stmt)
            rows = result.all()
        finally:
            # AsyncSession close handled by caller via factory cleanup;
            # we only close if the factory returned a session we own.
            close = getattr(session, "close", None)
            if callable(close):
                res = close()
                if hasattr(res, "__await__"):
                    await res

        if len(rows) >= self._max_chunks:
            logger.warning(
                "bm25_index_truncated_to_max_chunks",
                max_chunks=self._max_chunks,
            )

        # 2026-11-09 修 P0：未显式指定 kb_version 时，**内存里裁剪到最新版本**，
        # 与 dense 腿对齐（详见此前 `_build_locked` 里的缺陷说明）。
        if kb_version is None and rows:
            _versions = [getattr(r, "kb_version", None) for r in rows]
            _versions = [v for v in _versions if v]
            if _versions:
                _latest = max(_versions)
                _before = len(rows)
                rows = [r for r in rows if getattr(r, "kb_version", None) == _latest]
                if len(rows) < _before:
                    logger.info(
                        "bm25_index_pinned_to_latest_version",
                        kb_version=_latest,
                        before=_before,
                        after=len(rows),
                    )

        corpus: list[list[str]] = []
        chunk_ids: list[str] = []
        chunks: list[KnowledgeChunk] = []
        organs: list[str] = []
        source_sets: list[str] = []
        for row in rows:
            tokenized = tokenize(row.content or "")
            if not tokenized:
                # Skip completely empty-tokenized docs to keep BM25Okapi happy.
                continue
            corpus.append(tokenized)
            chunk_ids.append(row.chunk_id)
            organs.append(row.organ)
            meta = dict(row.meta) if row.meta else {}
            embedding = list(row.embedding) if row.embedding is not None else None
            chunk = KnowledgeChunk(
                chunk_id=row.chunk_id,
                kb_version=row.kb_version,
                organ=row.organ,
                content=row.content,
                metadata=meta,
                embedding=embedding,
                source_doc_id=row.source_doc_id,
                section_path=row.section_path or "",
                token_count=row.token_count or 0,
            )
            chunks.append(chunk)
            source_sets.append(
                source_set_of(source_doc_id=row.source_doc_id, metadata=meta)
            )

        bm25 = BM25Okapi(corpus) if corpus else None
        self._built = _BuiltIndex(
            bm25=bm25,
            chunk_ids=chunk_ids,
            chunks=chunks,
            organs=organs,
            kb_version=kb_version,
            built_at=time.monotonic(),
            source_sets=source_sets,
        )
        # 语料集分布入日志：运维答"这台机器上到底装了哪几套语料"靠这条。
        distribution: dict[str, int] = {}
        for ss in source_sets:
            distribution[ss] = distribution.get(ss, 0) + 1
        logger.info(
            "bm25_index_built",
            num_chunks=len(chunks),
            kb_version=kb_version,
            source_set_distribution=distribution,
        )

    def search(
        self,
        query: str,
        top_k: int = 20,
        organ_filter: str | None = None,
        source_sets: list[str] | None = None,
    ) -> list[BM25Hit]:
        """Run a BM25 search against the (already built) index.

        Args:
            query: 查询串。
            top_k: 返回条数上限。
            organ_filter: 器官过滤（精确相等，与改动前一致）。
            source_sets: 语料集白名单（2026-11-09 加）。
                ``None`` / ``[]`` = 不过滤；非空 = 只保留命中的集。
                **来源认不出的 chunk（``unknown``）永远放行** —— 口径见
                ``app.knowledge.source_set.included_by_enabled``。

        Returns an empty list if the index has not been built yet (caller
        should call ensure_built() first).

        This is a CPU-bound sync method, intentionally: rank_bm25 is fast
        for <10k docs and avoids event-loop blocking for typical queries.
        """
        if self._built is None or self._built.bm25 is None:
            logger.warning("bm25_search_called_before_ensure_built")
            return []
        tokenized_query = tokenize(query)
        if not tokenized_query:
            return []
        scores = self._built.bm25.get_scores(tokenized_query)

        # Collect (score, position) pairs and filter by organ / source_set.
        #
        # 白名单为空时**完全不碰** source_sets 数组（一个分支都不多走），
        # 保证"不配置"和"改动前"在热路径上逐条等价。
        enabled = [s for s in (source_sets or []) if s]
        scored: list[tuple[float, int]] = []
        for idx, score in enumerate(scores):
            if organ_filter is not None and self._built.organs[idx] != organ_filter:
                continue
            if enabled:
                idx_sets = self._built.source_sets
                current = idx_sets[idx] if idx < len(idx_sets) else ""
                if not included_by_enabled(current, enabled):
                    continue
            # BM25Okapi can produce negative scores; clamp to 0 for cleanliness.
            scored.append((max(0.0, float(score)), idx))

        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[: max(0, top_k)]
        return [
            BM25Hit(
                chunk_id=self._built.chunk_ids[idx],
                score=score,
                chunk=self._built.chunks[idx],
            )
            for score, idx in top
        ]

    async def health_check(self) -> bool:
        return self._built is not None and self._built.bm25 is not None

    @property
    def is_built(self) -> bool:
        return self._built is not None and self._built.bm25 is not None

    @property
    def num_chunks(self) -> int:
        return len(self._built.chunk_ids) if self._built else 0

    async def close(self) -> None:
        self._built = None


class BM25Fallback:
    """T-GA-RAG-03 BM25 fallback policy + search wrapper.

    Triggers when:
    - The retriever returned 0 chunks.
    - The top1 score is below `min_top1_score`.
    - The retriever returned fewer than `min_results` chunks.
    """

    def __init__(
        self,
        bm25_index: BM25Index,
        min_top1_score: float | None = None,
        min_results: int | None = None,
    ):
        self._index = bm25_index
        self._min_top1_score = (
            min_top1_score if min_top1_score is not None
            else settings.rag_bm25_min_top1_score
        )
        self._min_results = (
            min_results if min_results is not None
            else settings.rag_bm25_min_results
        )

    def should_trigger(self, retriever_result: RetrievalResult) -> bool:
        if not retriever_result.has_results:
            return True
        if retriever_result.top_score < self._min_top1_score:
            return True
        if len(retriever_result.chunks) < self._min_results:
            return True
        return False

    def trigger_reason(self, retriever_result: RetrievalResult) -> str:
        if not retriever_result.has_results:
            return "no_results"
        if retriever_result.top_score < self._min_top1_score:
            return "low_top1_score"
        if len(retriever_result.chunks) < self._min_results:
            return "too_few_results"
        return "ok"

    async def fallback_search(
        self,
        query: str,
        top_k: int,
        organ: str | None,
        source_sets: list[str] | None = None,
    ) -> list[ScoredChunk]:
        """Run BM25 search and wrap hits as ScoredChunks.

        Args:
            source_sets: 语料集白名单（2026-11-09 加）。**必须与主检索腿用同一份**
                —— 否则"主腿只查 C-TIRADS、兜底腿却捞回诊疗规范"，
                医院配的权威语料限制会在最需要它的兜底路径上失效。

        Returns an empty list if the index has not been built yet.
        """
        if not self._index.is_built:
            return []
        hits = self._index.search(
            query, top_k=top_k, organ_filter=organ, source_sets=source_sets
        )
        return [
            ScoredChunk(
                chunk=h.chunk,
                score=h.score,
                rerank_score=None,
                retrieval_source=RetrievalMethod.BM25_FALLBACK,
            )
            for h in hits
        ]


__all__ = [
    "BM25Fallback",
    "BM25Hit",
    "BM25Index",
    "tokenize",
]
