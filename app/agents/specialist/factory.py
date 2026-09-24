"""T-GA-AGT-04/05/06 — Specialist 工厂模块。

提供 :func:`build_dispatcher` 工厂函数：在 lifespan 启动期把
``InferenceEngine`` + ``KnowledgeAgent`` + **器官级/病种级置信度阈值**
注入到三个专科智能体（thyroid / liver / cardiac），并组装为
:class:`DispatcherAgentImpl` 实例。

工厂职责：
1. 从 ``OrganDiseaseThreshold`` 表加载器官级/病种级阈值（**D1 偏离点
   修复**，设计 §2.4）；
2. 实例化三个 RAG-enabled specialist（注入阈值）；
3. 注册到 :class:`DispatcherAgentImpl` 的 specialists 字典；
4. 返回可立即用于路由的 dispatcher。

设计要点：
- 工厂失败时**不**抛错：返回 ``None`` 或抛 ``RuntimeError``，由调用方
  （``app/main.py`` lifespan）决定降级策略（fallback 到占位 specialist）；
- 工厂参数显式列出（不引入全局 settings 依赖），便于单元测试；
- **D1 阈值加载失败降级**：DB 不可达时使用默认硬编码阈值
  （thyroid 0.85 / liver 0.88 / cardiac 0.92），并 log warning。
"""
from __future__ import annotations

from typing import Any

import structlog

from app.agents.dispatcher import DispatcherAgentImpl
from app.agents.protocols import KnowledgeAgent
from app.agents.specialist.base import (
    PlaceholderSpecialistAgent,
)
from app.agents.specialist.cardiac import CardiacSpecialistAgent
from app.agents.specialist.liver import LiverSpecialistAgent
from app.agents.specialist.thyroid import ThyroidSpecialistAgent
from app.inference.protocols import InferenceEngine
from app.models.schemas import Organ

logger = structlog.get_logger("app.agents.specialist.factory")


# ----------------------------------------------------------------------
# D1 偏离点修复：器官级/病种级默认阈值（与 01-系统架构设计 §2.4 对齐）
# ----------------------------------------------------------------------

# 一期硬编码默认阈值（DB 不可用时降级使用）。设计 §2.4 表格：
#   - 甲状腺 0.85（乳头状癌 0.88 / 未分化癌 0.95 / 结节性甲状腺肿 0.80）
#   - 肝脏 0.88（肝细胞癌 0.92 / 肝血管瘤 0.85 / 肝硬化 0.88）
#   - 心脏 0.92（急性心衰 0.95 / 慢性心功能不全 0.90 / 瓣膜反流 0.88）
DEFAULT_ORGAN_THRESHOLDS: dict[str, float] = {
    "thyroid": 0.85,
    "liver": 0.88,
    "cardiac": 0.92,
}


# ----------------------------------------------------------------------
# D1: 异步加载 OrganDiseaseThreshold 表
# ----------------------------------------------------------------------


async def load_organ_thresholds() -> dict[
    str, tuple[float, dict[str, float]]
]:
    """从 OrganDiseaseThreshold 表加载器官级/病种级置信度阈值。

    **D1 偏离点修复**（设计 §2.4）：
    - 查询 ``organ_disease_thresholds`` 表所有行；
    - 按 ``organ`` 分组，每个 organ 取 ``disease_type IS NULL`` 的行
      作为器官级阈值，其他作为病种级阈值（``{disease_type: threshold}``）；
    - DB 不可用时降级为 :data:`DEFAULT_ORGAN_THRESHOLDS`，不抛异常。

    Returns:
        ``{organ_value: (organ_threshold, {disease_type: threshold})}``
        字典。``organ_value`` 为 ``Organ.value``（thyroid / liver / cardiac）。
    """
    try:
        # 延迟导入避免 lifespan 启动期循环引用
        from sqlalchemy import select

        from app.config.database import AsyncSessionLocal
        from app.models.database import OrganDiseaseThreshold

        result_map: dict[str, tuple[float, dict[str, float]]] = {}
        async with AsyncSessionLocal() as session:
            stmt = select(OrganDiseaseThreshold)
            rows = (await session.execute(stmt)).scalars().all()
        for row in rows:
            organ = row.organ
            organ_thr, disease_map = result_map.get(organ, (0.0, {}))
            if row.disease_type is None or row.disease_type == "":
                # 器官级（disease_type 为空 = 默认）
                organ_thr = float(row.confidence_threshold)
            else:
                disease_map[str(row.disease_type)] = float(row.confidence_threshold)
            result_map[organ] = (organ_thr, disease_map)
        # DB 为空或 organ 未配置 → 补默认
        for organ, default in DEFAULT_ORGAN_THRESHOLDS.items():
            if organ not in result_map:
                result_map[organ] = (default, {})
        logger.info(
            "organ_thresholds_loaded",
            threshold_count=sum(
                1 + len(d) for _, d in result_map.values()
            ),
            organs=list(result_map.keys()),
        )
        return result_map
    except Exception as exc:
        # DB 不可用 → 降级为默认硬编码阈值（warning 即可，不阻塞启动）
        logger.warning(
            "organ_thresholds_load_failed_using_defaults",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return {
            organ: (threshold, {})
            for organ, threshold in DEFAULT_ORGAN_THRESHOLDS.items()
        }


# ----------------------------------------------------------------------
# 工厂函数
# ----------------------------------------------------------------------


def build_dispatcher(
    inference_engine: InferenceEngine | None,
    knowledge_agent: KnowledgeAgent | None,
    use_placeholder_on_missing: bool = True,
    rag_top_k: int = 5,
    max_tokens: int = 1024,
    organ_thresholds: dict[str, tuple[float, dict[str, float]]] | None = None,
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
        organ_thresholds: 器官级/病种级置信度阈值（**D1 偏离点修复**）。
            格式 ``{organ_value: (organ_threshold, {disease_type: th})}``。
            ``None`` 时使用 :data:`DEFAULT_ORGAN_THRESHOLDS`（推荐做法
            是 lifespan 启动期先调用 :func:`load_organ_thresholds` 再
            传入本参数）。

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

    # D1: 注入器官级/病种级阈值（默认使用硬编码）
    if organ_thresholds is None:
        organ_thresholds = {
            organ: (threshold, {})
            for organ, threshold in DEFAULT_ORGAN_THRESHOLDS.items()
        }

    def _th(organ_value: str) -> tuple[float, dict[str, float]]:
        return organ_thresholds.get(organ_value, (0.0, {}))

    th_thyroid, disease_thyroid = _th("thyroid")
    th_liver, disease_liver = _th("liver")
    th_cardiac, disease_cardiac = _th("cardiac")

    specialists = {
        Organ.THYROID: ThyroidSpecialistAgent(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            rag_top_k=rag_top_k,
            max_tokens=max_tokens,
            organ_threshold=th_thyroid or None,
            disease_thresholds=disease_thyroid or None,
        ),
        Organ.LIVER: LiverSpecialistAgent(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            rag_top_k=rag_top_k,
            max_tokens=max_tokens,
            organ_threshold=th_liver or None,
            disease_thresholds=disease_liver or None,
        ),
        Organ.CARDIAC: CardiacSpecialistAgent(
            inference_engine=inference_engine,
            knowledge_agent=knowledge_agent,
            rag_top_k=rag_top_k,
            max_tokens=max_tokens,
            organ_threshold=th_cardiac or None,
            disease_thresholds=disease_cardiac or None,
        ),
    }
    logger.info(
        "dispatcher_built",
        specialist_classes=[
            type(s).__name__ for s in specialists.values()
        ],
        rag_top_k=rag_top_k,
        max_tokens=max_tokens,
        organ_thresholds={
            organ.value: specialists[organ]._organ_threshold
            for organ in Organ
            if organ in specialists
        },
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


async def build_dispatcher_with_thresholds(
    inference_engine: InferenceEngine | None,
    knowledge_agent: KnowledgeAgent | None,
    **kwargs: Any,
) -> DispatcherAgentImpl:
    """异步便捷方法：先加载阈值再构建 dispatcher（**lifespan 调用入口**）。

    等价于：
    ```python
    organ_thresholds = await load_organ_thresholds()
    return build_dispatcher(
        inference_engine=inference_engine,
        knowledge_agent=knowledge_agent,
        organ_thresholds=organ_thresholds,
        **kwargs,
    )
    ```

    Args:
        inference_engine: 底层 LLM 推理引擎。
        knowledge_agent: 知识查询智能体。
        **kwargs: 透传给 :func:`build_dispatcher`（``use_placeholder_on_missing`` /
            ``rag_top_k`` / ``max_tokens``）。

    Returns:
        已注入 specialist 的 dispatcher。
    """
    organ_thresholds = await load_organ_thresholds()
    return build_dispatcher(
        inference_engine=inference_engine,
        knowledge_agent=knowledge_agent,
        organ_thresholds=organ_thresholds,
        **kwargs,
    )


__all__ = [
    "DEFAULT_ORGAN_THRESHOLDS",
    "build_dispatcher",
    "build_dispatcher_with_thresholds",
    "load_organ_thresholds",
]
