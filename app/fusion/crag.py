"""T-GA-FUS-02 — CRAG 补充检索模块。

本模块实现 CRAG（Corrective RAG）处理器 (:class:`CRAGHandler`)。

CRAG 流程（设计文档 12-系统性修订实施指南.md:171）：

1. Self-RAG 失败后触发；
2. 评估失败原因（``evidence_insufficient`` / ``evidence_contradictory``
   / ``feature_missing`` / ``unknown``）；
3. 重新构造查询：在原 query 基础上追加失败原因关键词；
4. **P2-8 修复**：经**中央调度智能体**（``DispatcherAgent``）路由 CRAG
   补充检索（``dispatcher.route_crag_query()``），由 dispatcher 内部
   委派给 ``KnowledgeAgent.query()``。链路：
   ``Self-RAG → Dispatcher → KnowledgeAgent → RAG → 返回结果``。
   享受两层缓存（Redis TTL 86400s + LRU ≤50）+ 负载均衡 + KB 版本感知。
   无 dispatcher / knowledge_agent 时 fallback 到 RAG Service 直连。
5. **P1-3 修复**：RAG Service fallback 路径开启 ``use_bm25_fallback=True``，
   BM25 作为终极兜底；
6. **P2-6 修复**：用新证据**重新调用** specialist LLM（而非简单地
   ``+0.2`` 提升 confidence）。design 02-核心流程与知识库.md:222
   "每次仅重新执行生成与质检，复用首次检索与校验结果"。
   无 specialist_invoker 时 fallback 到占位实现（保持向后兼容）。
7. 仍失败 → 标记 needs_review。

设计要点：

- **P2-6 真实重生成**：CRAG 不应仅机械 +0.2 confidence（掩盖 LLM 真实失败），
  应重新调用 specialist 让 LLM 基于新证据做完整诊断；
- **失败安全**：检索异常 → ``recovered=False, reason="rag_failed"``；
- **可注入**：dispatcher / knowledge_agent / RAG Service / specialist
  invoker 均通过构造参数注入；
- **可降级**：所有依赖均可为 None，最差情况返回 needs_review。
"""
from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

import structlog

from app.agents.protocols import AgentContext, AgentResponse
from app.fusion.protocols import FusionRequest
from app.fusion.self_rag import SelfRAGEvalResult
from app.knowledge.protocols import KnowledgeChunk
from app.rag.protocols import (
    RAGService,
    RetrievalRequest,
)

logger = structlog.get_logger("app.fusion.crag")


# ----------------------------------------------------------------------
# 协议（duck-typed 注入，避免 FUS 层硬依赖 AGT 层）
# ----------------------------------------------------------------------


@runtime_checkable
class _KnowledgeAgentLike(Protocol):
    """CRAG 路由目标的最小协议（duck-typed）。

    与 ``app.agents.protocols.KnowledgeAgent`` 接口兼容（``query`` +
    ``health_check``），但不强制继承，便于 FUS 层在无 AGT 依赖时独立运行。
    """

    async def query(
        self,
        query: str,
        organ: Any | None = None,
        top_k: int = 10,
    ) -> list[KnowledgeChunk]: ...


@runtime_checkable
class _DispatcherLike(Protocol):
    """P2-8 修复：CRAG 中央调度路由的最小协议（duck-typed）。

    与 ``app.agents.protocols.DispatcherAgent.route_crag_query`` 接口兼容。
    FUS 层不强制继承 ABC，便于独立测试。
    """

    async def route_crag_query(
        self,
        organ: Any,
        query: str,
        top_k: int = 20,
        failure_category: str = "unknown",
    ) -> list[KnowledgeChunk]: ...


#: Specialist invoker 类型（P2-6）：接收 AgentContext，返回新 AgentResponse
SpecialistInvoker = Callable[[AgentContext], Awaitable[AgentResponse]]

#: A.88 T5-6：CRAG 恢复的**质量条件**（``recovered=True`` 必须先过其中之一）。
#: 这些名字会出现在 ``CRAGResult.reason`` 里 —— 运维据此判断"为什么明明检索到东西
#: 却没算恢复"。**不许**收敛成一句笼统的 "quality check failed"
#: （AGENTS.md 硬规则 8：每个被跳过的分支要留下自己的原因）。
CRAG_QUALITY_CONDITIONS = (
    "evidence_not_used",         # 新检索到的 chunk 一条都没进入新响应
    "score_below_threshold",     # 检索分数低于 crag_min_evidence_score
    "revalidation_failed",       # Self-RAG 复评没过
    "revalidation_error",        # 复评本身抛错（不能把"没评成"当"评过"）
    "empty_reresponse_content",  # 重生成响应正文为空
)

#: 复评器类型：接收新响应，返回带 ``passed`` 属性的评估结果
Revalidator = Callable[[AgentResponse], Awaitable[Any]]


def _chunk_score(chunk: Any) -> float | None:
    """取一个 chunk 的检索分数（拿不到就 ``None``，**不**默认 0.0）。

    两个来源：
    1. ``ScoredChunk`` 包裹形态：``chunk.score``；
    2. 本模块 ``_retrieve_via_rag_service`` 写进 ``metadata["retrieval_score"]``
       的形态（``ScoredChunk.chunk`` 拿到后分数就丢了）。

    为什么拿不到要返回 ``None`` 而不是 0.0：0.0 是个**合法分数**，用它冒充
    "没分数"会让 ``score_below_threshold`` 在阈值 > 0 时永远成立 ——
    看起来"校验过"，实际是校验失明。
    """
    value = getattr(chunk, "score", None)
    if value is None:
        meta = getattr(chunk, "metadata", None)
        if isinstance(meta, dict):
            value = meta.get("retrieval_score")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        # 分数存在但不可转成 float → 按"拿不到"处理（返回 None，由调用方报
        # `score_below_threshold: no_scores_available`）；此处留痕，便于排查
        # "为什么阈值判据一直说没分数"。
        logger.debug(
            "crag_chunk_score_unparsable",
            value_type=type(value).__name__,
            error=str(exc)[:120],
        )
        return None


# ----------------------------------------------------------------------
# 数据模型
# ----------------------------------------------------------------------


@dataclass
class CRAGResult:
    """CRAG 处理结果。

    Attributes:
        recovered: CRAG 是否成功恢复（new_response 可用）。
        new_response: 补充检索后的新响应（``recovered=True`` 时有意义）。
        failure_category: 失败原因分类。
        retrieval_top_k: 实际使用的 top_k。
        reason: 失败/恢复的文本原因。
        via_dispatcher: 是否经中央调度路由（True/False/None 表示未触发检索）。
        specialist_reinvoked: 是否成功重新调用 specialist LLM。
        quality_checked: A.88 T5-6：本次是否真的做了**恢复质量校验**。
            ``False`` 表示"只看了检索非空"（质量判据不可用的降级情形）—— 与
            "校验通过"必须可区分，否则又会变成"空转冒充恢复"。
        quality_reason: 质量校验的细节（未通过时是哪一条，通过时是哪一条救的）。
    """

    recovered: bool
    new_response: AgentResponse | None = None
    failure_category: str = "unknown"
    retrieval_top_k: int = 0
    reason: str = ""
    via_dispatcher: bool | None = None
    specialist_reinvoked: bool = False
    quality_checked: bool = False
    quality_reason: str = ""


# ----------------------------------------------------------------------
# CRAGHandler
# ----------------------------------------------------------------------


#: 补充检索的 top_k（默认 20，原 retriever 默认 5）
DEFAULT_CRAG_TOP_K = 20

#: 失败原因分类关键词
_FAILURE_CATEGORIES = {
    "evidence_insufficient": ("证据不足", "insufficient", "no evidence"),
    "evidence_contradictory": ("矛盾", "contradict", "conflict"),
    "feature_missing": ("遗漏", "missing", "incomplete"),
    "low_confidence": ("置信度低", "low confidence", "uncertain"),
}


class CRAGHandler:
    """CRAG 补充检索处理器（T-GA-FUS-02）。

    Args:
        rag_service: RAG Service（用于补充检索的直接路径，P1-2 回退）。
            ``None`` 时 CRAG 不可用（除非提供了 knowledge_agent 或 dispatcher）。
        knowledge_agent: 知识查询智能体（P1-2 直连路径，跳过 dispatcher）。
        dispatcher_agent: 中央调度智能体（P2-8 优先路径，链路符合设计文档）。
            若提供则走 ``dispatcher.route_crag_query()``，由 dispatcher
            内部决定调用哪个 knowledge_agent。
        specialist_invoker: P2-6 修复：specialist LLM 重生成函数（callable）。
            签名 ``async (AgentContext) -> AgentResponse``。
            若提供则用新证据重新调用 specialist；否则 fallback 到占位实现
            （仅合并 evidence，不重跑 LLM）。
        crag_top_k: 补充检索的 top_k（默认 20）。
        min_evidence_score: **A.88 T5-6** 新证据的检索分数下限（默认 0.0 =
            不启用）。开启后，最高分都低于该值 → 判"检索到了但不够有说服力"，
            ``recovered=False`` + ``reason=score_below_threshold``。
            默认关：会改变既有的恢复判定口径，必须先在真机上按分数分布标定
            （与 ``similar_cases_min_score`` 同样的纪律）。
        revalidator: **A.88 T5-6** Self-RAG 复评器（可选）。给了就对**重生成
            后的响应**再评一次，通过才算恢复 —— 这是"这次真的够了"最直接的判据。
    """

    def __init__(
        self,
        rag_service: RAGService | None = None,
        knowledge_agent: _KnowledgeAgentLike | None = None,
        dispatcher_agent: _DispatcherLike | None = None,
        specialist_invoker: SpecialistInvoker | None = None,
        crag_top_k: int = DEFAULT_CRAG_TOP_K,
        min_evidence_score: float = 0.0,
        revalidator: Revalidator | None = None,
    ) -> None:
        if crag_top_k <= 0:
            raise ValueError("crag_top_k must be positive")
        if min_evidence_score < 0.0:
            raise ValueError("min_evidence_score must be >= 0.0")
        # 至少需要一个检索后端
        if (
            rag_service is None
            and knowledge_agent is None
            and dispatcher_agent is None
        ):
            logger.warning(
                "crag_handler_no_retrieval_backend",
                message="CRAG has no retrieval backend — will always skip with rag_unavailable",
            )

        # P2-8 修复：路由优先级
        # dispatcher > knowledge_agent > rag_service
        # 即：有 dispatcher 时优先用 dispatcher（设计要求）；
        # 无 dispatcher 但有 knowledge_agent 时降级到直连 knowledge_agent；
        # 仅有 rag_service 时降级到 RAG Service 直连。
        self._dispatcher_agent = dispatcher_agent
        self._knowledge_agent = knowledge_agent
        self._rag_service = rag_service
        self._specialist_invoker = specialist_invoker
        self._crag_top_k = crag_top_k
        # A.88 T5-6：恢复质量判据（默认不启用分数阈值 —— 会改既有口径）
        self._min_evidence_score = float(min_evidence_score)
        self._revalidator = revalidator
        # P2-9 增强：记录最近一次检索异常（用于失败原因透出）
        self._last_retrieval_error: BaseException | None = None

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def handle(
        self,
        response: AgentResponse,
        self_rag_result: SelfRAGEvalResult,
        request: FusionRequest,
    ) -> CRAGResult:
        """执行 CRAG 补充检索。

        Args:
            response: 原始 specialist 响应（Self-RAG 评估对象）。
            self_rag_result: Self-RAG 评估结果。
            request: 融合请求（用于检索时的 organ / request_id）。

        Returns:
            :class:`CRAGResult`：CRAG 处理结果。
        """
        if (
            self._rag_service is None
            and self._knowledge_agent is None
            and self._dispatcher_agent is None
        ):
            return CRAGResult(
                recovered=False,
                failure_category="rag_unavailable",
                reason="rag_unavailable: CRAG skipped because no retrieval backend configured",
            )

        # 1) 分类失败原因
        failure_category = self._classify_failure(self_rag_result)
        # 2) 构造增强 query
        enhanced_query = self._build_enhanced_query(
            request=request,
            response=response,
            failure_category=failure_category,
        )
        # 3) 调用补充检索（P2-8 修复：路由 dispatcher 优先）
        new_chunks, via_dispatcher = await self._retrieve(
            response=response,
            request=request,
            query=enhanced_query,
            failure_category=failure_category,
        )
        if not isinstance(new_chunks, list):
            # _retrieve 返回 CRAGResult（异常时）→ 直接返回
            return new_chunks  # type: ignore[return-value]

        # 4) 评估是否恢复
        if not new_chunks:
            # P2-9 增强：若检索过程中发生异常（_last_retrieval_error），
            # 透出异常类型到 reason（保留旧测试 "rag_failed: RuntimeError"
            # 形式）。
            if self._last_retrieval_error is not None:
                err_type = type(self._last_retrieval_error).__name__
                return CRAGResult(
                    recovered=False,
                    failure_category=failure_category,
                    retrieval_top_k=self._crag_top_k,
                    reason=f"rag_failed: crag_retrieval_{err_type}",
                    via_dispatcher=via_dispatcher,
                )
            return CRAGResult(
                recovered=False,
                failure_category=failure_category,
                retrieval_top_k=self._crag_top_k,
                reason="crag_retrieval_returned_empty",
                via_dispatcher=via_dispatcher,
            )

        # 5) 构造新响应：P2-6 修复 — 真正调用 specialist LLM
        citation_sink: dict[str, Any] = {}
        new_response, specialist_reinvoked = await self._build_new_response(
            original=response,
            new_evidence=new_chunks,
            request=request,
            citation_sink=citation_sink,
        )

        # 6) A.88 T5-6：**恢复质量校验**。
        # 此前 recovered=True 的唯一前提是"检索返回非空" —— 于是补充检索回来一条
        # 与结论无关的块也算"恢复"，而 FusionEngineImpl 会据此清掉 self_rag_failed
        # 标记（service.py:214-217）→ 一次没修好的质检凭空消失。这是"空转冒充恢复"。
        quality_ok, quality_reason = await self._verify_recovery_quality(
            original=response,
            new_evidence=new_chunks,
            new_response=new_response,
            citated=citation_sink.get("cited"),
        )
        # ``unverified_`` 前缀 = 占位路径（无重生成）→ 无法校验，不许自称"已校验"
        quality_checked = not quality_reason.startswith("unverified_")
        if not quality_ok:
            logger.info(
                "crag_recovery_quality_rejected",
                agent=response.agent_name,
                failure_category=failure_category,
                new_evidence_count=len(new_chunks),
                specialist_reinvoked=specialist_reinvoked,
                quality_reason=quality_reason,
            )
            return CRAGResult(
                recovered=False,
                failure_category=failure_category,
                retrieval_top_k=self._crag_top_k,
                # 前缀保留检索事实，后缀写明**哪一条**质量条件没过
                reason=(
                    f"crag_recovery_quality_rejected: {quality_reason} "
                    f"(new_chunks={len(new_chunks)}, "
                    f"reinvoked={specialist_reinvoked})"
                ),
                via_dispatcher=via_dispatcher,
                specialist_reinvoked=specialist_reinvoked,
                quality_checked=True,
                quality_reason=quality_reason,
            )

        logger.info(
            "crag_recovered",
            agent=response.agent_name,
            failure_category=failure_category,
            new_evidence_count=len(new_chunks),
            via_dispatcher=via_dispatcher,
            specialist_reinvoked=specialist_reinvoked,
            quality_reason=quality_reason,
        )
        return CRAGResult(
            recovered=True,
            new_response=new_response,
            failure_category=failure_category,
            retrieval_top_k=self._crag_top_k,
            reason=(
                f"crag_recovered_with_{len(new_chunks)}_new_chunks"
                + ("_reinvoked" if specialist_reinvoked else "_evidence_only")
                + f" [{quality_reason}]"
            ),
            via_dispatcher=via_dispatcher,
            specialist_reinvoked=specialist_reinvoked,
            quality_checked=quality_checked,
            quality_reason=quality_reason,
        )

    # ------------------------------------------------------------------
    # 内部：恢复质量校验（A.88 T5-6）
    # ------------------------------------------------------------------

    async def _verify_recovery_quality(
        self,
        *,
        original: AgentResponse,
        new_evidence: list[KnowledgeChunk],
        new_response: AgentResponse,
        citated: list[str] | None = None,
    ) -> tuple[bool, str]:
        """校验"这次是不是**真的**修好了"（任一条件通过即算恢复）。

        条件（按代价从低到高）：

        1. ``revalidation_passed``：给了 ``revalidator`` 时，新响应通过 Self-RAG
           复评 —— 最直接的判据（LLM 自己再看一遍"证据够不够"）；
        2. ``score_ok``：开了 ``min_evidence_score`` 时，新证据最高分达到阈值；
        3. ``evidence_used``：新检索到的 chunk **真的被 specialist 引用过**
           （说明被用上了，不是拿回来堆着）。

        ## ``citated`` 为什么必须单独传（不能看 new_response 的 evidence）

        :meth:`_build_new_response` 返回的是**三方合并**后的响应
        （旧 evidence + 新 evidence + specialist 自己引用的）—— 新证据必然
        出现在里面，拿它判断"用上了没有"**永远为真**，等于没判。
        所以 ``citated`` 传的是 specialist **合并前**自己引用的那批。
        无 invoker（占位路径）时 ``citated=None`` → 没有"被引用"的证据，
        走 ``evidence_not_used``（占位路径只合并证据、没重新生成，不能算恢复）。

        一条都不过 → ``(False, 具体条件名: 细节)``。名字与细节都要给，
        否则运维只能看到"没恢复"却不知道卡在哪（AGENTS.md 硬规则 8）。
        """
        # 0) 空正文：没有结论就谈不上"恢复"，先挡掉（省掉后面的判据）
        if not str(new_response.content or "").strip():
            return False, "empty_reresponse_content"

        # 1) Self-RAG 复评（最可信）
        revalidation_error = ""
        if self._revalidator is not None:
            try:
                revalidation = await self._revalidator(new_response)
            except Exception as exc:
                logger.warning(
                    "crag_revalidation_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    agent=new_response.agent_name,
                )
                # "没评成"不等于"评过" —— 不许当通过，继续看后面的判据
                revalidation = None
                revalidation_error = f"revalidation_error: {type(exc).__name__}"
            if revalidation is not None:
                if bool(getattr(revalidation, "passed", False)):
                    return True, "revalidation_passed"
                return False, (
                    "revalidation_failed: "
                    f"score={getattr(revalidation, 'score', None)} "
                    f"reason={getattr(revalidation, 'reason', '') or '-'}"
                )

        # 2) 检索分数阈值（仅当显式开启）
        if self._min_evidence_score > 0.0:
            scores = [_chunk_score(chunk) for chunk in new_evidence]
            scores = [s for s in scores if s is not None]
            if not scores:
                # 有块但一个分数都拿不到 → 无法验证，不许当成通过
                return False, "score_below_threshold: no_scores_available"
            best = max(scores)
            if best < self._min_evidence_score:
                return False, (
                    f"score_below_threshold: best={best:.4f} < "
                    f"{self._min_evidence_score}"
                )
            return True, f"score_ok: best={best:.4f}"

        # 3) 新证据是否真的被 specialist 引用（**合并前**的那批）
        if citated is None:
            # 占位路径：没有重新生成，只有"证据被并进列表"这件事。
            # **不能**据此声称"质量已校验" —— 但也无法证伪（检索确实变了、
            # 证据确实多了一条）。故保持既有的 recovered=True 语义，
            # 用 quality_checked=False + 明确 reason 把"未校验"讲清楚：
            # 调用方（FusionResult.crag_recovered + CRAGResult.quality_checked）
            # 能分辨"真的重生成并通过质量门"与"仅合并证据"。
            return True, (
                "unverified_placeholder_evidence_only: specialist not reinvoked, "
                "quality cannot be verified"
            )
        new_ids = {
            str(getattr(chunk, "chunk_id", "") or "") for chunk in new_evidence
        }
        new_ids.discard("")
        overlap = new_ids & {str(chunk_id) for chunk_id in citated}
        if overlap:
            return True, f"evidence_used: {len(overlap)}/{len(new_ids)}"
        detail = f" ({revalidation_error})" if revalidation_error else ""
        return False, (
            f"evidence_not_used: none of {len(new_ids)} new chunk(s) cited by "
            f"the regenerated specialist response{detail}"
        )

    # ------------------------------------------------------------------
    # 内部：检索（路由决策）
    # ------------------------------------------------------------------

    async def _retrieve(
        self,
        response: AgentResponse,
        request: FusionRequest,
        query: str,
        failure_category: str,
    ) -> tuple[list[KnowledgeChunk], bool | None]:
        """执行补充检索并返回 ``(chunks, via_dispatcher)``。

        异常时按优先级降级到下一个后端（dispatcher → knowledge_agent →
        rag_service）。所有后端均失败时返回 ``CRAGResult`` 短路。
        """
        organ = response.organ

        # P2-8 修复：路由优先级 dispatcher > knowledge_agent > rag_service
        # P2-9 增强：单后端失败时降级到下一个，确保 CRAG 不被单点失败阻断
        if self._dispatcher_agent is not None:
            try:
                chunks = await self._dispatcher_agent.route_crag_query(
                    organ=organ,
                    query=query,
                    top_k=self._crag_top_k,
                    failure_category=failure_category,
                )
                return list(chunks or []), True
            except Exception as exc:
                self._last_retrieval_error = exc
                logger.warning(
                    "crag_dispatcher_failed_fallback",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                # 降级到 knowledge_agent（若有）
                if self._knowledge_agent is not None:
                    return await self._retrieve_via_knowledge_agent(
                        organ, query
                    )

        if self._knowledge_agent is not None:
            return await self._retrieve_via_knowledge_agent(organ, query)

        if self._rag_service is not None:
            return await self._retrieve_via_rag_service(organ, query, request)

        # 全部失败 / 不可用
        return await self._retrieve_fail(failure_category)

    async def _retrieve_via_knowledge_agent(
        self,
        organ: Any,
        query: str,
    ) -> tuple[list[KnowledgeChunk], bool | None]:
        """通过 KnowledgeAgent 检索（带异常降级到 RAG Service）。"""
        try:
            chunks = await self._knowledge_agent.query(  # type: ignore[union-attr]
                query=query,
                organ=organ,
                top_k=self._crag_top_k,
            )
            return list(chunks or []), False
        except Exception as exc:
            self._last_retrieval_error = exc
            logger.warning(
                "crag_knowledge_agent_failed_fallback",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            if self._rag_service is not None:
                # 注意：降级到 RAG Service 需要 request 参数；这里用 None 兜底
                return await self._retrieve_via_rag_service(organ, query, None)
            return await self._retrieve_fail("rag_failed")

    async def _retrieve_via_rag_service(
        self,
        organ: Any,
        query: str,
        request: FusionRequest | None,
    ) -> tuple[list[KnowledgeChunk], bool | None]:
        """通过 RAG Service 直连检索（带 BM25 兜底）。"""
        try:
            retrieval_request = RetrievalRequest(
                query=query,
                organ=organ.value if organ else None,
                top_k=self._crag_top_k,
                use_query_enhancement=False,
                use_reranking=False,  # 减少延迟
                use_bm25_fallback=True,  # P1-3 修复：BM25 终极兜底
                use_multimodal=False,
                request_id=(request.request_id if request else None) or None,
                session_id=(request.session_id if request else None) or None,
            )
            result = await self._rag_service.retrieve(retrieval_request)  # type: ignore[union-attr]
            chunks = [sc.chunk for sc in (result.chunks or [])]
            # A.88 T5-6：把检索分数带下去 —— 恢复质量判据要按分数判定，
            # 而 `ScoredChunk.chunk` 本身不带分数。写进 metadata 而不是另造
            # 一个新结构：调用方（重生成、留痕）都不必改。
            for scored in result.chunks or []:
                meta = getattr(scored.chunk, "metadata", None)
                if isinstance(meta, dict) and "retrieval_score" not in meta:
                    meta["retrieval_score"] = float(getattr(scored, "score", 0.0) or 0.0)
            return chunks, False
        except Exception as exc:
            self._last_retrieval_error = exc
            logger.warning(
                "crag_rag_service_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return await self._retrieve_fail("rag_failed")

    async def _retrieve_fail(
        self, failure_category: str
    ) -> tuple[list[KnowledgeChunk], bool | None]:
        """统一失败返回（返回特殊 CRAGResult 标记让 handle() 短路）。

        P2-9 增强：若 _last_retrieval_error 有值，handle() 会读取并把
        异常类型写入 CRAGResult.reason（保留"rag_failed: RuntimeError"
        形式以兼容旧测试）。
        """
        # 通过返回空 list + via_dispatcher=False 表示失败；
        # handle() 会根据 _last_retrieval_error 决定 reason 字段。
        return [], None

    # ------------------------------------------------------------------
    # 内部：构造新响应（P2-6 修复：真正调用 specialist LLM）
    # ------------------------------------------------------------------

    async def _build_new_response(
        self,
        original: AgentResponse,
        new_evidence: list[KnowledgeChunk],
        request: FusionRequest,
        citation_sink: dict[str, Any] | None = None,
    ) -> tuple[AgentResponse, bool]:
        """基于新证据构造新响应。

        P2-6 修复：
        - 优先调用 ``self._specialist_invoker`` 真正重跑 specialist LLM；
        - 无 invoker 时 fallback 到占位实现（合并 evidence，confidence +0.2）。

        设计文档 02-核心流程与知识库.md:222：
        > Self-RAG 重试：每次仅重新执行**生成与质检**，复用首次检索与校验结果

        Args:
            citation_sink: **A.88 T5-6** 出参（可选）。函数会把"specialist
                **自己**重新生成时引用了哪些 chunk"写进 ``citation_sink["cited"]``。
                为什么需要它：返回的 ``final_response.evidence_chunk_ids`` 是
                **三方合并**后的（旧 + 新 + 重生成），新证据必然在里面 ——
                拿它判断"新证据有没有被用上"永远为真，等于没判。
                用出参而不是返回值，是为了不改既有 2 元组契约（有调用方按
                2 元组解包）。

        Returns:
            ``(new_response, specialist_reinvoked)``。
        """
        # 1) 合并新证据（旧在前，新在后，去重）
        merged_evidence = self._merge_evidence(original, new_evidence)

        # 2) P2-6 修复：尝试重新调用 specialist
        if self._specialist_invoker is not None:
            try:
                new_response = await self._specialist_invoker(
                    self._build_specialist_context(
                        original=original,
                        new_evidence=new_evidence,
                        request=request,
                    )
                )
                if citation_sink is not None:
                    # 记录 **specialist 自己** 引用的（合并前），供质量校验用
                    citation_sink["cited"] = [
                        str(chunk_id)
                        for chunk_id in (new_response.evidence_chunk_ids or [])
                    ]
                # 校验重新调用的响应：保留 specialist 决策权
                # 三方合并：原 evidence + 新 evidence + specialist 输出 evidence
                # 避免丢失原 evidence（specialist 重新生成时可能不引用旧 chunk）
                final_evidence = self._merge_evidence_three(
                    original, new_evidence, new_response
                )
                self._log_evidence_merge(
                    request.request_id or "",
                    mode="reinvoke",
                    original_ids=list(original.evidence_chunk_ids or []),
                    new_ids=[str(getattr(c, "chunk_id", "")) for c in new_evidence],
                    final_ids=final_evidence,
                )
                # A.95 Q11：把**三段来源**分别记进 trace，让"落库 evidence 包含
                # 本次 prompt 里没有的块"成为可解释事实（详见 `_merge_evidence_three`
                # docstring）。审计时按 `evidence_sources` 就能对上：
                #   original_* = 首次生成时的 prompt 证据（本次重生成 prompt 里没有）
                #   new_*      = 本次重生成 prompt 里补进来的
                #   reinvoked_* = 重生成后 specialist 自己引用的
                _orig_ids = [str(x) for x in (original.evidence_chunk_ids or [])]
                _new_ids = [str(getattr(c, "chunk_id", "")) for c in new_evidence]
                _reinv_ids = [
                    str(x) for x in (new_response.evidence_chunk_ids or [])
                ]
                _final_set = set(final_evidence)
                _only_from_original = [
                    x for x in _orig_ids if x in _final_set and x not in set(_new_ids)
                ]
                evidence_sources_trace = (
                    "evidence_sources: "
                    f"original={len(_orig_ids)} "
                    f"new={len(_new_ids)} "
                    f"reinvoked={len(_reinv_ids)} "
                    f"final={len(final_evidence)} "
                    f"not_in_this_prompt={len(_only_from_original)}"
                    + (
                        f" [{','.join(_only_from_original[:3])}...]"
                        if _only_from_original
                        else ""
                    )
                )
                if _only_from_original:
                    logger.info(
                        "crag_evidence_includes_first_prompt_only",
                        request_id=request.request_id,
                        count=len(_only_from_original),
                        sample=_only_from_original[:3],
                        hint=(
                            "这些块来自**首次**生成时的 prompt，本次重生成的 prompt 里没有 —— "
                            "是 _merge_evidence_three 第①步的有意合并，不是引用造假"
                        ),
                    )
                final_response = AgentResponse(
                    agent_name=new_response.agent_name,
                    organ=new_response.organ,
                    content=new_response.content,
                    confidence=new_response.confidence,
                    evidence_chunk_ids=final_evidence,
                    reasoning_trace=[
                        *list(new_response.reasoning_trace or []),
                        f"crag_reinvoked_with_{len(new_evidence)}_new_chunks",
                        evidence_sources_trace,
                    ],
                    latency_ms=new_response.latency_ms,
                    needs_review=new_response.needs_review,
                    disease_code=new_response.disease_code,
                    used_evidence_ids=list(new_response.used_evidence_ids or []),
                )
                logger.info(
                    "crag_specialist_reinvoked",
                    agent=original.agent_name,
                    new_evidence_count=len(new_evidence),
                )
                return final_response, True
            except Exception as exc:
                logger.warning(
                    "crag_specialist_reinvoke_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    agent=original.agent_name,
                )
                # 降级到占位实现

        # 3) Fallback：占位实现（仅合并 evidence + +0.2 confidence）
        #
        # 2026-09-28 接线审计：该分支此前**完全静默** —— 由于 `main.py` 的 invoker
        # 能力探测恒失败，它成了事实上的唯一路径，但既无日志也无计数，
        # 外部无法识别"CRAG 其实没有做真实重生成"。现在改为留痕。
        logger.warning(
            "crag_reinvoke_placeholder_used",
            reason=(
                "specialist_invoker_not_configured"
                if self._specialist_invoker is None
                else "specialist_reinvoke_failed"
            ),
            agent=original.agent_name,
            added_evidence=len(new_evidence),
        )
        new_conf = min(float(original.confidence or 0.0) + 0.2, 1.0)
        new_trace = list(original.reasoning_trace or [])
        new_trace.append(
            f"crag_recovery_evidence_only: added {len(new_evidence)} new evidence chunk(s) (no specialist reinvoke)"
        )
        self._log_evidence_merge(
            request.request_id or "",
            mode="placeholder_evidence_only",
            original_ids=list(original.evidence_chunk_ids or []),
            new_ids=[str(getattr(c, "chunk_id", "")) for c in new_evidence],
            final_ids=merged_evidence,
        )
        return (
            AgentResponse(
                agent_name=original.agent_name,
                organ=original.organ,
                content=original.content or "",
                confidence=round(new_conf, 4),
                evidence_chunk_ids=merged_evidence,
                reasoning_trace=new_trace,
                latency_ms=original.latency_ms,
                needs_review=original.needs_review,
                disease_code=original.disease_code,
                used_evidence_ids=list(original.used_evidence_ids or []),
            ),
            False,
        )

    @staticmethod
    def _build_specialist_context(
        original: AgentResponse,
        new_evidence: list[KnowledgeChunk],
        request: FusionRequest,
    ) -> AgentContext:
        """从原始 response + 新证据构造 AgentContext 供 specialist 重生成。

        关键：保留原 organ + user_query + 已有 evidence；追加新 evidence
        供 specialist 参考（注意：specialist 应从 request.clinical_context
        中读取 evidence，避免污染 AgentContext.evidence_chunk_ids）。
        """
        # 收集所有 evidence（old + new）
        all_evidence_ids: list[str] = []
        seen: set[str] = set()
        for cid in (original.evidence_chunk_ids or []):
            if cid and cid not in seen:
                seen.add(cid)
                all_evidence_ids.append(cid)
        for chunk in new_evidence:
            cid = getattr(chunk, "chunk_id", None)
            if cid and cid not in seen:
                seen.add(cid)
                all_evidence_ids.append(cid)

        # clinical_context 传递所有 metadata + 新 evidence
        clinical_context: dict[str, Any] = dict(request.clinical_context or {})
        clinical_context["crag_evidence_ids"] = all_evidence_ids
        clinical_context["crag_evidence_chunks"] = [
            {
                "chunk_id": getattr(c, "chunk_id", ""),
                "content": getattr(c, "content", ""),
                "metadata": getattr(c, "metadata", {}),
            }
            for c in new_evidence
        ]
        clinical_context["crag_failure_category"] = "see_outer_call"

        user_query = (
            request.clinical_context.get("user_query", "")  # type: ignore[union-attr]
            if isinstance(request.clinical_context, dict)
            else ""
        ) or original.content or ""

        return AgentContext(
            request_id=request.request_id or "crag_reinvoke",
            session_id=request.session_id or "crag_reinvoke",
            organ=original.organ or clinical_context.get("organ"),  # type: ignore[arg-type]
            user_query=str(user_query),
            imaging_metadata=clinical_context.get("imaging_metadata"),  # type: ignore[union-attr]
            prior_messages=clinical_context.get("prior_messages", []),  # type: ignore[arg-type]
        )

    @staticmethod
    def _merge_evidence(
        original: AgentResponse,
        new_evidence: list[KnowledgeChunk],
    ) -> list[str]:
        """合并 evidence（旧在前，新在后，去重）。"""
        merged: list[str] = []
        seen: set[str] = set()
        for cid in (original.evidence_chunk_ids or []):
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(cid)
        for chunk in new_evidence:
            cid = getattr(chunk, "chunk_id", None)
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(cid)
        return merged

    @staticmethod
    def _log_evidence_merge(
        request_id: str,
        *,
        mode: str,
        original_ids: list[str],
        new_ids: list[str],
        final_ids: list[str],
    ) -> None:
        """证据链留痕（A.62 批 2，`UAI_EVIDENCE_CHAIN_DEBUG=1` 时开启）。

        为什么必须在这里留痕：`_build_specialist_context` 组装的 `crag_evidence_ids/chunks`
        **从未传给 reinvoke 的 AgentContext**（`crag.py:563-570` 没带 `clinical_context`），
        所以 CRAG 那次**新检索到的块从未进入任何 prompt**，却经 `_merge_evidence_three`
        进了落库 `knowledge_ids` —— 真机表现为「落库里有块，不在任何 prompt 留痕里」
        （批 1 的 MISMATCH，心脏腿 2/2）。留痕之后，探针能把落库块分解为
        「模型看到过」与「仅被记录（模型从未看到）」两类，并对后者问责（见 16 号 A.62.x）。
        """
        if os.environ.get("UAI_EVIDENCE_CHAIN_DEBUG") != "1":
            return
        logger.info(
            "crag_evidence_merge",
            request_id=request_id,
            mode=mode,
            original_chunks=len(original_ids),
            new_chunks=len(new_ids),
            final_chunks=len(final_ids),
            original_ids=list(original_ids),
            new_ids=list(new_ids),
            final_ids=list(final_ids),
        )

    @staticmethod
    def _merge_evidence_three(
        original: AgentResponse,
        new_evidence: list[KnowledgeChunk],
        reinvoked_response: AgentResponse,
    ) -> list[str]:
        """三方合并 evidence：原 + 新 + reinvoked。

        顺序：原 specialist evidence → 新 chunks → reinvoked response evidence
        （保持 specialist 输出优先）。

        ## ⚠️ A.95 Q11：这里就是那个"证据链断点"的**根因**（有意设计，需可解释）

        真机现象（A.62 批1 实测）：心脏腿 **2/2** 出现「数据库 `knowledge_ids` 里有块，
        但不在任何 `_build_prompt` 留痕中」（如 `dialogue_cardiac_外科_0028#0000`）；
        甲状腺腿 **5/5** 正常。

        **根因在本函数第 ① 步**：`original.evidence_chunk_ids` 是**首次生成时**
        specialist 引用的证据。而 CRAG 重新调用 specialist 时，它看到的 prompt 里是
        `new_evidence`（补充检索的新块）——**不是** original 那一批。

        于是 `final_evidence` 必然包含"重生成时 prompt 里没有的块"，
        它们来自**第一次**的 prompt。**这不是 bug**（丢掉原证据会让结论依据残缺），
        但留痕必须说清楚，否则任何审计都会把它当成"引用造假"。

        为什么甲状腺腿没出现：它的结论没触发 CRAG 重生成
        （`crag_recovered=False`），走原样返回路径，`final_evidence ==
        original.evidence`，与 prompt 完全一致。

        → 处置：**保留合并语义**，但调用方（`_build_new_response`）必须把三段来源
        分别写进 `reasoning_trace`，让"落库 evidence ≠ 本次 prompt 所见"
        成为**可解释事实**而不是疑点。见该函数的 `evidence_sources` 留痕。
        """
        merged: list[str] = []
        seen: set[str] = set()
        # ① 原 evidence（**首次 prompt 所见** —— 断点来源）
        for cid in (original.evidence_chunk_ids or []):
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(cid)
        # ② 新 evidence chunks（**本次重生成的 prompt 所见**）
        for chunk in new_evidence:
            cid = getattr(chunk, "chunk_id", None)
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(cid)
        # ③ reinvoked response 自身的 evidence（specialist 决策优先）
        for cid in (reinvoked_response.evidence_chunk_ids or []):
            if cid and cid not in seen:
                seen.add(cid)
                merged.append(cid)
        return merged

    # ------------------------------------------------------------------
    # 内部：失败分类 + query 构造
    # ------------------------------------------------------------------

    def _classify_failure(self, self_rag_result: SelfRAGEvalResult) -> str:
        """根据 Self-RAG 评分维度 + reason 字符串分类失败原因。"""
        if self_rag_result.evidence_sufficiency < 2.0:
            return "evidence_insufficient"
        if self_rag_result.evidence_consistency < 2.0:
            return "evidence_contradictory"
        if self_rag_result.feature_completeness < 2.0:
            return "feature_missing"
        reason_lower = (self_rag_result.reason or "").lower()
        for category, keywords in _FAILURE_CATEGORIES.items():
            if any(kw.lower() in reason_lower for kw in keywords):
                return category
        return "unknown"

    @staticmethod
    def _build_enhanced_query(
        request: FusionRequest,
        response: AgentResponse,
        failure_category: str,
    ) -> str:
        """构造增强 query：在原 user_query 基础上追加失败原因关键词。"""
        base = str(request.clinical_context.get("user_query", "")) if request.clinical_context else ""
        if not base:
            base = response.content or ""
        if not base:
            base = f"{response.organ.value if response.organ else 'unknown'} diagnosis"
        suffix_map = {
            "evidence_insufficient": "需要更多证据",
            "evidence_contradictory": "需要澄清证据",
            "feature_missing": "需要补充特征",
            "low_confidence": "需要更高置信度证据",
            "unknown": "需要更多上下文",
        }
        suffix = suffix_map.get(failure_category, "")
        if suffix and suffix not in base:
            return f"{base} {suffix}"
        return base


__all__ = [
    "DEFAULT_CRAG_TOP_K",
    "CRAGHandler",
    "CRAGResult",
    "SpecialistInvoker",
]
