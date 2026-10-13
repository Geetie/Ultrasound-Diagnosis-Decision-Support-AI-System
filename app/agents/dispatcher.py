"""T-GA-AGT-02 — 中央调度智能体实现。

``DispatcherAgentImpl`` 提供三项能力：

1. ``route()``：基于 ``DiagnosisRequest.target_organ`` 的简单路由。
   Phase 0A 直接透传 ``target_organ`` 字段；Phase 0B+ 可加入基于文本意图识别
   的智能路由（在不破坏现有 API 的前提下替换本方法）。
2. ``aggregate()``：将多个 ``AgentResponse`` 融合为单个 ``AgentResponse``。
   Phase 0A 使用「算术平均 confidence + 内容拼接」的简化策略；Phase 0B+ 由
   FUS 轨道替换为基于一致性的加权融合（在不破坏现有 API 的前提下替换本方法）。
3. ``route_crag_query()``：P2-8 修复 — CRAG 补充检索的中央路由入口。
   内部委派给已注入的 ``KnowledgeAgent``（享受两层缓存 + 负载均衡）。
   无 ``knowledge_agent`` 时抛 ``RuntimeError``（由 CRAGHandler fallback
   到 knowledge_agent / rag_service 后端）。

错误降级策略：
- 调用 specialist 抛任何异常 → 视为该 specialist 不可用，response 中
  ``content = "[<organ>] specialist_unavailable"``，``confidence = 0.0``，
  并在 ``reasoning_trace`` 中追加 ``"specialist_unavailable: <organ>"``。
- 全部 specialist 失败 / 输入列表为空 → 返回 ``confidence=0.0`` 的占位
  响应，``content = "[dispatcher] no specialist responded"``。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from app.agents.protocols import (
    Agent,
    AgentContext,
    AgentResponse,
    DispatcherAgent,
    KnowledgeAgent,
)
from app.agents.specialist.readable import (
    pipeline_unavailable_text,
    specialist_unavailable_text,
)
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import DiagnosisRequest, Organ

logger = structlog.get_logger("app.agents.dispatcher")


# 路由表常量：Phase 0A 显式列出 3 个器官，避免隐式默认。
#
# 字段含义：``{Organ: "<class_name>"}``，class_name 解析为：
# - 在 Wave 5+ 之后：``ThyroidSpecialistAgent`` / ``LiverSpecialistAgent`` /
#   ``CardiacSpecialistAgent``（由 :func:`app.agents.specialist.build_dispatcher`
#   注入依赖并实例化）；
# - 在 Wave 1+2 阶段：``PlaceholderSpecialistAgent``（占位实现）。
#
# 注意：本映射仅用于日志/审计/能力发现，**不**用于运行时类型检查
# （运行时通过 ``DispatcherAgentImpl._specialists`` 字典直接索引）。
ORGAN_TO_SPECIALIST: dict[Organ, str] = {
    Organ.THYROID: "thyroid_specialist",
    Organ.LIVER: "liver_specialist",
    Organ.CARDIAC: "cardiac_specialist",
}


class DispatcherAgentImpl(DispatcherAgent, Agent):
    """中央调度器实现。

    同时实现 ``Agent`` 协议（作为 dispatcher 自身也提供 ``invoke()``）和
    ``DispatcherAgent`` 协议（额外暴露 ``route`` / ``aggregate`` /
    ``route_crag_query``）。

    Args:
        specialists: ``{organ: specialist_agent}`` 字典。``organ`` 必须是
            ``Organ`` 枚举，specialist 必须是 ``Agent`` 子类实例。
        knowledge_agent: P2-8 修复 — ``KnowledgeAgent`` 实例，用于
            ``route_crag_query`` 中央路由。``None`` 时 ``route_crag_query``
            抛 ``RuntimeError``（由 CRAGHandler 降级到 knowledge_agent /
            rag_service 后端）。Phase 0B+ 可在此参数外加 KB 版本感知 /
            跨器官协调逻辑。
        name: dispatcher 自身的名称（用于 ``AgentResponse.agent_name``）。
    """

    def __init__(
        self,
        specialists: dict[Organ, Agent],
        knowledge_agent: KnowledgeAgent | None = None,
        name: str = "dispatcher",
    ) -> None:
        if not specialists:
            raise ValueError("specialists dict must not be empty")
        self._specialists: dict[Organ, Agent] = dict(specialists)
        # P2-8 修复：保存 knowledge_agent 引用，供 route_crag_query 使用
        self._knowledge_agent: KnowledgeAgent | None = knowledge_agent
        self._name = name

    # ------------------------------------------------------------------
    # 内部：单 specialist 调用的容错封装（消除 invoke / dispatch_all 重复）
    # ------------------------------------------------------------------

    async def _call_one_safe(
        self,
        organ: Organ,
        context: AgentContext,
        include_route_in_trace: bool = True,
    ) -> AgentResponse:
        """对单个 specialist 做容错调用，失败时返回 ``confidence=0.0`` 的占位响应。

        这是 ``invoke()`` 和 ``dispatch_all()`` 共享的核心降级逻辑：
        - specialist 缺失 → 返回 ``specialist_unavailable`` 占位响应；
        - specialist 抛任何异常 → 捕获后返回 ``specialist_unavailable`` 占位响应；
        - 正常调用 → 返回 specialist 的原始响应。

        Args:
            organ: 目标器官。
            context: 调用上下文。
            include_route_in_trace: 是否在 ``reasoning_trace`` 前面追加
                ``"route -> {organ}"`` 行（``invoke()`` 需要；``dispatch_all``
                的并行调用不需要重复写入）。
        """
        specialist = self._specialists.get(organ)
        if specialist is None:
            logger.warning(
                "dispatcher_no_specialist_for_organ",
                organ=organ.value,
            )
            return AgentResponse(
                agent_name=f"unknown_{organ.value}",
                organ=organ,
                # A.59 可读化 + 转人工：原先 `[thyroid] specialist_unavailable` 直接当正文
                content=specialist_unavailable_text(organ),
                confidence=0.0,
                needs_review=True,
                reasoning_trace=self._make_degraded_trace(
                    organ, "no registered specialist", include_route_in_trace
                ),
            )

        try:
            return await specialist.invoke(context)
        except Exception as exc:
            logger.warning(
                "dispatcher_specialist_failed",
                organ=organ.value,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return AgentResponse(
                agent_name=f"failed_{specialist.__class__.__name__}",
                organ=organ,
                # A.59 可读化 + 转人工（同上）
                content=specialist_unavailable_text(organ),
                confidence=0.0,
                needs_review=True,
                reasoning_trace=self._make_degraded_trace(
                    organ, type(exc).__name__, include_route_in_trace
                ),
            )

    @staticmethod
    def _make_degraded_trace(
        organ: Organ,
        reason: str,
        include_route: bool,
    ) -> list[str]:
        """构造降级响应的 ``reasoning_trace``。"""
        if include_route:
            return [
                f"route -> {organ.value}",
                f"specialist_unavailable: {reason}",
            ]
        return [f"specialist_unavailable: {reason}"]

    # ------------------------------------------------------------------
    # Agent protocol
    # ------------------------------------------------------------------

    async def invoke(self, context: AgentContext) -> AgentResponse:
        """作为 Agent 协议的入口：直接转发给对应 specialist 并聚合。

        行为：
        1. 用 ``context.organ`` 找出对应 specialist；
        2. 调用 specialist（异常时降级为 confidence=0.0 的占位响应）；
        3. 聚合单条响应（与多 specialist 场景共用同一条聚合逻辑）。
        """
        start = time.monotonic()
        response = await self._call_one_safe(context.organ, context)
        responses: list[AgentResponse] = [response]
        aggregated = await self.aggregate(responses)
        aggregated.latency_ms = (time.monotonic() - start) * 1000.0
        return aggregated

    def get_capability_tags(self) -> list[str]:
        return ["dispatcher", "routing", "aggregation"]

    async def health_check(self) -> bool:
        """所有已注册的 specialist 都健康 → dispatcher 健康。"""
        for organ, specialist in self._specialists.items():
            try:
                ok = await specialist.health_check()
            except Exception as exc:
                logger.warning(
                    "dispatcher_specialist_health_error",
                    organ=organ.value,
                    error=str(exc),
                )
                return False
            if not ok:
                return False
        return True

    async def close(self) -> None:
        """释放 dispatcher 资源（调用各 specialist.close() 并清空 specialists 字典）。"""
        for organ, specialist in list(self._specialists.items()):
            try:
                await specialist.close()
            except Exception as exc:
                logger.warning(
                    "dispatcher_specialist_close_error",
                    organ=organ.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
        self._specialists.clear()
        logger.info("dispatcher_closed", name=self._name)

    # ------------------------------------------------------------------
    # DispatcherAgent protocol
    # ------------------------------------------------------------------

    async def route(self, request: DiagnosisRequest) -> Organ:
        """Phase 0A：直接透传 ``request.target_organ``。

        保留方法签名便于 Phase 0B+ 在不破坏调用方的前提下替换为基于意图识别的
        智能路由。
        """
        return request.target_organ

    async def aggregate(self, specialist_responses: list[AgentResponse]) -> AgentResponse:
        """融合多条 specialist 响应。

        规则：
        - **空输入**：返回 ``confidence=0.0`` 的占位响应；
        - **非空输入**：``content`` 用 ``"\\n\\n"`` 拼接各 specialist 的内容；
          ``confidence`` 取算术平均（保留 4 位小数）；``evidence_chunk_ids``
          合并去重；``reasoning_trace`` 汇总各 specialist 的 trace 并加一行
          ``aggregated N specialist(s)``。
        """
        if not specialist_responses:
            return AgentResponse(
                agent_name=self._name,
                content=pipeline_unavailable_text(),
                confidence=0.0,
                needs_review=True,
                reasoning_trace=["aggregated 0 specialist(s)"],
            )

        contents: list[str] = []
        confidences: list[float] = []
        evidence_ids: list[str] = []
        used_evidence_ids: list[str] = []
        trace: list[str] = []
        seen_evidence: set[str] = set()
        seen_used: set[str] = set()

        for resp in specialist_responses:
            contents.append(resp.content)
            confidences.append(resp.confidence)
            for cid in resp.evidence_chunk_ids:
                if cid not in seen_evidence:
                    seen_evidence.add(cid)
                    evidence_ids.append(cid)
            # A.59：`used_evidence_ids` 同样要聚合（KB safety_critical 精确撤销依据）
            for cid in resp.used_evidence_ids:
                if cid not in seen_used:
                    seen_used.add(cid)
                    used_evidence_ids.append(cid)
            trace.extend(resp.reasoning_trace)

        avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        trace.append(f"aggregated {len(specialist_responses)} specialist(s)")

        # A.59（零静默）：**任何** specialist 要求人工复核 → 聚合结果必须保留该标记。
        # 原实现重建 AgentResponse 时漏掉 `needs_review`，于是「经 dispatcher.invoke()
        # 走的单器官链路」会把专科的转人工标记**静默丢掉**（`aggregate()` 是
        # `invoke()` 的唯一出口）。判据取「或」——复核标记只增不减。
        needs_review = any(r.needs_review for r in specialist_responses)
        if needs_review:
            trace.append("aggregated_needs_review: any specialist flagged")

        return AgentResponse(
            agent_name=self._name,
            organ=specialist_responses[0].organ,
            content="\n\n".join(contents),
            confidence=avg_confidence,
            evidence_chunk_ids=evidence_ids,
            used_evidence_ids=used_evidence_ids,
            needs_review=needs_review,
            reasoning_trace=trace,
        )

    # ------------------------------------------------------------------
    # P2-8 修复：CRAG 中央调度路由
    # ------------------------------------------------------------------

    async def route_crag_query(
        self,
        organ: Organ,
        query: str,
        top_k: int = 20,
        failure_category: str = "unknown",
    ) -> list[KnowledgeChunk]:
        """CRAG 补充检索的中央路由（P2-8 修复实现）。

        链路：``Self-RAG 失败 → Dispatcher.route_crag_query → KnowledgeAgent
        → RAG → 返回 chunks``，享受 ``KnowledgeAgent`` 的两层缓存
        （Redis TTL 86400s + LRU ≤50）+ 负载均衡 + KB 版本感知。

        Args:
            organ: 目标器官。
            query: 增强后的查询文本（含失败原因关键词）。
            top_k: 返回的 chunk 上限。
            failure_category: Self-RAG 失败原因分类
                （``evidence_insufficient`` / ``evidence_contradictory`` /
                ``feature_missing`` / ``low_confidence`` / ``unknown``）。

        Returns:
            :class:`KnowledgeChunk` 列表，按相关性降序。

        Raises:
            RuntimeError: 未配置 ``knowledge_agent`` 时。CRAGHandler 捕获
                此异常并降级到 ``knowledge_agent`` 直连 / ``rag_service``
                后端，保证 CRAG 不被单点失败阻断。
        """
        if self._knowledge_agent is None:
            logger.error(
                "dispatcher_route_crag_query_no_knowledge_agent",
                organ=organ.value if organ else None,
                failure_category=failure_category,
                message="knowledge_agent not configured; CRAGHandler will fallback",
            )
            raise RuntimeError(
                "dispatcher.route_crag_query requires a configured knowledge_agent; "
                "CRAGHandler should fallback to direct knowledge_agent / rag_service."
            )

        logger.info(
            "dispatcher_route_crag_query",
            organ=organ.value if organ else None,
            query_preview=(query or "")[:80],
            top_k=top_k,
            failure_category=failure_category,
        )
        return list(
            await self._knowledge_agent.query(
                query=query,
                organ=organ,
                top_k=top_k,
            )
        )


# ----------------------------------------------------------------------
# 便捷工具：批量并行调用各 specialist
# ----------------------------------------------------------------------


async def dispatch_all(
    dispatcher: DispatcherAgentImpl,
    context: AgentContext,
    organs: list[Organ],
) -> list[AgentResponse]:
    """对 ``organs`` 列表中的每个器官并行调用对应 specialist，并收集响应。

    当任一 specialist 抛异常时，对应响应为 ``confidence=0.0`` 的占位响应
    （同 ``DispatcherAgentImpl.invoke`` 的降级语义）。本函数不调用
    ``aggregate``，由调用方决定是否聚合。

    Args:
        dispatcher: 已实例化的 ``DispatcherAgentImpl``。
        context: 共享调用上下文。
        organs: 需要并行调用的器官列表。

    Returns:
        按 ``organs`` 输入顺序排列的 ``AgentResponse`` 列表。
    """
    if not organs:
        return []

    async def _call_one(organ: Organ) -> AgentResponse:
        # dispatch_all 路径：reasoning_trace 不需要重复 "route -> xxx"（每行由
        # 调用方在 aggregate 时统一追加 "aggregated N specialist(s)"）
        return await dispatcher._call_one_safe(
            organ, context, include_route_in_trace=False
        )

    return await asyncio.gather(*[_call_one(o) for o in organs])


__all__ = [
    "ORGAN_TO_SPECIALIST",
    "DispatcherAgentImpl",
    "dispatch_all",
]
