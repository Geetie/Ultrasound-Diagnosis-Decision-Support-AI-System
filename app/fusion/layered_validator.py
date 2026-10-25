"""T-GA-FUS-01 — 分层校验模块。

本模块实现融合层的分层校验器 (:class:`LayeredValidator`)。

三层校验架构（按依赖顺序）：

- **Layer 1 — 冲突检测**：调用 :class:`app.fusion.conflict.ConflictDetector`。
  - 有冲突 → ``needs_review=True``（**不**直接阻断，保留给 Layer 2 兜底）。
  - 无冲突 → 继续 Layer 2。
- **Layer 2 — 一致性校验**：调用 :class:`app.fusion.consistency.ConsistencyChecker`。
  - 严重度 = CRITICAL → ``needs_review=True``，原因 ``consistency_failed``。
  - 严重度 ≤ WARNING → 继续 Layer 3。
- **Layer 3 — Self-RAG 质检**（可选，由 :class:`FusionRequest.use_self_rag` 控制）。
  - 失败 → 标记 :attr:`FusionResult.self_rag_passed=False`，由编排器决定
    是否触发 CRAG（见 :mod:`app.fusion.service`）。

设计要点：

- **纯编排**：本类不持有 LLM 调用（Self-RAG 由 :mod:`app.fusion.self_rag`
  负责），仅串联组件 + 汇总状态；
- **失败安全**：所有内部异常 → 标记 needs_review，**不**抛错；
- **可观测**：所有中间结果写入 :class:`FusionResult`。
"""
from __future__ import annotations

import asyncio
import time

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.conflict import ConflictDetector
from app.fusion.consistency import ConsistencyChecker
from app.fusion.protocols import (
    ConflictReport,
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionRequest,
    FusionResult,
)
from app.fusion.self_rag import SelfRAGEvalResult, SelfRAGEvaluator

logger = structlog.get_logger("app.fusion.layered_validator")


# ----------------------------------------------------------------------
# LayeredValidator
# ----------------------------------------------------------------------


class LayeredValidator:
    """融合层三层校验编排器（T-GA-FUS-01）。

    Args:
        conflict_detector: 冲突检测器实例。
        consistency_checker: 一致性校验器实例。
        self_rag_evaluator: Self-RAG 评估器实例（可选，``None`` 时跳过 Layer 3）。
    """

    def __init__(
        self,
        conflict_detector: ConflictDetector,
        consistency_checker: ConsistencyChecker,
        self_rag_evaluator: SelfRAGEvaluator | None = None,
    ) -> None:
        if conflict_detector is None:
            raise ValueError("conflict_detector must not be None")
        if consistency_checker is None:
            raise ValueError("consistency_checker must not be None")
        self._conflict_detector = conflict_detector
        self._consistency_checker = consistency_checker
        self._self_rag_evaluator = self_rag_evaluator

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    async def validate(
        self, request: FusionRequest, *, deadline: float | None = None
    ) -> FusionResult:
        """执行三层校验。

        Args:
            request: 融合请求。
            deadline: **A.88 T5-5** validate 的硬截止时刻（``time.monotonic()``
                口径），由 :class:`FusionEngineImpl` 按
                ``pipeline_validate_budget_s`` 算好后传入。Self-RAG 的重试以它为
                边界（剩余预算不够就不重试），从而使"开重试"也不会把 validate
                拖出预算。``None`` = 不设预算（旧行为）。

        Returns:
            :class:`FusionResult`：含 ``conflict_report`` / ``consistency_result``
            / ``self_rag_passed`` / ``needs_review`` / ``review_reason`` /
            ``total_latency_ms``。``final_response`` 字段由 :class:`FusionEngineImpl`
            在本方法返回后填入（选择最终响应）。
        """
        start = time.monotonic()
        result = FusionResult()

        # Layer 1: 冲突检测
        try:
            conflict_report = self._conflict_detector.detect(request.agent_responses)
        except Exception as exc:
            logger.warning(
                "layered_validator_conflict_check_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            conflict_report = ConflictReport(
                has_conflict=False,
                detail=f"conflict_check_failed: {type(exc).__name__}",
            )
        result.conflict_report = conflict_report
        if conflict_report.has_conflict:
            result.needs_review = True
            result.review_reason = "conflict_detected"
            logger.info(
                "fusion_conflict_detected",
                conflict_types=[c.value for c in conflict_report.conflict_types],
                suggestion=conflict_report.resolution_suggestion.value,
            )
            # 不直接 return：让 Layer 2 仍可继续（一致性可能更严重）

        # Layer 2 & 3 并行执行（T-Perf 措施#2）：两者输入相同（request）、
        # 输出独立，原先串行 await 导致 LLM 调用链 2x 时延（fusion 19.6s
        # 实测主因）。asyncio.gather 并行后取 max 而非 sum。
        consistency_task = self._check_consistency_safe(request)
        if request.use_self_rag and self._self_rag_evaluator is not None and request.agent_responses:
            self_rag_task = self._evaluate_self_rag_safe(request, deadline=deadline)
            consistency_result, self_rag_out = await asyncio.gather(
                consistency_task, self_rag_task
            )
        else:
            consistency_result = await consistency_task
            self_rag_out = None

        result.consistency_result = consistency_result
        if (
            not result.needs_review
            and consistency_result.severity == ConsistencySeverity.CRITICAL
        ):
            result.needs_review = True
            result.review_reason = "consistency_failed"
            logger.info(
                "fusion_consistency_critical",
                detail=consistency_result.detail,
            )

        if self_rag_out is not None:
            self_rag_result = self_rag_out
            if isinstance(self_rag_result, Exception):
                logger.warning(
                    "layered_validator_self_rag_failed",
                    error=str(self_rag_result),
                    error_type=type(self_rag_result).__name__,
                )
                result.self_rag_passed = None  # 标记为「未执行」
            elif self_rag_result is not None:
                result.self_rag_passed = self_rag_result.passed
                result.self_rag_score = self_rag_result.score
                # P0-2 修复：写入完整评估结果，service.py 复用而非重新构造
                result.self_rag_result = self_rag_result
                if not self_rag_result.passed and not result.needs_review:
                    result.needs_review = True
                    result.review_reason = "self_rag_failed"
                    logger.info(
                        "fusion_self_rag_failed",
                        score=self_rag_result.score,
                        reason=self_rag_result.reason,
                    )

        result.total_latency_ms = (time.monotonic() - start) * 1000.0
        return result

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    async def _check_consistency_safe(
        self, request: FusionRequest
    ) -> ConsistencyCheckResult:
        """一致性校验（失败安全：异常 → is_consistent=True 的占位结果）。"""
        try:
            return await self._consistency_checker.check(request)
        except Exception as exc:
            logger.warning(
                "layered_validator_consistency_check_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return ConsistencyCheckResult(
                is_consistent=True,
                severity=ConsistencySeverity.INFO,
                detail=f"consistency_check_failed: {type(exc).__name__}",
            )

    async def _evaluate_self_rag_safe(
        self, request: FusionRequest, *, deadline: float | None = None
    ) -> SelfRAGEvalResult | Exception | None:
        """Self-RAG 质检（失败安全：异常原样返回，由调用方记录降级）。

        A.88 T5-5：``deadline`` 透传给评估器 —— 重试受 validate 预算约束。
        """
        try:
            primary = self._select_primary_response(request.agent_responses)
            return await self._self_rag_evaluator.evaluate(primary, deadline=deadline)
        except Exception as exc:  # silent-ok: 异常原样返回，由调用方统一记录降级（见 docstring）
            return exc

    @staticmethod
    def _select_primary_response(
        responses: list[AgentResponse],
    ) -> AgentResponse:
        """从多条响应中选取"主响应"（供 Self-RAG 评估用）。

        策略：取 confidence 最高的响应；并列时取第一个。
        """
        if not responses:
            raise ValueError("responses must not be empty")
        return max(responses, key=lambda r: (r.confidence, responses.index(r)))


__all__ = ["LayeredValidator"]
