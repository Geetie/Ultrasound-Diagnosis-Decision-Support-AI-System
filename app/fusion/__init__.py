"""FUS 融合层轨道 (T-GA-FUS-01/02).

本包实现 Wave 5+6 阶段交付的融合层：

- :mod:`app.fusion.protocols` — 核心数据模型与 :class:`FusionEngine` 抽象协议
  （ConflictType、ConflictReport、ConsistencyCheckResult、FusionRequest、
  FusionResult）。
- :mod:`app.fusion.conflict` — T-GA-FUS-01 冲突检测：分级冲突、置信度冲突、
  建议冲突、证据冲突四类。
- :mod:`app.fusion.consistency` — T-GA-FUS-01 一致性校验：与既往诊断对比、
  与临床指南对比、跨器官一致性。
- :mod:`app.fusion.layered_validator` — T-GA-FUS-01 三层校验编排：
  冲突检测 → 一致性 → Self-RAG。
- :mod:`app.fusion.self_rag` — T-GA-FUS-02 Self-RAG 自我评估：LLM 自我评估
  证据充分性 / 一致性 / 关键特征遗漏。
- :mod:`app.fusion.crag` — T-GA-FUS-02 CRAG 补充检索：Self-RAG 失败后的
  兜底（重建查询 + 扩大 top_k 重新检索）。
- :mod:`app.fusion.service` — :class:`FusionEngineImpl`：融合层编排器，
  串联所有组件。
- :mod:`app.fusion.desensitization` — **已有**：融合层脱敏（纵深防御）。

设计原则（与 ``app/inference/protocols.py`` / ``app/rag/protocols.py``
/ ``app/agents/protocols.py`` 保持一致）：

- ABC + Pydantic 模式：协议 + 数据模型严格校验；
- 失败安全：任何内部异常 → 标记 ``needs_review=True`` 而不是抛错阻断；
- 与既有 specialist 协议对齐：所有组件消费 ``AgentResponse`` /
  ``AgentContext``，不引入新的核心协议。
"""
from __future__ import annotations

from app.fusion.protocols import (
    ConflictReport,
    ConflictResolution,
    ConflictType,
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionEngine,
    FusionRequest,
    FusionResult,
)

__all__ = [
    "ConflictReport",
    "ConflictResolution",
    "ConflictType",
    "ConsistencyCheckResult",
    "ConsistencySeverity",
    "FusionEngine",
    "FusionRequest",
    "FusionResult",
]
