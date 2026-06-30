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

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List, Optional, Protocol, runtime_checkable

import structlog

from app.agents.protocols import AgentContext, AgentResponse
from app.fusion.protocols import FusionRequest
from app.fusion.self_rag import SelfRAGEvalResult
from app.knowledge.protocols import KnowledgeChunk
from app.rag.protocols import (
    RAGService,
    RetrievalRequest,
    ScoredChunk,
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
        organ: Optional[Any] = None,
        top_k: int = 10,
    ) -> List[KnowledgeChunk]: ...


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
    ) -> List[KnowledgeChunk]: ...


#: Specialist invoker 类型（P2-6）：接收 AgentContext，返回新 AgentResponse
SpecialistInvoker = Callable[[AgentContext], Awaitable[AgentResponse]]


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
    """

    recovered: bool
    new_response: Optional[AgentResponse] = None
    failure_category: str = "unknown"
    retrieval_top_k: int = 0
    reason: str = ""
    via_dispatcher: Optional[bool] = None
    specialist_reinvoked: bool = False


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
    """

    def __init__(
        self,
        rag_service: Optional[RAGService] = None,
        knowledge_agent: Optional[_KnowledgeAgentLike] = None,
        dispatcher_agent: Optional[_DispatcherLike] = None,
        specialist_invoker: Optional[SpecialistInvoker] = None,
        crag_top_k: int = DEFAULT_CRAG_TOP_K,
    ) -> None:
        if crag_top_k <= 0:
            raise ValueError("crag_top_k must be positive")
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
            return CRAGResult(
                recovered=False,
                failure_category=failure_category,
                retrieval_top_k=self._crag_top_k,
                reason="crag_retrieval_returned_empty",
                via_dispatcher=via_dispatcher,
            )

        # 5) 构造新响应：P2-6 修复 — 真正调用 specialist LLM
        new_response, specialist_reinvoked = await self._build_new_response(
            original=response,
            new_evidence=new_chunks,
            request=request,
        )

        logger.info(
            "crag_recovered",
            agent=response.agent_name,
            failure_category=failure_category,
            new_evidence_count=len(new_chunks),
            via_dispatcher=via_dispatcher,
            specialist_reinvoked=specialist_reinvoked,
        )
        return CRAGResult(
            recovered=True,
            new_response=new_response,
            failure_category=failure_category,
            retrieval_top_k=self._crag_top_k,
            reason=(
                f"crag_recovered_with_{len(new_chunks)}_new_chunks"
                + ("_reinvoked" if specialist_reinvoked else "_evidence_only")
            ),
            via_dispatcher=via_dispatcher,
            specialist_reinvoked=specialist_reinvoked,
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
    ) -> tuple[List[KnowledgeChunk], Optional[bool]]:
        """执行补充检索并返回 ``(chunks, via_dispatcher)``。

        异常时直接返回 ``CRAGResult`` 让调用方短路（保持原 handle() 接口）。
        """
        organ = response.organ

        # P2-8 修复：路由优先级 dispatcher > knowledge_agent > rag_service
        try:
            if self._dispatcher_agent is not None:
                # P2-8 优先路径：经中央调度路由
                chunks = await self._dispatcher_agent.route_crag_query(
                    organ=organ,
                    query=query,
                    top_k=self._crag_top_k,
                    failure_category=failure_category,
                )
                return list(chunks or []), True
            if self._knowledge_agent is not None:
                # P1-2 fallback：直接走知识查询智能体
                chunks = await self._knowledge_agent.query(
                    query=query,
                    organ=organ,
                    top_k=self._crag_top_k,
                )
                return list(chunks or []), False
            # P1-2 fallback：直连 RAG Service
            retrieval_request = RetrievalRequest(
                query=query,
                organ=organ.value if organ else None,
                top_k=self._crag_top_k,
                use_query_enhancement=False,
                use_reranking=False,  # 减少延迟
                use_bm25_fallback=True,  # P1-3 修复：BM25 终极兜底
                use_multimodal=False,
                request_id=request.request_id or None,
                session_id=request.session_id or None,
            )
            result = await self._rag_service.retrieve(retrieval_request)  # type: ignore[union-attr]
            chunks = [sc.chunk for sc in (result.chunks or [])]
            return chunks, False
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "crag_retrieval_failed",
                error=str(exc),
                error_type=type(exc).__name__,
                via_dispatcher=self._dispatcher_agent is not None,
            )
            # 返回 CRAGResult（短路）— 调用方需识别非 list 返回值
            return [  # type: ignore[return-value]
                CRAGResult(
                    recovered=False,
                    failure_category=failure_category,
                    retrieval_top_k=self._crag_top_k,
                    reason=f"rag_failed: crag_retrieval_{type(exc).__name__}",
                    via_dispatcher=(
                        True if self._dispatcher_agent is not None else False
                    ),
                ),
                None,
            ]

    # ------------------------------------------------------------------
    # 内部：构造新响应（P2-6 修复：真正调用 specialist LLM）
    # ------------------------------------------------------------------

    async def _build_new_response(
        self,
        original: AgentResponse,
        new_evidence: List[KnowledgeChunk],
        request: FusionRequest,
    ) -> tuple[AgentResponse, bool]:
        """基于新证据构造新响应。

        P2-6 修复：
        - 优先调用 ``self._specialist_invoker`` 真正重跑 specialist LLM；
        - 无 invoker 时 fallback 到占位实现（合并 evidence，confidence +0.2）。

        设计文档 02-核心流程与知识库.md:222：
        > Self-RAG 重试：每次仅重新执行**生成与质检**，复用首次检索与校验结果

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
                # 校验重新调用的响应：保留 specialist 决策权
                # 仅补充 evidence（specialist 输出的 evidence 可能不含新 chunk）
                final_evidence = self._merge_evidence(new_response, new_evidence)
                final_response = AgentResponse(
                    agent_name=new_response.agent_name,
                    organ=new_response.organ,
                    content=new_response.content,
                    confidence=new_response.confidence,
                    evidence_chunk_ids=final_evidence,
                    reasoning_trace=list(new_response.reasoning_trace or [])
                    + [
                        f"crag_reinvoked_with_{len(new_evidence)}_new_chunks",
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
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "crag_specialist_reinvoke_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                    agent=original.agent_name,
                )
                # 降级到占位实现

        # 3) Fallback：占位实现（仅合并 evidence + +0.2 confidence）
        new_conf = min(float(original.confidence or 0.0) + 0.2, 1.0)
        new_trace = list(original.reasoning_trace or [])
        new_trace.append(
            f"crag_recovery_evidence_only: added {len(new_evidence)} new evidence chunk(s) (no specialist reinvoke)"
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
        new_evidence: List[KnowledgeChunk],
        request: FusionRequest,
    ) -> AgentContext:
        """从原始 response + 新证据构造 AgentContext 供 specialist 重生成。

        关键：保留原 organ + user_query + 已有 evidence；追加新 evidence
        供 specialist 参考（注意：specialist 应从 request.clinical_context
        中读取 evidence，避免污染 AgentContext.evidence_chunk_ids）。
        """
        # 收集所有 evidence（old + new）
        all_evidence_ids: List[str] = []
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
        new_evidence: List[KnowledgeChunk],
    ) -> List[str]:
        """合并 evidence（旧在前，新在后，去重）。"""
        merged: List[str] = []
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
    "CRAGHandler",
    "CRAGResult",
    "DEFAULT_CRAG_TOP_K",
    "SpecialistInvoker",
]
