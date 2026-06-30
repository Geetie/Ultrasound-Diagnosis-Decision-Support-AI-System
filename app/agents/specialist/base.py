"""T-GA-AGT-04/05/06 占位基类与实现（Phase 0A）。

真正的器官专科智能体（thyroid / liver / cardiac）将在 AGT-Specialists 轨道
的 Wave 3+ 中实现。本文件提供：

1. ``SpecialistAgent`` 抽象基类：统一所有专科智能体的接口契约（与
   ``app/agents/protocols.py`` 中的 ``Agent`` 一致 + ``organ`` 属性）。
2. ``PlaceholderSpecialistAgent``：用于在 Phase 0A 阶段占位 —
   当请求被路由到某器官但真实 specialist 尚未实现时，
   dispatcher 调用本占位类并返回 ``specialist_pending`` 占位响应。

这样设计的好处：
- dispatcher 在 lifespan 启动时即可实例化（不依赖 specialist 实现状态）；
- 单元测试可独立验证 dispatcher 的路由 / 聚合逻辑；
- 当真实 specialist 接入时，只需替换占位实例为真实实例，无需改 dispatcher 代码。
"""
from __future__ import annotations

import time
from typing import List

import structlog

from app.agents.protocols import Agent, AgentContext, AgentResponse
from app.models.schemas import Organ

logger = structlog.get_logger("app.agents.specialist.base")


class SpecialistAgent(Agent):
    """器官专科智能体抽象基类。

    真实 specialist（thyroid / liver / cardiac）的统一父类。本类仅做接口约束，
    不强制额外方法；如需器官级别的额外行为，由子类按需扩展。
    """

    organ: Organ  # 子类必须声明


class PlaceholderSpecialistAgent(SpecialistAgent):
    """占位专科智能体（Phase 0A 用）。

    在 AGT-04/05/06 真实实现交付前，所有器官的 specialist 都用本类占位。
    行为：
    - ``invoke()`` 立即返回 ``confidence=0.0`` 的占位响应，
      ``content`` 标记为 ``"specialist_pending_implementation"``；
    - ``health_check()`` 永远返回 ``True``（占位不会失败，方便 dispatcher
      集成测试）。
    """

    organ: Organ

    def __init__(self, organ: Organ) -> None:
        self.organ = organ

    async def invoke(self, context: AgentContext) -> AgentResponse:
        start = time.monotonic()
        logger.info(
            "placeholder_specialist_invoked",
            organ=self.organ.value,
            request_id=context.request_id,
        )
        return AgentResponse(
            agent_name=f"placeholder_{self.organ.value}",
            organ=self.organ,
            content="[specialist_pending_implementation] this organ specialist is not yet wired in Phase 0A",
            confidence=0.0,
            reasoning_trace=[
                f"route -> {self.organ.value}",
                "specialist_pending_implementation",
            ],
            latency_ms=(time.monotonic() - start) * 1000.0,
        )

    def get_capability_tags(self) -> List[str]:
        return [f"{self.organ.value}_placeholder"]

    async def health_check(self) -> bool:
        return True


__all__ = [
    "SpecialistAgent",
    "PlaceholderSpecialistAgent",
]
