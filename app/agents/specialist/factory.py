"""T-GA-AGT-04/05/06 — Specialist 工厂模块。

提供 :func:`build_dispatcher` 工厂函数：在 lifespan 启动期把
``InferenceEngine`` + ``KnowledgeAgent`` 注入到三个专科智能体（thyroid /
liver / cardiac），并组装为 :class:`DispatcherAgentImpl` 实例。

工厂职责：
1. 实例化三个 RAG-enabled specialist；
2. 注册到 :class:`DispatcherAgentImpl` 的 specialists 字典；
3. 返回可立即用于路由的 dispatcher。

设计要点：
- 工厂失败时**不**抛错：返回 ``None`` 或抛 ``RuntimeError``，由调用方
  （``app/main.py`` lifespan）决定降级策略（fallback 到占位 specialist）；
- 工厂参数显式列出（不引入全局 settings 依赖），便于单元测试。
"""
from __future__ import annotations

from typing import Optional

import structlog

from app.agents.dispatcher import DispatcherAgentImpl
from app.agents.protocols import KnowledgeAgent
from app.agents.specialist.base import (
    PlaceholderSpecialistAgent,
    RAGSpecialistAgent,
)
from app.agents.specialist.cardiac import CardiacSpecialistAgent
from app.agents.specialist.liver import LiverSpecialistAgent
from app.agents.specialist.thyroid import ThyroidSpecialistAgent
from app.inference.protocols import InferenceEngine
from app.models.schemas import Organ

logger = structlog.get_logger("app.agents.specialist.factory")


# ----------------------------------------------------------------------
# 工厂函数
# ----------------------------------------------------------------------


def build_dispatcher(
    inference_engine: Optional[InferenceEngine],
    knowledge_agent: Optional[KnowledgeAgent],
    use_placeholder_on_missing: bool = True,
    rag_top_k: int = 5,
    max_tokens: int = 1024,
) -> DispatcherAgentImpl:
    """构建已注入依赖的中央调度器。

    Args:
        inference_engine: 底层 LLM 推理引擎。``None`` 时根据
            ``use_placeholder_on_missing`` 决定是抛错还是用占位 specialist
            注册。
        knowledge_agent: 知识查询智能体。同上。
        use_placeholder_on_missing: 当 ``inference_engine`` 或
            ``knowledge_agent`` 为 ``None`` 时，是否用占位 specialist
            兜底。``True``（默认）→ 三个器官全部用占位实例，dispatcher
            仍可启动（返回 ``specialist_pending_implementation``）；
            ``False`` → 抛 ``ValueError``。
        rag_top_k: RAG 检索返回 chunk 上限。默认 5。
        max_tokens: LLM 生成上限。默认 1024。

    Returns:
        已注入 specialist 的 :class:`DispatcherAgentImpl` 实例。

    Raises:
        ValueError: 依赖缺失且 ``use_placeholder_on_missing=False``。
    """
    if inference_engine is None or knowledge_agent is None:
        if not use_placeholder_on_missing:
            raise ValueError(
                f"inference_engine and knowledge_agent are required to build a "
                f"dispatcher with real specialists. Got inference_engine="
                f"{inference_engine}, knowledge_agent={knowledge_agent}. "
                f"Either provide both, or set use_placeholder_on_missing=True."
            )
        logger.warning(
            "dispatcher_using_placeholder_specialists",
            has_inference=inference_engine is not None,
            has_knowledge=knowledge_agent is not None,
        )
        return _build_placeholder_dispatcher()

    specialists = {
        Organ.THYROID: ThyroidSpecialistAgent(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            rag_top_k=rag_top_k,
            max_tokens=max_tokens,
        ),
        Organ.LIVER: LiverSpecialistAgent(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            rag_top_k=rag_top_k,
            max_tokens=max_tokens,
        ),
        Organ.CARDIAC: CardiacSpecialistAgent(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            rag_top_k=rag_top_k,
            max_tokens=max_tokens,
        ),
    }
    logger.info(
        "dispatcher_built",
        specialist_classes=[
            type(s).__name__ for s in specialists.values()
        ],
        rag_top_k=rag_top_k,
        max_tokens=max_tokens,
    )
    # P2-8 修复：把 knowledge_agent 注入 dispatcher，供 route_crag_query 中央路由使用
    return DispatcherAgentImpl(specialists=specialists, knowledge_agent=knowledge_agent)


def _build_placeholder_dispatcher() -> DispatcherAgentImpl:
    """构造使用占位 specialist 的 dispatcher（降级用）。"""
    specialists = {
        Organ.THYROID: PlaceholderSpecialistAgent(Organ.THYROID),
        Organ.LIVER: PlaceholderSpecialistAgent(Organ.LIVER),
        Organ.CARDIAC: PlaceholderSpecialistAgent(Organ.CARDIAC),
    }
    return DispatcherAgentImpl(specialists=specialists)


__all__ = [
    "build_dispatcher",
]
