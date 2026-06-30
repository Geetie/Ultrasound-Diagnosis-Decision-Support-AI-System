"""FUS 融合层协议定义模块 (T-GA-FUS-01/02).

本模块定义融合层的核心数据模型与抽象协议，是整个 FUS 子系统的基础。

核心组件：

- :class:`ConflictType` — 冲突类型枚举（4 类）。
- :class:`ConflictResolution` — 冲突解决建议枚举。
- :class:`ConflictReport` — 冲突检测报告。
- :class:`ConsistencySeverity` — 一致性严重度枚举。
- :class:`ConsistencyCheckResult` — 一致性校验结果。
- :class:`FusionRequest` — 融合请求。
- :class:`FusionResult` — 融合结果（最终响应 + 中间状态）。
- :class:`FusionEngine` — 融合引擎抽象协议。

设计原则（与 ``app/inference/protocols.py`` / ``app/rag/protocols.py`` /
``app/agents/protocols.py`` 保持一致）：

- ABC 模式支持多后端（Phase 0A 单实现，Phase 0B+ 可替换）；
- Pydantic 严格校验所有请求/响应数据；
- 输入数据完全基于既有 :class:`AgentResponse` / :class:`AgentContext`，
  不引入新的核心协议。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from app.agents.protocols import AgentResponse
from app.models.schemas import Organ


# ----------------------------------------------------------------------
# 冲突类型
# ----------------------------------------------------------------------


class ConflictType(str, Enum):
    """冲突检测报告中的冲突类型。

    - ``GRADING_CONFLICT``: 同器官不同专科给出不同分级（如 TR3 vs TR4）。
    - ``CONFIDENCE_CONFLICT``: 多个响应 confidence 差距 > 0.4。
    - ``RECOMMENDATION_CONFLICT``: 后续处置建议互相矛盾（活检 vs 随访）。
    - ``EVIDENCE_CONFLICT``: 引用的知识库条目互相矛盾。
    """

    GRADING_CONFLICT = "grading_conflict"
    CONFIDENCE_CONFLICT = "confidence_conflict"
    RECOMMENDATION_CONFLICT = "recommendation_conflict"
    EVIDENCE_CONFLICT = "evidence_conflict"


class ConflictResolution(str, Enum):
    """冲突解决建议（由 ConflictDetector 给出）。

    - ``KEEP_HIGHEST_CONFIDENCE``: 保留最高置信度的响应。
    - ``REQUIRE_HUMAN_REVIEW``: 强制人工复核（冲突无法自动消解）。
    - ``MERGE_EVIDENCE``: 合并多方证据（适用于证据冲突）。
    """

    KEEP_HIGHEST_CONFIDENCE = "keep_highest_confidence"
    REQUIRE_HUMAN_REVIEW = "require_human_review"
    MERGE_EVIDENCE = "merge_evidence"


# ----------------------------------------------------------------------
# 一致性严重度
# ----------------------------------------------------------------------


class ConsistencySeverity(str, Enum):
    """一致性校验严重度。

    - ``INFO``: 提示性偏差（同档内微小差异，可放行）。
    - ``WARNING``: 警告性偏差（建议人工关注，但不放行阻断）。
    - ``CRITICAL``: 严重偏差（强制人工复核）。
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ----------------------------------------------------------------------
# 报告模型
# ----------------------------------------------------------------------


class ConflictReport(BaseModel):
    """冲突检测报告（T-GA-FUS-01）。

    Attributes:
        has_conflict: 是否检出冲突（任一类型即视为有冲突）。
        conflict_types: 检出的所有冲突类型（可能多个）。
        conflicting_responses: 参与冲突的 specialist 响应。
        resolution_suggestion: 冲突解决建议。
        detail: 人类可读的详细说明（按冲突类型展开）。
    """

    has_conflict: bool = False
    conflict_types: List[ConflictType] = Field(default_factory=list)
    conflicting_responses: List[AgentResponse] = Field(default_factory=list)
    resolution_suggestion: ConflictResolution = ConflictResolution.KEEP_HIGHEST_CONFIDENCE
    detail: str = ""

    @field_validator("conflict_types")
    @classmethod
    def _conflict_types_consistent_with_has_conflict(
        cls, v: List[ConflictType], info: Any
    ) -> List[ConflictType]:
        """``conflict_types`` 非空时必须 ``has_conflict=True``，反之亦然。"""
        # Pydantic v1/v2 兼容：info.data 在 v2 是 dict，v1 是 ValuesView
        has_conflict = getattr(info, "data", {}).get("has_conflict", False)
        if v and not has_conflict:
            raise ValueError(
                "has_conflict must be True when conflict_types is non-empty"
            )
        return v


class ConsistencyCheckResult(BaseModel):
    """一致性校验结果（T-GA-FUS-01）。

    Attributes:
        is_consistent: 是否一致（``severity != CRITICAL`` 时为 True）。
        severity: 严重度（``info`` / ``warning`` / ``critical``）。
        inconsistent_organ_pairs: 不一致的器官对（用于跨器官冲突定位）。
        compared_with_prior: 是否与既往诊断做了对比。
        compared_with_guidelines: 是否与临床指南做了对比。
        detail: 人类可读详细说明。
    """

    is_consistent: bool = True
    severity: ConsistencySeverity = ConsistencySeverity.INFO
    inconsistent_organ_pairs: List[Tuple[str, str]] = Field(default_factory=list)
    compared_with_prior: bool = False
    compared_with_guidelines: bool = False
    detail: str = ""

    @field_validator("inconsistent_organ_pairs")
    @classmethod
    def _pairs_well_formed(cls, v: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        for pair in v:
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                raise ValueError(
                    f"inconsistent_organ_pairs items must be (organ_a, organ_b), got {pair!r}"
                )
        return v


# ----------------------------------------------------------------------
# 融合请求 / 结果
# ----------------------------------------------------------------------


class FusionRequest(BaseModel):
    """融合请求。

    Attributes:
        agent_responses: 各专科智能体返回的响应列表（Phase 0A 通常 1 条，
            Phase 0B+ 多专科时多条）。
        prior_diagnosis: 既往诊断（用于一致性对比）。``None`` 表示无既往
            诊断可比对。
        clinical_context: 临床上下文（用于一致性校验的额外信号）。
        request_id: 请求唯一标识（用于追溯）。
        session_id: 会话标识。
        use_self_rag: 是否启用 Self-RAG 质检（默认 True，可降级关闭）。
        use_crag: Self-RAG 失败后是否触发 CRAG（默认 True）。
    """

    agent_responses: List[AgentResponse] = Field(default_factory=list)
    prior_diagnosis: Optional[Dict[str, Any]] = None
    clinical_context: Dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""
    session_id: str = ""
    use_self_rag: bool = True
    use_crag: bool = True

    @field_validator("agent_responses")
    @classmethod
    def _responses_optional(cls, v: List[AgentResponse]) -> List[AgentResponse]:
        # 允许空列表：FusionEngineImpl 内部会返回占位响应
        return list(v) if v is not None else []


class FusionResult(BaseModel):
    """融合结果。

    Attributes:
        final_response: 最终选定的 specialist 响应（融合后输出）。
        conflict_report: 冲突检测报告。
        consistency_result: 一致性校验结果。
        self_rag_passed: Self-RAG 质检是否通过。``None`` 表示未启用。
        self_rag_score: Self-RAG 质检分数（0.0 - 5.0）。``None`` 表示未启用。
        crag_triggered: 是否触发了 CRAG 补充检索。
        crag_recovered: CRAG 是否成功恢复（仅在 ``crag_triggered=True`` 时有意义）。
        needs_review: 是否需要人工复核。
        review_reason: 复核原因（``conflict_detected`` / ``consistency_failed``
            / ``self_rag_failed`` / ``no_specialist_responded`` 等）。
        total_latency_ms: 融合层总耗时（不含 Self-RAG LLM 调用）。
    """

    final_response: Optional[AgentResponse] = None
    conflict_report: ConflictReport = Field(default_factory=ConflictReport)
    consistency_result: ConsistencyCheckResult = Field(default_factory=ConsistencyCheckResult)
    self_rag_passed: Optional[bool] = None
    self_rag_score: Optional[float] = None
    crag_triggered: bool = False
    crag_recovered: bool = False
    needs_review: bool = False
    review_reason: Optional[str] = None
    total_latency_ms: float = 0.0

    @field_validator("self_rag_score")
    @classmethod
    def _score_in_range(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and (v < 0.0 or v > 5.0):
            raise ValueError("self_rag_score must be in [0.0, 5.0]")
        return v

    @field_validator("review_reason")
    @classmethod
    def _review_reason_valid(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in _ALLOWED_REVIEW_REASONS:
            # 不抛错 — 兼容未来扩展；只 log warning（由调用方负责）
            return v
        return v


# 已知的 review_reason 取值（用于校验和文档化；扩展时不抛错）
_ALLOWED_REVIEW_REASONS = frozenset(
    {
        "conflict_detected",
        "consistency_failed",
        "self_rag_failed",
        "no_specialist_responded",
        "all_specialists_unavailable",
        "below_confidence_threshold",
    }
)


# ----------------------------------------------------------------------
# 抽象协议
# ----------------------------------------------------------------------


class FusionEngine(ABC):
    """融合引擎抽象协议（T-GA-FUS-01/02）。

    实现类需要：
    1. 协调冲突检测、一致性校验、Self-RAG、CRAG 四大组件；
    2. 输出最终 ``FusionResult``，并按需标记 ``needs_review``；
    3. 失败安全：任何内部异常 → 标记 ``needs_review=True`` 而不是抛错。
    """

    @abstractmethod
    async def fuse(self, request: FusionRequest) -> FusionResult:
        """执行融合。

        Args:
            request: 融合请求（含 specialist 响应 / 既往诊断 / 临床上下文）。

        Returns:
            :class:`FusionResult`：融合结果（最终响应 + 中间状态）。

        Raises:
            协议层不抛错（实现层必须吞掉所有异常并标记 needs_review）。
        """
        raise NotImplementedError

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查（所有子组件 healthy → True）。

        Returns:
            True 当且仅当所有子组件可用。
        """
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        """释放融合层持有的所有资源。

        实现要求：
        - 任何异常都必须吞掉（warning 日志），不能阻塞 lifespan 关闭；
        - 调用后融合引擎不应再被使用（语义上"已关闭"）。
        """
        raise NotImplementedError


__all__ = [
    "ConflictType",
    "ConflictResolution",
    "ConflictReport",
    "ConsistencySeverity",
    "ConsistencyCheckResult",
    "FusionRequest",
    "FusionResult",
    "FusionEngine",
]
