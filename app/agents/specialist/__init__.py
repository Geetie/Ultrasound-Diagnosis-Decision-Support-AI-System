"""Specialist agents package (T-GA-AGT-04/05/06).

本目录提供：

- :class:`SpecialistAgent` — 极薄标记类（仅约束 ``organ`` 属性）
- :class:`RAGSpecialistAgent` — 启用 RAG + LLM 的专科智能体抽象基类
  （公共 invoke 流程）
- :class:`PlaceholderSpecialistAgent` — 占位实现（Phase 0A 兼容）
- :class:`ThyroidSpecialistAgent` — 甲状腺专科（T-GA-AGT-04，TI-RADS 分级）
- :class:`LiverSpecialistAgent` — 肝脏专科（T-GA-AGT-05，LI-RADS 分级）
- :class:`CardiacSpecialistAgent` — 心脏专科（T-GA-AGT-06，LVEF 评估）
- :func:`build_dispatcher` — 工厂函数：注入 inference + knowledge 并
  返回 ``DispatcherAgentImpl`` 实例
- :func:`load_organ_thresholds` — **D1 偏离点修复**：从 OrganDiseaseThreshold
  表加载器官级/病种级置信度阈值
- :data:`DEFAULT_ORGAN_THRESHOLDS` — DB 不可用时的默认阈值
"""
from app.agents.specialist.base import (
    DEFAULT_RAG_TOP_K,
    PlaceholderSpecialistAgent,
    RAGSpecialistAgent,
    SpecialistAgent,
    normalize_grading_text,
)
from app.agents.specialist.cardiac import CardiacSpecialistAgent
from app.agents.specialist.factory import (
    DEFAULT_ORGAN_THRESHOLDS,
    build_dispatcher,
    build_dispatcher_with_thresholds,
    load_organ_thresholds,
)
from app.agents.specialist.liver import LiverSpecialistAgent
from app.agents.specialist.thyroid import ThyroidSpecialistAgent

__all__ = [
    # 基类
    "SpecialistAgent",
    "RAGSpecialistAgent",
    "PlaceholderSpecialistAgent",
    "normalize_grading_text",
    "DEFAULT_RAG_TOP_K",
    # 真实专科
    "ThyroidSpecialistAgent",
    "LiverSpecialistAgent",
    "CardiacSpecialistAgent",
    # 工厂（D1 修复）
    "build_dispatcher",
    "build_dispatcher_with_thresholds",
    "load_organ_thresholds",
    "DEFAULT_ORGAN_THRESHOLDS",
]
