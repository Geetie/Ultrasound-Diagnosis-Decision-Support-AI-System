"""T-GA-AGT-02 — 中央调度智能体实现。

``DispatcherAgentImpl`` 提供两项能力：

1. ``route()``：基于 ``DiagnosisRequest.target_organ`` 的简单路由。
   Phase 0A 直接透传 ``target_organ`` 字段；Phase 0B+ 可加入基于文本意图识别
   的智能路由（在不破坏现有 API 的前提下替换本方法）。
2. ``aggregate()``：将多个 ``AgentResponse`` 融合为单个 ``AgentResponse``。
   Phase 0A 使用「算术平均 confidence + 内容拼接」的简化策略；Phase 0B+ 由
   FUS 轨道替换为基于一致性的加权融合（在不破坏现有 API 的前提下替换本方法）。

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
from typing import Dict, List

import structlog

from app.agents.protocols import (
    Agent,
    AgentContext,
    AgentResponse,
    DispatcherAgent,
)
from app.models.schemas import DiagnosisRequest, Organ

logger = structlog.get_logger("app.agents.dispatcher")


# 路由表常量：Phase 0A 显式列出 3 个器官，避免隐式默认。
ORGAN_TO_SPECIALIST: Dict[Organ, str] = {
    Organ.THYROID: "thyroid",
    Organ.LIVER: "liver",
    Organ.CARDIAC: "cardiac",
}


class DispatcherAgentImpl(DispatcherAgent, Agent):
    """中央调度器实现。

    同时实现 ``Agent`` 协议（作为 dispatcher 自身也提供 ``invoke()``）和
    ``DispatcherAgent`` 协议（额外暴露 ``route`` / ``aggregate``）。

    Args:
        specialists: ``{organ: specialist_agent}`` 字典。``organ`` 必须是
            ``Organ`` 枚举，specialist 必须是 ``Agent`` 子类实例。
        name: dispatcher 自身的名称（用于 ``AgentResponse.agent_name``）。
    """

    def __init__(
        self,
        specialists: Dict[Organ, Agent],
        name: str = "dispatcher",
    ) -> None:
        if not specialists:
            raise ValueError("specialists dict must not be empty")
        self._specialists: Dict[Organ, Agent] = dict(specialists)
        self._name = name

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
        responses: List[AgentResponse] = []
        specialist = self._specialists.get(context.organ)
        if specialist is None:
            logger.warning(
                "dispatcher_no_specialist_for_organ",
                organ=context.organ.value,
            )
            responses.append(
                AgentResponse(
                    agent_name=f"unknown_{context.organ.value}",
                    organ=context.organ,
                    content=f"[{context.organ.value}] specialist_unavailable",
                    confidence=0.0,
                    reasoning_trace=[
                        f"route -> {context.organ.value}",
                        "specialist_unavailable: no registered specialist",
                    ],
                )
            )
        else:
            try:
                resp = await specialist.invoke(context)
                responses.append(resp)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dispatcher_specialist_failed",
                    organ=context.organ.value,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                responses.append(
                    AgentResponse(
                        agent_name=f"failed_{specialist.__class__.__name__}",
                        organ=context.organ,
                        content=f"[{context.organ.value}] specialist_unavailable",
                        confidence=0.0,
                        reasoning_trace=[
                            f"route -> {context.organ.value}",
                            f"specialist_unavailable: {type(exc).__name__}",
                        ],
                    )
                )

        aggregated = await self.aggregate(responses)
        aggregated.latency_ms = (time.monotonic() - start) * 1000.0
        return aggregated

    def get_capability_tags(self) -> List[str]:
        return ["dispatcher", "routing", "aggregation"]

    async def health_check(self) -> bool:
        """所有已注册的 specialist 都健康 → dispatcher 健康。"""
        for organ, specialist in self._specialists.items():
            try:
                ok = await specialist.health_check()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "dispatcher_specialist_health_error",
                    organ=organ.value,
                    error=str(exc),
                )
                return False
            if not ok:
                return False
        return True

    # ------------------------------------------------------------------
    # DispatcherAgent protocol
    # ------------------------------------------------------------------

    async def route(self, request: DiagnosisRequest) -> Organ:
        """Phase 0A：直接透传 ``request.target_organ``。

        保留方法签名便于 Phase 0B+ 在不破坏调用方的前提下替换为基于意图识别的
        智能路由。
        """
        return request.target_organ

    async def aggregate(self, specialist_responses: List[AgentResponse]) -> AgentResponse:
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
                content="[dispatcher] no specialist responded",
                confidence=0.0,
                reasoning_trace=["aggregated 0 specialist(s)"],
            )

        contents: List[str] = []
        confidences: List[float] = []
        evidence_ids: List[str] = []
        trace: List[str] = []
        seen_evidence: set[str] = set()

        for resp in specialist_responses:
            contents.append(resp.content)
            confidences.append(resp.confidence)
            for cid in resp.evidence_chunk_ids:
                if cid not in seen_evidence:
                    seen_evidence.add(cid)
                    evidence_ids.append(cid)
            trace.extend(resp.reasoning_trace)

        avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else 0.0
        trace.append(f"aggregated {len(specialist_responses)} specialist(s)")

        return AgentResponse(
            agent_name=self._name,
            organ=specialist_responses[0].organ,
            content="\n\n".join(contents),
            confidence=avg_confidence,
            evidence_chunk_ids=evidence_ids,
            reasoning_trace=trace,
        )


# ----------------------------------------------------------------------
# 便捷工具：批量并行调用各 specialist
# ----------------------------------------------------------------------


async def dispatch_all(
    dispatcher: DispatcherAgentImpl,
    context: AgentContext,
    organs: List[Organ],
) -> List[AgentResponse]:
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
        specialist = dispatcher._specialists.get(organ)
        if specialist is None:
            return AgentResponse(
                agent_name=f"unknown_{organ.value}",
                organ=organ,
                content=f"[{organ.value}] specialist_unavailable",
                confidence=0.0,
                reasoning_trace=["specialist_unavailable: no registered specialist"],
            )
        try:
            return await specialist.invoke(context)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "dispatch_all_specialist_failed",
                organ=organ.value,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return AgentResponse(
                agent_name=f"failed_{specialist.__class__.__name__}",
                organ=organ,
                content=f"[{organ.value}] specialist_unavailable",
                confidence=0.0,
                reasoning_trace=[f"specialist_unavailable: {type(exc).__name__}"],
            )

    return await asyncio.gather(*[_call_one(o) for o in organs])


__all__ = [
    "DispatcherAgentImpl",
    "ORGAN_TO_SPECIALIST",
    "dispatch_all",
]
