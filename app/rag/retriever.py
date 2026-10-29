"""T-GA-RAG-02: Multimodal parallel retrieval (Dense + Sparse + Image) with RRF.

This module implements the retrieval leg of the RAG pipeline. Three retrieval
legs can be enabled independently:

- Dense: bge-m3-medical text embedding -> VectorIndexClient (Qdrant/PGVector)
- Sparse: BM25 over knowledge_chunks.content (shared BM25Index instance)
- Image: ultrasound-clip image embedding -> VectorIndexClient (image collection)

The legs are launched concurrently with asyncio.gather. Their results are
merged with Reciprocal Rank Fusion (RRF), then truncated to request.top_k.

Failure handling:
- Any single leg failure is logged and degrades to that leg being empty.
- The dense leg retries once on transient EmbeddingError (connection).
- A missing image collection is treated as a soft skip (not an error).
"""
from __future__ import annotations

import asyncio
import re
from collections import deque
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.embedding.protocols import (
    EmbeddingError,
    EmbeddingService,
    ImageEmbeddingRequest,
    SearchResult,
    TextEmbeddingRequest,
    VectorIndexClient,
    VectorIndexError,
)
from app.knowledge.protocols import KnowledgeChunk
from app.rag.bm25_fallback import BM25Index
from app.rag.image_leg import resolve_image_leg_slot, warn_once
from app.rag.protocols import (
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)

logger = structlog.get_logger("app.rag.retriever")

# 检索空结果滑动窗口（wiring-audit P1-3：retrieval_empty_rate gauge 生产者）
_RETRIEVAL_OUTCOME_WINDOW: deque[int] = deque(maxlen=500)


def _record_retrieval_outcome(*, is_empty: bool) -> None:
    """记录一次融合检索的空结果与否，并把窗口空率推到 Prometheus gauge。

    失败静默（观测不阻塞检索主链路）。
    """
    try:
        _RETRIEVAL_OUTCOME_WINDOW.append(1 if is_empty else 0)
        window = _RETRIEVAL_OUTCOME_WINDOW
        rate = sum(window) / len(window)
        from app.observability.metrics_collector import get_metrics_collector

        get_metrics_collector().set_retrieval_empty_rate(rate)
    except Exception as exc:
        logger.debug("retrieval_empty_rate_update_failed", error=str(exc))

# Used to detect "collection does not exist" style errors from Qdrant so we
# can silently skip the image leg when the image collection is not provisioned.
_NOT_FOUND_PATTERNS = (
    re.compile(r"not\s*found", re.IGNORECASE),
    re.compile(r"doesn't\s*exist", re.IGNORECASE),
    re.compile(r"does\s*not\s*exist", re.IGNORECASE),
    re.compile(r"collection.*missing", re.IGNORECASE),
)


def _is_collection_not_found(exc: Exception) -> bool:
    msg = str(exc) or ""
    return any(p.search(msg) for p in _NOT_FOUND_PATTERNS)


def image_search_filter(organ: str | None, session_id: str | None) -> dict | None:
    """影像腿的向量检索过滤条件（**病人隔离红线**）。纯函数（守卫钉住）。

    规则：
    * **有 session_id → 必须按 session 过滤**（影像属于某次检查会话；
      跨会话召回等于看到别的病人的影像）；
    * 有 organ 再叠加；
    * 两者都没有 → 返回 None；调用方（`_safe_image`）在无 session 时**不应发请求**，
      本函数不替它"放开全库"。

    ## A.90：`organ` 叠加是**有条件的**（否则这条腿永远 0 命中）

    真机实测（2026-10-25）：`ultrasound_chunks` 的 580 个点涉及 316 个 session，
    其中**只有 24 个**能拿到 `organ`（payload 里 `if row.get("organ")` 才写，
    而 292 个 session 压根没有 `diagnosis_results` 记录）。
    于是按 `session_id + organ` 双条件查询 → **命中恒为 0**，
    影像腿"接线通了但永远返回空"，A/B 十例全部 `new=0`。

    这不是"该不该按 organ 过滤"的问题（按 organ 过滤本身是对的：跨器官影像不该
    参与本次检索），而是**过滤条件不能比数据里实际存在的字段更严**。
    故保留 `organ` 在返回值里（调用方据此判断是否需要降级重试），
    并新增 :func:`image_search_filters` 给出"严格 → 宽松"的候选序列。
    """
    flt: dict = {}
    if session_id:
        flt["session_id"] = session_id
    if organ:
        flt["organ"] = organ
    return flt or None


def image_search_filters(organ: str | None, session_id: str | None) -> list[dict]:
    """影像腿的过滤条件**候选序列**（严格 → 宽松），按顺序尝试直到有命中。

    A.90：为什么需要"降级重试"而不是二选一 ——
    * 严格档（`session + organ`）：语义最准，但历史 payload 缺 `organ` 时恒 0 命中；
    * 宽松档（仅 `session`）：**病人隔离仍然成立**（session 属于单一患者），
      只是少了"同会话跨器官"这一层收窄。影像点本来就按 session 归属，
      同会话内不同器官的影像同时召回不会泄露他人数据。

    **病人隔离红线不变**：返回的每一档都必带 `session_id`；无 session 返回空列表
    （调用方据此不发请求，绝不退化成"放开全库"）。
    """
    if not session_id:
        return []
    strict = {"session_id": session_id}
    if organ:
        strict["organ"] = organ
        return [strict, {"session_id": session_id}]
    return [strict]


class _Hit:
    """Internal lightweight hit type used during RRF fusion."""

    __slots__ = ("chunk", "chunk_id", "score", "source")

    def __init__(
        self,
        chunk_id: str,
        chunk: KnowledgeChunk,
        score: float,
        source: RetrievalMethod,
    ):
        self.chunk_id = chunk_id
        self.chunk = chunk
        self.score = score
        self.source = source


#: 影像腿未执行的**具体**原因码（AGENTS.md 硬规则 8：每个分支留自己的原因）。
#: 与 ``app/rag/image_leg.py`` 的能力码刻意区分开：
#: ``capability_*`` = 这个部署根本没有影像检索能力（配置缺陷）；
#: ``no_image_data`` = 调用方这次没送字节（请求级问题）。
IMAGE_SKIP_NO_DATA = "no_image_data"
IMAGE_SKIP_CLIP_UNAVAILABLE = "clip_service_unavailable"
IMAGE_SKIP_MULTIMODAL_OFF = "multimodal_disabled"
IMAGE_SKIP_NO_SESSION_FOR_LEG = "no_session_id"


class ImageLegResolution:
    """``retrieve()`` 解析出的影像腿输入（纯数据，便于单测）。"""

    __slots__ = ("image_data", "image_format", "session_id", "skip_reason")

    def __init__(
        self,
        image_data: bytes | None,
        image_format: str,
        session_id: str | None,
        skip_reason: str | None,
    ):
        self.image_data = image_data
        self.image_format = image_format
        self.session_id = session_id
        self.skip_reason = skip_reason


class _DropTally:
    """**单次** ``retrieve()`` 内的"PG 缺行"丢弃计数（A.88 T4-⑥）。

    刻意不挂在 ``Retriever`` 实例上：实例是长跑共享的，累加会把"上一次请求
    的丢弃"算进这一次（结果里那个 ``dropped_missing_row`` 就会说谎）。
    """

    __slots__ = ("dense", "image")

    def __init__(self) -> None:
        self.dense = 0
        self.image = 0

    @property
    def total(self) -> int:
        return self.dense + self.image

    def add(self, *, source: RetrievalMethod, count: int) -> None:
        if count <= 0:
            return
        # 影像腿在 ``_safe_image`` 里被标成 DENSE（见该方法的注释）；
        # 这里按"调用来源"分账，而不是按 retrieval_source 分账。
        if source == RetrievalMethod.DENSE:
            self.dense += count
        else:
            self.image += count

    def add_image(self, count: int) -> None:
        if count > 0:
            self.image += count


class Retriever:
    """T-GA-RAG-02 multi-modal parallel retriever.

    Args:
        vector_client: VectorIndexClient (Qdrant or PGVector, from factory).
        embedding_service: Text embedding service (BgeM3MedicalService).
        ultrasound_clip: Optional image embedding service. If None, image leg
            is disabled regardless of request.use_multimodal.
        bm25_index: Shared BM25Index instance (from bm25_fallback.py).
        session_factory: AsyncSession factory for resolving chunk metadata
            from search results.
        rrf_k: RRF rank constant (default 60, per Cormack et al. 2009).
        graph_adapter: Optional GraphQueryAdapter (PgVectorGraphAdapter). Only
            consulted when settings.graph_query_enabled is True; None means
            construct one lazily on first use.
        graph_seed_limit: How many fused top-k chunks to use as graph seeds.
        graph_neighbor_limit: Max neighbor chunks appended after top-k.
    """

    def __init__(
        self,
        vector_client: VectorIndexClient,
        embedding_service: EmbeddingService,
        ultrasound_clip: EmbeddingService | None = None,
        bm25_index: BM25Index | None = None,
        session_factory: Any | None = None,
        rrf_k: int | None = None,
        graph_adapter: Any | None = None,
        graph_seed_limit: int = 3,
        graph_neighbor_limit: int = 5,
    ):
        self._vector_client = vector_client
        self._embedding = embedding_service
        self._clip = ultrasound_clip
        self._bm25 = bm25_index
        self._session_factory = session_factory
        self._rrf_k = rrf_k if rrf_k is not None else settings.rag_rrf_k
        self._graph_adapter = graph_adapter
        self._graph_seed_limit = max(1, int(graph_seed_limit))
        self._graph_neighbor_limit = max(0, int(graph_neighbor_limit))

    async def retrieve(
        self,
        request: RetrievalRequest,
        enhanced_query: str,
    ) -> RetrievalResult:
        """Run dense + sparse (+ optional image) and RRF-merge them.

        Returns a RetrievalResult with retrieval_method=HYBRID and the
        top-k chunks. If every leg is empty, the result has zero chunks.
        """
        # Each leg is keyed by the RetrievalMethod enum value that its hits
        # carry (image leg hits are tagged IMAGE inside _safe_image, so the
        # image leg is keyed under its own IMAGE slot).
        tasks: list[tuple[RetrievalMethod, asyncio.Future]] = []
        # A.88 T4-⑥：**单次检索**的丢弃计数（PG 缺行）；挂本地变量而不是实例属性。
        drop_tally = _DropTally()
        tasks.append((
            RetrievalMethod.DENSE,
            asyncio.create_task(
                self._safe_dense(enhanced_query, request, drop_tally)
            ),
        ))
        if self._bm25 is not None:
            tasks.append((
                RetrievalMethod.SPARSE,
                asyncio.create_task(self._safe_sparse(enhanced_query, request)),
            ))
        # A.88 T4-①：影像腿**输入解析**（结构可达性修复）。
        # 旧实现的判据是 ``request.image_data is not None``，而三个生产调用点都不传
        # → 这条腿结构性不可达（开关打开也不会触发，且日志里看不出任何痕迹）。
        # 现在改为：显式 ``request.image_data`` 优先，否则按 ``request.request_id``
        # 取管线登记的注入槽（``app/rag/image_leg.attach_image_leg_for_request``）。
        # **每个分支都留自己的跳过原因**（不聚合到一个默认值，AGENTS.md 硬规则 8）。
        leg = self._resolve_image_leg(request)
        if leg.skip_reason is None:
            # 01-1 #3 分槽修复：影像腿改用独立 RetrievalMethod.IMAGE。
            # 旧实现（与 dense 腿共用 DENSE key，注释自承）在 _rrf_fuse 里等于
            # 把两条腿拼成一条 rank 序列互相挤位；独立分槽后两条腿各贡献
            # 各自的 1/(k+rank)，互不挤位。
            tasks.append((
                RetrievalMethod.IMAGE,
                asyncio.create_task(
                    self._safe_image(self._with_resolved_leg(request, leg), drop_tally)
                ),
            ))
        else:
            logger.info(
                "image_leg_skipped",
                request_id=request.request_id,
                organ=request.organ,
                reason=leg.skip_reason,
            )

        legs: dict[RetrievalMethod, list[_Hit]] = {}
        for method, task in tasks:
            try:
                hits = await task
            except Exception as exc:
                logger.warning(
                    "retriever_leg_failed",
                    leg=method.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                legs[method] = []
                continue
            existing = legs.get(method, [])
            existing.extend(hits)
            legs[method] = existing

        fused = self._rrf_fuse(legs)
        top_k = max(1, request.top_k)
        fused = fused[:top_k]
        _record_retrieval_outcome(is_empty=not fused)
        chunks = [
            ScoredChunk(
                chunk=h.chunk,
                score=h.score,
                rerank_score=None,
                retrieval_source=h.source,
            )
            for h in fused
        ]
        # 图谱邻居扩展（settings.graph_query_enabled 门控，默认关闭）：
        # 只在 top-k 之后追加，不挤占 RRF 排名；失败降级为不扩展。
        chunks = await self._expand_graph_neighbors(chunks)
        # 二期 §6：记录本次引用（best-effort，失败只记日志）。
        # 放在返回前而不是各条检索腿里：只看最终入选的 chunks——落选条目的
        # "被引用"并不是真的被用上，把它算进去会让衰减依据失真。
        await self._record_references(chunks)
        # A.88 T4-③：衰减调度（每日一次，内存节流；失败不影响本次检索）。
        await self._maybe_run_decay()

        # A.88 T4-⑥：把"向量库命中但 PG 无该行"的丢弃条数暴露到结果里。
        # §4.3「零静默失败要加定语」正是这一条：不能只有一句无日志的 continue。
        if drop_tally.total:
            logger.warning(
                "retrieval_rows_dropped_missing_in_pg",
                request_id=request.request_id,
                organ=request.organ,
                dropped=drop_tally.total,
                dropped_dense=drop_tally.dense,
                dropped_image=drop_tally.image,
                hint="向量索引与 PG 不一致（索引里有点但知识块行不在）：需重建索引/核对 kb_version",
            )

        return RetrievalResult(
            chunks=chunks,
            retrieval_method=RetrievalMethod.HYBRID,
            dropped_missing_row=drop_tally.total,
            image_leg_skip_reason=leg.skip_reason or "",
        )

    @staticmethod
    async def _record_references(chunks: list[ScoredChunk]) -> None:
        chunk_ids = [chunk.chunk.chunk_id for chunk in chunks if chunk.chunk.chunk_id]
        if not chunk_ids:
            return
        from app.knowledge.decay import record_retrieval_references

        await record_retrieval_references(chunk_ids)

    @staticmethod
    async def _maybe_run_decay() -> None:
        """检索侧的衰减调度点（A.88 T4-③）。

        ## 修的缺陷

        ``app/knowledge/decay.py::apply_decay`` 此前**只被测试调用**（架构真相
        §3.3「时效衰减调度无调用方」）→ 库里 ``confidence_score`` 恒为入库默认
        1.0，整条衰减能力是壳。

        ## 为什么挂在检索侧

        本仓库没有进程内定时任务框架；为一个每日幂等 UPDATE 引入调度器不划算。
        检索是**每请求必过**的唯一通用节点，加一次内存节流判断即可（详见
        ``decay.run_scheduled_decay`` 的注释，含"只在有流量时才跑"这条代价）。

        ## 失败不阻断检索

        ``run_scheduled_decay`` 自身永不抛；这里再加一层兜底，任何异常只记
        warning —— 衰减是维护性作业，绝不能让医生的一次检索因此失败。
        """
        try:
            from app.knowledge.decay import run_scheduled_decay

            await run_scheduled_decay(trigger="retrieval")
        except Exception as exc:
            logger.warning(
                "knowledge_decay_dispatch_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def _get_graph_adapter(self) -> Any | None:
        if self._graph_adapter is not None:
            return self._graph_adapter
        try:
            from app.knowledge.graph_query import PgVectorGraphAdapter

            self._graph_adapter = PgVectorGraphAdapter(
                session_factory=self._session_factory
            )
        except Exception as exc:
            logger.warning(
                "graph_adapter_init_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            self._graph_adapter = False  # sentinel: do not retry this instance
        return self._graph_adapter or None

    async def _expand_graph_neighbors(
        self, chunks: list[ScoredChunk]
    ) -> list[ScoredChunk]:
        """图谱扩展：以融合 top-k 的前 N 个 chunk 为种子追加 depth-1 邻居。

        - 门控：settings.graph_query_enabled 为 False 时直接原样返回；
        - 语义：邻居是检索增强补充，append 在 top-k 之后，不改排名；
        - 失败：adapter 内部已降级为空列表，这里再兜一层异常不影响主链路。

        ## 为什么**默认关闭**（不是「忘了打开」，改默认值前先读这段）

        ``settings.graph_query_enabled`` 默认 ``False`` 是**已记录的裁决结果**，
        不是待办事项。两轮预注册判据都判了 NO_GO：

        * **A.65**（16 号附录 A.65.1）：136 条 golden 上扩边的池子召回
          ``0.9853 → 0.9853``（**一个新 gold 都没捞到**）→
          ``NO_GO_NO_RECALL_GAIN``（P0：扩边必须有 ≥2pp 召回增益）。
          同轮两条硬结论：不排序的裸扩边会把 hit@5 从 0.9853 稀释到
          **0.0963**（A.64 ``SUPERNODE_RISK`` 的独立验证）；跨文档同章节边
          在现有语料上零召回收益。
        * **A.66**（A.66.1/A.66.2）：先证明评测仪器**饱和**（129/136 命中
          top-1，hit@5 天花板只剩 1.47pp）；再造难集复评，结论是「扩边真有召回
          增益，但**当前排序转不成收益**」—— 前置条件是邻居排序与可分的长文档
          语料，不是把开关打开。

        也就是说：**这个开关打开时确实生效**（A.63 已修「score=0 的邻居被
        ``min_score_threshold`` 吞掉」，见 ``app/rag/service.py`` 的 GRAPH 豁免与
        ``tests/unit/rag/test_service_graph_threshold.py`` 的钉子），
        但**没有证据支持默认打开**。要改默认值，先补一份**更难查询集**上的
        预注册评测（A.65 局限段已写明现有 golden 作为该测量仪器已饱和），
        达标再动 —— 不要把它当成「接线漏了」顺手打开。
        """
        if (
            not chunks
            or self._graph_neighbor_limit == 0
            or not bool(getattr(settings, "graph_query_enabled", False))
        ):
            return chunks
        adapter = await self._get_graph_adapter()
        if adapter is None:
            return chunks

        seen_ids = {c.chunk.chunk_id for c in chunks if c.chunk.chunk_id}
        seeds = [
            c.chunk.chunk_id
            for c in chunks[: self._graph_seed_limit]
            if c.chunk.chunk_id
        ]
        added: list[ScoredChunk] = []
        fetched = 0
        duplicates = 0
        invalid = 0
        for seed_id in seeds:
            if len(added) >= self._graph_neighbor_limit:
                break
            try:
                neighbors = await adapter.query_neighbors(seed_id, max_depth=1)
            except Exception as exc:
                logger.warning(
                    "graph_expand_failed",
                    seed=seed_id,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                break
            fetched += len(neighbors)
            for nb in neighbors:
                if len(added) >= self._graph_neighbor_limit:
                    break
                if not nb.chunk_id:
                    invalid += 1
                    continue
                if nb.chunk_id in seen_ids:
                    duplicates += 1
                    continue
                seen_ids.add(nb.chunk_id)
                added.append(
                    ScoredChunk(
                        chunk=nb,
                        score=0.0,
                        rerank_score=None,
                        retrieval_source=RetrievalMethod.GRAPH,
                    )
                )
        if added:
            logger.info(
                "graph_neighbors_appended",
                seeds=len(seeds),
                added=len(added),
                fetched=fetched,
                duplicates=duplicates,
            )
        else:
            # A.63 批 WP1：门控开着却一条邻居都没加上时留痕，且**必须区分两种成因**
            # （把它们混为一谈会误导排查方向 —— 实测真机第一跑就是这个坑）：
            #   * no_edges_for_seeds：种子块自己没有 relations（存量库没回填，或
            #     该文档只有一个 chunk）→ 跑 `scripts/kb_backfill_relations.py`；
            #   * all_neighbors_already_retrieved：adapter 拿到了邻居，但它们**已经**
            #     在本次 top-k 里（相邻块语义相近，常被一起检索到）→ 这是**正常结果**，
            #     意味着"图谱扩展在本查询上没有新增信息"，不是故障。
            logger.info(
                "graph_neighbors_empty",
                seeds=len(seeds),
                fetched=fetched,
                duplicates=duplicates,
                invalid=invalid,
                reason=(
                    "no_edges_for_seeds"
                    if fetched == 0
                    else "all_neighbors_already_retrieved"
                ),
            )
        return chunks + added

    # ------------------------------------------------------------------
    # Legs
    # ------------------------------------------------------------------

    async def _safe_dense(
        self, query: str, request: RetrievalRequest, drop_tally: _DropTally | None = None
    ) -> list[_Hit]:
        try:
            return await self._dense_search(query, request, drop_tally)
        except (EmbeddingError, VectorIndexError) as exc:
            logger.warning(
                "dense_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        except Exception as exc:
            logger.warning(
                "dense_search_unexpected",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

    async def _safe_sparse(
        self, query: str, request: RetrievalRequest
    ) -> list[_Hit]:
        if self._bm25 is None or not self._bm25.is_built:
            return []
        try:
            hits = self._bm25.search(
                query, top_k=request.top_k, organ_filter=request.organ
            )
        except Exception as exc:
            logger.warning(
                "sparse_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        return [
            _Hit(
                chunk_id=h.chunk_id,
                chunk=h.chunk,
                score=h.score,
                source=RetrievalMethod.SPARSE,
            )
            for h in hits
        ]

    def _resolve_image_leg(self, request: RetrievalRequest) -> ImageLegResolution:
        """解析本次请求的影像腿输入（显式 ``image_data`` 优先，其次请求槽）。

        ## 为什么要有这一步（A.88 T4-①）

        旧判据 ``request.image_data is not None`` 在三个生产调用点恒为 False
        （``app/agents/knowledge.py`` / ``app/fusion/consistency.py`` /
        ``app/fusion/crag.py`` 都不传字节）→ 影像腿**结构性不可达**：开关打开
        也不触发，日志里连"为什么没触发"都没有。

        现在的解析顺序与**各自的跳过原因**：

        1. 显式 ``request.image_data`` 非空 → 直接用（老行为，测试与直连调用可用）；
        2. ``use_multimodal=False`` → ``multimodal_disabled``；
        3. 没有 CLIP 服务（``self._clip is None``）→ ``clip_service_unavailable``；
        4. 按 ``request_id`` 查管线注入槽（``lookup_image_leg_slot``）：
           * 槽里有字节 → 用；槽里带 ``session_id`` 则一并作为本次的检索范围；
           * 槽里 ``skip_reason`` 非空（管线取了但没取到）→ **原样上报该原因**
             （例如 ``capability_collection_missing`` / ``image_bytes_unavailable``），
             不许改写成 "no_image_data"——那会把"这个部署没建集合"伪装成
             "这次没送字节"，正是 A.86 踩过的错；
        5. 槽也没有（管线没接线 / 过了 TTL）→ ``no_image_data``。

        病人隔离：解析出的 ``session_id`` 取自 ``request.session_id``（优先）或槽里
        那次请求的 session；**两者都没有就不发检索**（由 ``_safe_image`` 兜底）。
        """
        if request.image_data is not None:
            # A.78 病人隔离：有字节但没 session → 这条腿**不跑**，且要留原因。
            # 提前判掉而不是交给 ``_safe_image`` 内部 return []：那样结果里的
            # ``image_leg_skip_reason`` 会是空串，"这条腿为什么没贡献"又消失了。
            if not request.session_id:
                return ImageLegResolution(
                    request.image_data,
                    request.image_format,
                    None,
                    IMAGE_SKIP_NO_SESSION_FOR_LEG,
                )
            return ImageLegResolution(
                image_data=request.image_data,
                image_format=request.image_format,
                session_id=request.session_id,
                skip_reason=None,
            )
        if not request.use_multimodal:
            return ImageLegResolution(None, request.image_format, request.session_id,
                                      IMAGE_SKIP_MULTIMODAL_OFF)
        if self._clip is None:
            return ImageLegResolution(None, request.image_format, request.session_id,
                                      IMAGE_SKIP_CLIP_UNAVAILABLE)
        slot = resolve_image_leg_slot(request.request_id)
        if slot is None:
            return ImageLegResolution(None, request.image_format, request.session_id,
                                      IMAGE_SKIP_NO_DATA)
        if slot.image_data is None:
            # 管线尝试取过图但没取到：**保留它自己的原因码**，不覆盖。
            return ImageLegResolution(
                None,
                request.image_format,
                request.session_id or slot.session_id or None,
                slot.skip_reason or IMAGE_SKIP_NO_DATA,
            )
        session_for_leg = request.session_id or slot.session_id or None
        if not session_for_leg:
            return ImageLegResolution(
                slot.image_data, slot.image_format, None, IMAGE_SKIP_NO_SESSION_FOR_LEG
            )
        return ImageLegResolution(
            image_data=slot.image_data,
            image_format=slot.image_format,
            session_id=session_for_leg,
            skip_reason=None,
        )

    @staticmethod
    def _with_resolved_leg(
        request: RetrievalRequest, leg: ImageLegResolution
    ) -> RetrievalRequest:
        """把解析结果覆盖到**副本**上（不改调用方传进来的请求对象）。"""
        return request.model_copy(
            update={
                "image_data": leg.image_data,
                "image_format": leg.image_format,
                "session_id": leg.session_id,
            }
        )

    async def _safe_image(
        self, request: RetrievalRequest, drop_tally: _DropTally | None = None
    ) -> list[_Hit]:
        if self._clip is None or request.image_data is None:
            return []
        # A.78 病人隔离：没有 session 就**不发检索请求**（宁可这条腿空，也不跨病人搜）。
        if not request.session_id:
            warn_once(
                "image_leg_no_session",
                "image_leg_skipped_no_session",
                hint="影像检索必须带 session_id 才能限定病人范围；无 session 时该腿直接跳过",
            )
            return []
        try:
            resp = await self._clip.embed_image(
                ImageEmbeddingRequest(
                    image_data=request.image_data,
                    image_format=request.image_format,
                    request_id=request.request_id,
                    session_id=request.session_id,
                )
            )
        except EmbeddingError as exc:
            logger.warning(
                "image_embedding_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        if not resp.embeddings:
            return []
        query_vector = self._embedding_to_list(resp.embeddings[0])
        # A.78 病人隔离：影像腿**必须**按 session 过滤。
        # 旧实现只按 organ 过滤 —— 一旦影像向量库灌入多病人数据，
        # 一次请求就会召回**别的病人**的影像（这是数据泄露，不是精度问题）。
        #
        # A.90：过滤条件按「严格 → 宽松」依次尝试（`image_search_filters`）。
        # 真机实测原因：历史 payload 里 292/316 个 session 没有 `organ` 字段
        # （建库时 `if row.get("organ")` 才写，而多数 session 没有判读记录），
        # 于是 `session + organ` 双条件**恒 0 命中**，这条腿长期"通了但永远是空"。
        # 宽松档只去掉 organ、**仍然带 session**，病人隔离不受影响。
        filters = image_search_filters(request.organ, request.session_id)
        if not filters:
            # 无 session 不发请求（宁可这条腿空，也不跨病人搜）
            return []
        results = None
        try:
            for i, flt in enumerate(filters):
                results = await self._vector_client.search(
                    collection=settings.rag_image_collection,
                    query_vector=query_vector,
                    top_k=request.top_k,
                    filter=flt,
                )
                if results:
                    if i > 0:
                        logger.info(
                            "image_leg_filter_relaxed",
                            request_id=request.request_id,
                            tried=len(filters),
                            used=list(flt.keys()),
                            hits=len(results),
                            hint="严格档 0 命中（多见于历史 payload 缺 organ），已退到仅按 session",
                        )
                    break
            if not results:
                logger.info(
                    "image_leg_no_hits",
                    request_id=request.request_id,
                    filters_tried=[list(f.keys()) for f in filters],
                )
        except VectorIndexError as exc:
            if _is_collection_not_found(exc):
                # A.68 / R1：集合缺失是**配置缺陷**，不是正常路径 → warning（仅一次），
                # 否则"开了但一直空转"会安静地持续下去（A.63 图谱边被静默吞掉的同族问题）。
                warn_once(
                    f"image_collection_missing:{settings.rag_image_collection}",
                    "image_leg_collection_missing",
                    collection=settings.rag_image_collection,
                    hint="建好该集合（512 维）并把 rag_use_multimodal 置 True，否则这条腿零贡献",
                )
                return []
            logger.warning(
                "image_search_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []
        except Exception as exc:
            if _is_collection_not_found(exc):
                warn_once(
                    f"image_collection_missing:{settings.rag_image_collection}",
                    "image_leg_collection_missing",
                    collection=settings.rag_image_collection,
                    hint="建好该集合（512 维）并把 rag_use_multimodal 置 True，否则这条腿零贡献",
                )
                return []
            logger.warning(
                "image_search_unexpected",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return []

        return await self._wrap_search_results(
            results, RetrievalMethod.IMAGE, drop_tally
        )

    async def _dense_search(
        self, query: str, request: RetrievalRequest, drop_tally: _DropTally | None = None
    ) -> list[_Hit]:
        # Embed the query (1 call, no retry logic here; the orchestrator wraps).
        text_resp = await self._embedding.embed_text(
            TextEmbeddingRequest(
                texts=[query],
                request_id=request.request_id,
                session_id=request.session_id,
            )
        )
        if not text_resp.embeddings:
            return []
        query_vector = self._embedding_to_list(text_resp.embeddings[0])
        collection = request.kb_version or settings.rag_collection
        flt = {"organ": request.organ} if request.organ else None
        results = await self._vector_client.search(
            collection=collection,
            query_vector=query_vector,
            top_k=request.top_k,
            filter=flt,
        )
        return await self._wrap_search_results(
            results, RetrievalMethod.DENSE, drop_tally
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _wrap_search_results(
        self,
        results: list[SearchResult],
        source: RetrievalMethod,
        drop_tally: _DropTally | None = None,
    ) -> list[_Hit]:
        """把向量检索命中回填成 ``_Hit``。

        A.88 T4-⑥：回填时**PG 里查不到该 chunk_id** 的行不再静默 ``continue`` ——
        逐条 ``logger.warning`` + 计数（``drop_tally``）。这是 §4.3「零静默失败
        要加定语」的具体一条：旧实现无日志无计数，"索引比库多几条"这种不一致
        在生产上完全看不见。

        ## A.90：**影像点不再走回表**（这是影像腿的最后一道死结）

        真机取证（2026-10-25）：影像腿检索**真的有命中**
        （`collection=ultrasound_chunks returned=3`，score 0.98~1.00），
        但紧接着全部被这里丢掉：

            retrieval_hit_dropped_missing_in_pg chunk_id=img_20261014_88fce652
              hint=向量索引里有点，但 knowledge_chunks 没有这一行
            retrieval_rows_dropped_missing_in_pg dropped=3 dropped_dense=3

        原因不是数据不一致，而是**两类向量被混在同一条回表路径上**：
        影像点的 id 形如 `img_…`，payload 自带 `image_id/storage_path`，
        它**本来就不该有 `knowledge_chunks` 行**（那是文本知识块的表）。
        于是"接线通了 → 有命中 → 全被当脏数据丢掉"。

        修法：**按 id 形态分流** —— `img_` 前缀的命中直接构造"影像块"
        （正文用 payload 里的可读描述，绝不伪造成知识正文），
        只有非 `img_` 命中才回表。这样影像腿的命中真的能进融合，
        而 `retrieval_rows_dropped_missing_in_pg` 也恢复它本来的语义
        （"文本索引与 PG 不一致"），不再被影像点常年顶成 3。
        """
        if not results:
            return []
        # A.90：影像点分流（见 docstring）。必须在回表之前判，
        # 否则它们会被当成"索引有、库里没有"的脏数据丢掉。
        image_hits, text_results = _split_image_hits(results, source)
        if image_hits:
            logger.info(
                "image_leg_hits_admitted",
                count=len(image_hits),
                chunk_ids=[h.chunk_id for h in image_hits[:5]],
                hint="影像点不回表（img_… 不在 knowledge_chunks），直接作为影像块参与融合",
            )
        results = text_results
        if not results:
            return image_hits
        if self._session_factory is None:
            # Without a session factory we cannot hydrate chunks; fall back to
            # placeholder content. This branch is used by tests with mocks.
            return image_hits + [
                _Hit(
                    chunk_id=r.id,
                    chunk=_placeholder_chunk(r.id),
                    score=float(r.score),
                    source=source,
                )
                for r in results
            ]
        session: AsyncSession = self._session_factory()
        try:
            from sqlalchemy import select

            from app.models.database import KnowledgeChunkModel

            ids = [r.id for r in results]
            stmt = select(KnowledgeChunkModel).where(
                KnowledgeChunkModel.chunk_id.in_(ids)
            )
            db_result = await session.execute(stmt)
            rows = {row.chunk_id: row for row in db_result.scalars().all()}
        finally:
            close = getattr(session, "close", None)
            if callable(close):
                res = close()
                if hasattr(res, "__await__"):
                    await res

        score_map = {r.id: float(r.score) for r in results}
        hits: list[_Hit] = []
        dropped = 0
        for r in results:
            row = rows.get(r.id)
            if row is None:
                # A.88 T4-⑥：**不再静默丢弃**。逐条留痕（trace 级，避免刷屏）+
                # 汇总计数（warning 由 retrieve() 统一打，带 request_id）。
                dropped += 1
                logger.warning(
                    "retrieval_hit_dropped_missing_in_pg",
                    chunk_id=r.id,
                    retrieval_source=source.value,
                    score=score_map.get(r.id, 0.0),
                    hint="向量索引里有点，但 knowledge_chunks 没有这一行",
                )
                continue
            chunk = KnowledgeChunk(
                chunk_id=row.chunk_id,
                kb_version=row.kb_version,
                organ=row.organ,
                content=row.content,
                metadata=dict(row.meta) if row.meta else {},
                embedding=list(row.embedding) if row.embedding is not None else None,
                source_doc_id=row.source_doc_id,
                section_path=row.section_path or "",
                token_count=row.token_count or 0,
            )
            hits.append(
                _Hit(
                    chunk_id=r.id,
                    chunk=chunk,
                    score=score_map.get(r.id, 0.0),
                    source=source,
                )
            )
        if dropped and drop_tally is not None:
            drop_tally.add(source=source, count=dropped)
        return image_hits + hits

    def _rrf_fuse(
        self, legs: dict[RetrievalMethod, list[_Hit]]
    ) -> list[_Hit]:
        """Reciprocal Rank Fusion: fused(d) = sum(1 / (k + rank_i(d))).

        Preserves the original _Hit.chunk / _Hit.source from the first leg
        where the chunk appears; ranks the fused list by fused score.
        """
        k = self._rrf_k
        fused_scores: dict[str, float] = {}
        first_seen: dict[str, _Hit] = {}
        for source, hits in legs.items():
            # Sort this leg by score desc to derive ranks.
            ordered = sorted(hits, key=lambda h: h.score, reverse=True)
            for rank, hit in enumerate(ordered, start=1):
                fused_scores[hit.chunk_id] = fused_scores.get(
                    hit.chunk_id, 0.0
                ) + 1.0 / (k + rank)
                if hit.chunk_id not in first_seen:
                    first_seen[hit.chunk_id] = hit

        # Deduplicate by chunk_id, keep the hit with the highest RRF score.
        unique: dict[str, _Hit] = {}
        for cid, score in fused_scores.items():
            hit = first_seen[cid]
            # Use the fused RRF score as the new "score" so consumers see a
            # single, comparable value.
            updated = _Hit(
                chunk_id=hit.chunk_id,
                chunk=hit.chunk,
                score=score,
                source=hit.source,
            )
            unique[cid] = updated
        return sorted(
            unique.values(), key=lambda h: h.score, reverse=True
        )

    @staticmethod
    def _embedding_to_list(emb: Any) -> list[float]:
        """Coerce an embedding (np.ndarray, list, etc.) to a flat list of floats."""
        try:
            import numpy as np
            if isinstance(emb, np.ndarray):
                return emb.astype(np.float32).ravel().tolist()
        except Exception as exc:
            logger.debug("retriever_cleanup_failed", error=str(exc))
        if isinstance(emb, list):
            return [float(x) for x in emb]
        if hasattr(emb, "tolist"):
            return [float(x) for x in emb.tolist()]
        return [float(emb)]

    async def health_check(self) -> dict[str, bool]:
        """Check health of embedding service and vector index.

        Returns a dict with keys "embedding" and "vector_index", values are
        True if healthy, False otherwise.
        """
        results: dict[str, bool] = {}
        try:
            results["embedding"] = bool(await self._embedding.health_check())
        except Exception as exc:
            logger.warning("retriever_health_embedding_failed", error=str(exc))
            results["embedding"] = False
        try:
            results["vector_index"] = bool(await self._vector_client.health_check())
        except Exception as exc:
            logger.warning("retriever_health_vector_index_failed", error=str(exc))
            results["vector_index"] = False
        return results

    async def close(self) -> None:
        """Close resources held by the retriever."""
        for obj in (self._vector_client, self._embedding):
            close = getattr(obj, "close", None)
            if callable(close):
                try:
                    res = close()
                    if asyncio.iscoroutine(res):
                        await res
                except Exception as exc:
                    logger.debug("retriever_close_failed", error=str(exc))


def _placeholder_chunk(chunk_id: str) -> KnowledgeChunk:
    """Used when no session_factory is available (e.g. lightweight tests)."""
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="unknown",
        organ="unknown",
        content="[placeholder]",
        metadata={},
        embedding=None,
        source_doc_id=chunk_id,
        section_path="",
        token_count=0,
    )


#: 影像向量点的 id 前缀（`build_image_vectors` 用 `image_id` 做点 id）。
IMAGE_CHUNK_ID_PREFIX = "img_"


def is_image_chunk_id(chunk_id: str | None) -> bool:
    """该向量 id 是否属于**影像集合**（而不是文本知识块）。纯函数（守卫钉住）。"""
    return str(chunk_id or "").startswith(IMAGE_CHUNK_ID_PREFIX)


def _image_chunk_text(chunk_id: str, payload: dict | None) -> str:
    """影像点 → **可读正文**（给融合层与报告用）。

    为什么不能留空：`_Hit.chunk.content` 若为空串，后续按内容做的
    分级/建议关键词匹配会得到"空匹配"（假一致）。所以给一段
    **明确标注为影像参考、且只含 payload 里真实存在字段** 的描述。
    **绝不编造影像所见**（那是临床内容，必须来自模型/医生）。
    """
    p = payload or {}
    bits = [f"[影像参考] {chunk_id}"]
    if p.get("modality"):
        bits.append(f"模态={p['modality']}")
    if p.get("organ"):
        bits.append(f"部位={p['organ']}")
    if p.get("session_id"):
        # session_id 是检查会话标识（非 PHI），用于溯源
        bits.append(f"会话={p['session_id']}")
    if p.get("image_id"):
        bits.append(f"影像={p['image_id']}")
    return "；".join(bits)


def _split_image_hits(
    results: list[SearchResult], source: RetrievalMethod
) -> tuple[list[_Hit], list[SearchResult]]:
    """把命中分成「影像点」与「需回表的文本命中」。纯函数（守卫钉住）。

    A.90：为什么必须分流（真机取证）—— 影像点 id 形如 `img_…`，
    `knowledge_chunks` 表**本来就没有这些行**；旧实现一律回表 →
    影像腿的 3 条高置信命中（score 0.98~1.00）被 `retrieval_hit_dropped_missing_in_pg`
    当脏数据丢掉，于是"接线修好了"却永远零贡献。

    Returns:
        ``(image_hits, text_results)`` —— 前者可直接进融合，后者照旧回表。
    """
    image_hits: list[_Hit] = []
    text_results: list[SearchResult] = []
    for r in results:
        if not is_image_chunk_id(r.id):
            text_results.append(r)
            continue
        # SimpleSearchResult 的字段名是 `metadata`（承载 payload），不是 `payload`
        meta = dict(getattr(r, "metadata", None) or {})
        # `KnowledgeChunk.organ` 的契约**不允许空串**（`organ_must_not_be_empty`），
        # 而真机上 292/316 个 session 的影像 payload 没有 organ ——
        # 直接传 "" 会抛 ValidationError 把整条腿打挂（本函数在 _safe_image 里，
        # 抛错会被外层当"影像腿失败"吞掉，于是又变成"跑不通"）。
        # 用 "unknown"（与 `_placeholder_chunk` 同一惯例）并留痕，不猜器官。
        organ_value = str(meta.get("organ") or "").strip() or "unknown"
        if organ_value == "unknown":
            logger.debug(
                "image_chunk_organ_unknown",
                chunk_id=r.id,
                hint="影像 payload 无 organ 字段（多为该 session 没有判读记录）",
            )
        image_hits.append(
            _Hit(
                chunk_id=r.id,
                chunk=KnowledgeChunk(
                    chunk_id=r.id,
                    kb_version=str(meta.get("kb_version") or "image"),
                    organ=organ_value,
                    content=_image_chunk_text(r.id, meta),
                    metadata=meta,
                    embedding=None,
                    source_doc_id=str(meta.get("image_id") or r.id),
                    section_path="",
                    token_count=0,
                ),
                score=float(r.score),
                source=source,
            )
        )
    return image_hits, text_results


__all__ = ["Retriever"]
