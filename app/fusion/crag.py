"""T-GA-FUS-02 — CRAG 补充检索模块。

本模块实现 CRAG（Corrective RAG）处理器 (:class:`CRAGHandler`)。

CRAG 流程：

1. Self-RAG 失败后触发；
2. 评估失败原因（``evidence_insufficient`` / ``evidence_contradictory``
   / ``feature_missing`` / ``unknown``）；
3. 重新构造查询：在原 query 基础上追加失败原因关键词；
4. 调用 RAG Service 的 retriever 重新检索（增加 ``top_k`` 到 20，关闭
   rerank / query enhancement 减少延迟）；
5. 用新证据**重新构造** specialist 响应（占位实现：返回带新 evidence 的
   AgentResponse，由编排器决定是否重跑 specialist）；
6. 仍失败 → 标记 needs_review。

设计要点：

- **占位实现**：Phase 0A 不重新调用 specialist（避免 LLM 调用递归 + 延迟
  不可控），仅返回"补充检索后的新证据"作为新响应的 content / evidence。
  真实重跑 specialist 放 Phase 0B+。
- **失败安全**：RAG 异常 → ``recovered=False, reason="rag_failed"``，由
  编排器标记 needs_review。
- **可注入**：RAG Service / InferenceEngine / 失败原因分类器均通过构造
  参数注入。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionRequest
from app.fusion.self_rag import SelfRAGEvalResult
from app.rag.protocols import (
    RAGService,
    RetrievalRequest,
    ScoredChunk,
)

logger = structlog.get_logger("app.fusion.crag")


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
    """

    recovered: bool
    new_response: Optional[AgentResponse] = None
    failure_category: str = "unknown"
    retrieval_top_k: int = 0
    reason: str = ""


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
        rag_service: RAG Service（用于补充检索）。``None`` 时 CRAG 不可用。
        crag_top_k: 补充检索的 top_k（默认 20）。
    """

    def __init__(
        self,
        rag_service: Optional[RAGService] = None,
        crag_top_k: int = DEFAULT_CRAG_TOP_K,
    ) -> None:
        if crag_top_k <= 0:
            raise ValueError("crag_top_k must be positive")
        self._rag_service = rag_service
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
        if self._rag_service is None:
            return CRAGResult(
                recovered=False,
                failure_category="rag_unavailable",
                reason="rag_unavailable: CRAG skipped because RAG service not configured",
            )

        # 1) 分类失败原因
        failure_category = self._classify_failure(self_rag_result)
        # 2) 构造增强 query
        enhanced_query = self._build_enhanced_query(
            request=request,
            response=response,
            failure_category=failure_category,
        )
        # 3) 调用 RAG 补充检索
        organ_value = response.organ.value if response.organ else None
        try:
            retrieval_request = RetrievalRequest(
                query=enhanced_query,
                organ=organ_value,
                top_k=self._crag_top_k,
                use_query_enhancement=False,
                use_reranking=False,  # 减少延迟
                use_bm25_fallback=False,  # 直接走 dense
                use_multimodal=False,
                request_id=request.request_id or None,
                session_id=request.session_id or None,
            )
            result = await self._rag_service.retrieve(retrieval_request)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "crag_retrieval_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return CRAGResult(
                recovered=False,
                failure_category=failure_category,
                retrieval_top_k=self._crag_top_k,
                reason=f"rag_failed: crag_retrieval_{type(exc).__name__}",
            )

        # 4) 评估是否恢复
        new_evidence = [sc.chunk for sc in result.chunks]
        if not new_evidence:
            return CRAGResult(
                recovered=False,
                failure_category=failure_category,
                retrieval_top_k=self._crag_top_k,
                reason="crag_retrieval_returned_empty",
            )

        # 5) 构造新响应（占位实现：合并新证据 + 旧 content + 提升 confidence）
        new_response = self._build_new_response(response, new_evidence, result)
        logger.info(
            "crag_recovered",
            agent=response.agent_name,
            failure_category=failure_category,
            new_evidence_count=len(new_evidence),
        )
        return CRAGResult(
            recovered=True,
            new_response=new_response,
            failure_category=failure_category,
            retrieval_top_k=self._crag_top_k,
            reason=f"crag_recovered_with_{len(new_evidence)}_new_chunks",
        )

    # ------------------------------------------------------------------
    # 内部
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
        """构造增强 query：在原 user_query 基础上追加失败原因关键词。

        注意：FusionRequest 本身不含 user_query（FusionEngineImpl 在外层
        注入；这里仅以 request.clinical_context 兜底）。
        """
        base = str(request.clinical_context.get("user_query", "")) if request.clinical_context else ""
        if not base:
            base = response.content or ""
        if not base:
            base = f"{response.organ.value if response.organ else 'unknown'} diagnosis"
        # 追加失败原因关键词（去重）
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

    @staticmethod
    def _build_new_response(
        original: AgentResponse,
        new_evidence: List[Any],  # List[KnowledgeChunk]
        retrieval_result: Any,
    ) -> AgentResponse:
        """基于新证据构造新响应（占位实现：合并证据 + 旧 content + 提升 confidence）。

        Phase 0A 不重新调用 specialist LLM（避免递归 + 延迟不可控）。
        """
        # 合并 evidence_chunk_ids（旧在前，新在后，去重）
        seen: set[str] = set()
        merged_evidence: List[str] = []
        for cid in (original.evidence_chunk_ids or []):
            if cid not in seen:
                seen.add(cid)
                merged_evidence.append(cid)
        for chunk in new_evidence:
            cid = getattr(chunk, "chunk_id", None)
            if cid and cid not in seen:
                seen.add(cid)
                merged_evidence.append(cid)

        # 提升 confidence：取 (原 conf + 0.2, 1.0) 中的较小者
        # 0.2 是保守加成（CRAG 补充检索补充了证据）
        new_conf = min(float(original.confidence or 0.0) + 0.2, 1.0)

        # 追加新 evidence 内容到 trace
        new_trace = list(original.reasoning_trace or [])
        new_trace.append(
            f"crag_recovery: added {len(new_evidence)} new evidence chunk(s)"
        )

        return AgentResponse(
            agent_name=original.agent_name,
            organ=original.organ,
            content=original.content or "",  # 占位：保留原 content（不重跑 LLM）
            confidence=round(new_conf, 4),
            evidence_chunk_ids=merged_evidence,
            reasoning_trace=new_trace,
            latency_ms=original.latency_ms,
        )


__all__ = [
    "CRAGHandler",
    "CRAGResult",
    "DEFAULT_CRAG_TOP_K",
]
