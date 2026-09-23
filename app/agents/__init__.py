"""AGT 智能体轨道 (T-GA-AGT-01/02/03/04/05/06/07).

本包实现多智能体系统（Multi-Agent System）的基础设施层。

核心组件：
- ``protocols``: T-GA-AGT-01 — 智能体抽象接口契约（Agent / DispatcherAgent /
  KnowledgeAgent / MonitorAgent）和共享数据模型（AgentContext / AgentResponse）。
- ``dispatcher``: T-GA-AGT-02 — 中央调度器（按 Organ 路由 + 多 specialist 聚合）。
- ``knowledge``:  T-GA-AGT-03 — 知识查询智能体（封装 RAG Service + 两层缓存）。
- ``monitor``:    T-GA-AGT-07 — 流程监控智能体（异常检测 + 指标埋点）。
- ``specialist``: T-GA-AGT-04/05/06 — 各器官专科智能体
                  （ThyroidSpecialistAgent / LiverSpecialistAgent /
                  CardiacSpecialistAgent），以及抽象基类
                  ``SpecialistAgent`` / ``RAGSpecialistAgent`` /
                  ``PlaceholderSpecialistAgent``。
- ``prompts``:    上述智能体的 system prompt 文档（Markdown）。

设计原则：
- 接口先行、契约优先、单一职责。Agent 子类只暴露一个 ``invoke()`` 入口，
  上层调用方依赖 ``Agent`` Protocol，不感知具体实现。
- 智能体通信在 Phase 0A 使用「直接异步调用」以保证可测试性；Phase 0B+ 可
  按需切换到 Redis Pub/Sub。
- 所有智能体都接受 ``AgentContext``、返回 ``AgentResponse``，便于统一埋点
  和后续引入 ``MonitorAgent`` 串接。
"""
from app.agents.protocols import (
    Agent,
    AgentContext,
    AgentResponse,
    DispatcherAgent,
    KnowledgeAgent,
    MonitorAgent,
)
from app.agents.specialist import build_dispatcher  # noqa: F401  工厂

__all__ = [
    "Agent",
    "AgentContext",
    "AgentResponse",
    "DispatcherAgent",
    "KnowledgeAgent",
    "MonitorAgent",
    "build_dispatcher",
]
