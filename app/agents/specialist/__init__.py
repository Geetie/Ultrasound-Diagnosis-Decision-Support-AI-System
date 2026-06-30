"""Specialist agents package (T-GA-AGT-04/05/06).

本目录在 Phase 0A 阶段提供：

- ``SpecialistAgent`` — 抽象基类（T-GA-AGT-04/05/06 的统一父类）
- ``PlaceholderSpecialistAgent`` — 占位实现，用于在真实 specialist 交付前
  保持 dispatcher 可实例化

真实的器官专科智能体（thyroid / liver / cardiac）将在 AGT-Specialists 轨道
的 Wave 3+ 计划中实现。本阶段 dispatcher 启动时使用占位实例注册。
"""
from app.agents.specialist.base import (
    PlaceholderSpecialistAgent,
    SpecialistAgent,
)

__all__ = [
    "SpecialistAgent",
    "PlaceholderSpecialistAgent",
]
