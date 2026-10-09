"""T-GA-FUS-01/02 — 融合层编排器 (:class:`FusionEngineImpl`)。

本模块实现 :class:`FusionEngine` 协议的具体类：协调冲突检测、一致性
校验、Self-RAG、CRAG 四大组件，输出最终的 :class:`FusionResult`。

完整编排流程：

```
fuse(request)
  ├── 1) 空输入 → 返回占位 FusionResult（needs_review=True）
  ├── 2) LayeredValidator.validate(request)  # 冲突 + 一致性 + Self-RAG
  │     - 冲突 → needs_review=True
  │     - consistency critical → needs_review=True
  │     - self_rag failed → needs_review=True（可触发 CRAG）
  ├── 3) 选择 final_response（取 confidence 最高者；并列时第一个）
  ├── 4) Self-RAG 失败 + use_crag=True + RAG 可用 → CRAG.handle(...)
  │     - 恢复成功 → 替换 final_response
  │     - 恢复失败 → 标记 crag_triggered=True（needs_review 仍 True）
  └── 5) 返回 FusionResult
```

设计要点：

- **失败安全**：任何内部异常 → 标记 needs_review=True 并 log warning，
  **不**抛错阻塞融合流程；
- **可注入**：所有子组件（ConflictDetector / ConsistencyChecker /
  SelfRAGEvaluator / CRAGHandler）通过构造参数注入；
- **可降级**：RAG Service 不可用 → 跳过 CRAG；InferenceEngine 不可用 →
  Self-RAG 走启发式；任一组件缺失都标记 needs_review 但不抛错。
- **P99 预算**：融合层（不含 Self-RAG LLM 调用）< 500ms（验收要求）。
"""
from __future__ import annotations

import time
from typing import Any

import structlog

from app.agents.protocols import AgentResponse
from app.fusion.conflict import ConflictDetector
from app.fusion.consistency import ConsistencyChecker
from app.fusion.crag import CRAGHandler
from app.fusion.layered_validator import LayeredValidator
from app.fusion.protocols import (
    FusionEngine,
    FusionRequest,
    FusionResult,
)
from app.fusion.self_rag import SelfRAGEvaluator
from app.rag.protocols import RAGService

logger = structlog.get_logger("app.fusion.service")


# ----------------------------------------------------------------------
# FusionEngineImpl
# ----------------------------------------------------------------------


class FusionEngineImpl(FusionEngine):
    """融合层编排器实现（T-GA-FUS-01/02）。

    Args:
        conflict_detector: 冲突检测器实例。
        consistency_checker: 一致性校验器实例。
        self_rag_evaluator: Self-RAG 评估器实例（可选）。
        crag_handler: CRAG 处理器实例（可选）。
        rag_service: RAG Service（用于一致性指南对比 + CRAG 检索）。
        name: 实例名称（用于日志）。
    """

    def __init__(
        self,
        conflict_detector: ConflictDetector,
        consistency_checker: ConsistencyChecker,
        self_rag_evaluator: SelfRAGEvaluator | None = None,
        crag_handler: CRAGHandler | None = None,
        rag_service: RAGService | None = None,
        name: str = "fusion_engine",
    ) -> None:
        if conflict_detector is None:
            raise ValueError("conflict_detector must not be None")
        if consistency_checker is None:
            raise ValueError("consistency_checker must not be None")
        self._conflict_detector = conflict_detector
        self._consistency_checker = consistency_checker
        self._self_rag_evaluator = self_rag_evaluator
        self._crag_handler = crag_handler
        self._rag_service = rag_service
        self._name = name
        # 预构造 LayeredValidator（self_rag 注入；CRAG 由本类在 validate 之后处理）
        self._layered_validator = LayeredValidator(
            conflict_detector=conflict_detector,
            consistency_checker=consistency_checker,
            self_rag_evaluator=self_rag_evaluator,
        )

    # ------------------------------------------------------------------
    # FusionEngine 协议
    # ------------------------------------------------------------------

    async def fuse(self, request: FusionRequest) -> FusionResult:
        """执行融合编排。

        Args:
            request: 融合请求。

        Returns:
            :class:`FusionResult`：含 final_response / conflict_report /
            consistency_result / self_rag_passed / crag_* / needs_review /
            review_reason / total_latency_ms。
        """
        start = time.monotonic()
        # 1) 空输入 → 占位响应
        if not request.agent_responses:
            placeholder = AgentResponse(
                agent_name="fusion_engine",
                content="[fusion] no specialist responded",
                confidence=0.0,
                reasoning_trace=["fusion: no input responses"],
            )
            result = FusionResult(
                final_response=placeholder,
                needs_review=True,
                review_reason="no_specialist_responded",
                total_latency_ms=(time.monotonic() - start) * 1000.0,
            )
            logger.info(
                "fusion_no_input",
                request_id=request.request_id,
            )
            return result

        # 2) 三层校验（冲突 / 一致性 / Self-RAG）。
        # 计时拆分（A.38）：fuse 的 0.7~14.1s 波动此前只有 total 一个数，
        # 14s 到底是编排开销还是 Self-RAG 的 LLM 调用不可归因 —— 拆出
        # validate（含 LLM）与 crag（含 LLM）两个子计时，pipeline 落库
        # timings_ms 后可对照。Layer2/3 在 validate 内 asyncio.gather 并行，
        # 所以不叫 self_rag_llm_ms —— 这是"含 LLM 的校验总时长"。
        t_validate = time.monotonic()
        try:
            result = await self._layered_validator.validate(request)
        except Exception as exc:
            logger.warning(
                "fusion_validate_failed",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            result = FusionResult(
                needs_review=True,
                review_reason=f"validation_failed: {type(exc).__name__}",
            )
        result.validate_ms = (time.monotonic() - t_validate) * 1000.0

        # P0-2 修复：缓存 LayeredValidator 评估后的真实 Self-RAG 结果
        self._last_self_rag_result = result.self_rag_result

        # 3) 选择 final_response（confidence 最高；并列时第一个）
        final = self._select_final_response(request.agent_responses)
        result.final_response = final

        # 4) Self-RAG 失败 + use_crag=True → CRAG 兜底
        if (
            request.use_crag
            and result.self_rag_passed is False
            and self._crag_handler is not None
            and self._rag_service is not None
            and not result.crag_triggered  # 避免重复触发
        ):
            try:
                t_crag = time.monotonic()
                crag_result = await self._crag_handler.handle(
                    response=final,
                    self_rag_result=self._get_self_rag_result(final),
                    request=request,
                )
                result.crag_ms = (time.monotonic() - t_crag) * 1000.0
                result.crag_triggered = True
                result.crag_recovered = crag_result.recovered
                if crag_result.recovered and crag_result.new_response is not None:
                    result.final_response = crag_result.new_response
                    # CRAG 恢复成功 → 清掉 self_rag_failed 标记
                    if result.review_reason == "self_rag_failed":
                        result.needs_review = False
                        result.review_reason = None
                    logger.info(
                        "fusion_crag_recovered",
                        new_evidence_count=len(crag_result.new_response.evidence_chunk_ids),
                    )
                else:
                    # 恢复失败 → 保留 needs_review=True
                    logger.info(
                        "fusion_crag_not_recovered",
                        reason=crag_result.reason,
                    )
            except Exception as exc:
                logger.warning(
                    "fusion_crag_failed",
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                result.crag_triggered = True
                result.crag_recovered = False

        # 5) 合并 final_response 自身 needs_review（specialist 标记的 needs_review）
        if (
            result.final_response is not None
            and result.final_response.needs_review
            and not result.needs_review
        ):
            result.needs_review = True
            if not result.review_reason:
                # P0-1 修复：使用语义准确的 reason
                result.review_reason = "specialist_flagged"

        result.total_latency_ms = (time.monotonic() - start) * 1000.0
        logger.info(
            "fusion_completed",
            request_id=request.request_id,
            final_confidence=(
                result.final_response.confidence if result.final_response else 0.0
            ),
            needs_review=result.needs_review,
            review_reason=result.review_reason,
            self_rag_passed=result.self_rag_passed,
            crag_triggered=result.crag_triggered,
            crag_recovered=result.crag_recovered,
            total_latency_ms=round(result.total_latency_ms, 2),
            validate_ms=round(result.validate_ms, 2),
            crag_ms=round(result.crag_ms, 2),
        )
        return result

    async def health_check(self) -> bool:
        """所有子组件 healthy → True。

        简化策略：仅检查子组件是否存在（构造期已校验非 None）。
        RAG Service 不可用是允许的（CRAG 跳过），不影响整体 health。
        """
        try:
            return (
                self._conflict_detector is not None
                and self._consistency_checker is not None
            )
        except Exception:
            return False

    async def close(self) -> None:
        """释放融合层资源（占位 — 当前各子组件无资源需关闭）。"""
        logger.info("fusion_engine_closed", name=self._name)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------

    @staticmethod
    def _select_final_response(
        responses: list[AgentResponse],
    ) -> AgentResponse:
        """选择 final_response：取 confidence 最高者；并列时第一个。"""
        if not responses:
            raise ValueError("responses must not be empty")
        # max 的 key 用 (confidence, -index) → 高 conf 优先；并列时 -index
        # 越小（即 index 越大）越靠后；改用 enumerate 比较 index
        best = responses[0]
        best_conf = best.confidence
        for r in responses[1:]:
            if r.confidence > best_conf:
                best = r
                best_conf = r.confidence
        return best

    def _get_self_rag_result(self, response: AgentResponse) -> Any:
        """获取 Self-RAG 评估结果供 CRAG 使用。

        P0-2 修复：复用 LayeredValidator 已评估的真实结果，而非重新构造
        伪造的"evidence_insufficient" 失败标记。这样 CRAG 的失败原因分类
        （``_classify_failure``）才能反映真实的失败原因。
        """
        # 1) 优先使用 LayeredValidator 写入的完整结果
        if self._last_self_rag_result is not None:
            return self._last_self_rag_result

        # 2) 回退：构造一个轻量占位（仅在 LayeredValidator 未执行 Self-RAG 时）
        #    这种情况：result.self_rag_passed == None（未启用或异常）
        from app.fusion.self_rag import SelfRAGEvalResult
        return SelfRAGEvalResult(
            passed=False,
            score=0.0,
            evidence_sufficiency=0.0,
            evidence_consistency=0.0,
            feature_completeness=0.0,
            reason="self_rag_not_evaluated_in_layered_validator",
        )


__all__ = ["FusionEngineImpl"]
