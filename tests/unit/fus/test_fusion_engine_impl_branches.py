"""融合编排器的分支补测（quality-debt，核心模块 app/fusion；53.6% -> 目标高位）。

融合层是"失败安全"的最后一道闸：任何内部异常都必须**降级为 needs_review**
而不是抛给调用方。所以每条失败路径都要真的走到，并且要断言它给出的是
"要求人工复核"而不是一个看起来正常的结论。

覆盖：
- `__init__` 的两条参数校验；
- 空输入占位（且**不得走到校验**）；
- 校验器抛异常 → `validation_failed: <异常类名>`、不向上抛；
- final_response 选择（最高置信度优先、并列取第一个、空列表报错）；
- CRAG 的五个分支（恢复成功并清标记 / 未恢复 / 处理器抛异常 / 四个跳过条件）；
- specialist 级 `needs_review` 合并与 `specialist_flagged` 语义；
- `health_check` 与 `close`；
- `_get_self_rag_result` 的复用与回退（P0-2 修复的落点）。
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionRequest, FusionResult
from app.fusion.service import FusionEngineImpl


class _StubValidator:
    """顶替 `LayeredValidator.validate`，让三层校验的结果完全可控。"""

    def __init__(self, result: FusionResult | None = None, raises: Exception | None = None):
        self.result = result if result is not None else FusionResult()
        self.raises = raises
        self.calls = 0

    async def validate(self, request: FusionRequest) -> FusionResult:
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.result


def _response(
    confidence: float, *, name: str = "a", needs_review: bool = False
) -> AgentResponse:
    return AgentResponse(
        agent_name=name,
        content=f"content-{name}",
        confidence=confidence,
        reasoning_trace=[f"{name}-trace"],
        needs_review=needs_review,
    )


def _engine(**kwargs) -> FusionEngineImpl:
    kwargs.setdefault("conflict_detector", MagicMock())
    kwargs.setdefault("consistency_checker", MagicMock())
    return FusionEngineImpl(**kwargs)


class TestConstructorValidation:
    def test_rejects_none_conflict_detector(self):
        with pytest.raises(ValueError, match="conflict_detector must not be None"):
            FusionEngineImpl(conflict_detector=None, consistency_checker=MagicMock())

    def test_rejects_none_consistency_checker(self):
        with pytest.raises(ValueError, match="consistency_checker must not be None"):
            FusionEngineImpl(conflict_detector=MagicMock(), consistency_checker=None)


class TestEmptyInput:
    async def test_placeholder_result_and_no_validation(self):
        """空输入直接返回占位结果——**不能**再走三层校验（否则会在空列表上炸掉）。"""
        engine = _engine()
        validator = _StubValidator()
        engine._layered_validator = validator

        result = await engine.fuse(FusionRequest())

        assert result.needs_review is True
        assert result.review_reason == "no_specialist_responded"
        assert result.final_response is not None
        assert result.final_response.confidence == 0.0
        assert result.total_latency_ms >= 0.0
        assert validator.calls == 0, "空输入不该进入校验阶段"


class TestValidationFailure:
    async def test_validator_exception_degrades_and_keeps_final_response(self):
        """校验器抛异常时必须降级（needs_review + 原因带异常类名），且仍要选出 final_response。"""
        engine = _engine()
        engine._layered_validator = _StubValidator(raises=RuntimeError("validator blew up"))

        result = await engine.fuse(FusionRequest(agent_responses=[_response(0.8)]))

        assert result.needs_review is True
        assert result.review_reason == "validation_failed: RuntimeError"
        assert result.final_response is not None
        assert result.final_response.confidence == 0.8


class TestFinalResponseSelection:
    async def test_highest_confidence_wins(self):
        engine = _engine()
        engine._layered_validator = _StubValidator()

        result = await engine.fuse(
            FusionRequest(
                agent_responses=[_response(0.3, name="low"), _response(0.9, name="high")]
            )
        )

        assert result.final_response.agent_name == "high"

    async def test_tie_keeps_first(self):
        """并列时取第一个：结果必须稳定可复现（否则同一请求两次融合结论会不同）。"""
        engine = _engine()
        engine._layered_validator = _StubValidator()

        result = await engine.fuse(
            FusionRequest(
                agent_responses=[_response(0.7, name="first"), _response(0.7, name="second")]
            )
        )

        assert result.final_response.agent_name == "first"

    def test_empty_list_raises(self):
        with pytest.raises(ValueError, match="responses must not be empty"):
            FusionEngineImpl._select_final_response([])


class TestCragBranches:
    def _crag_engine(self, crag_result=None, raises=None):
        handler = MagicMock()
        if raises is not None:
            handler.handle = AsyncMock(side_effect=raises)
        else:
            handler.handle = AsyncMock(return_value=crag_result)
        engine = _engine(crag_handler=handler, rag_service=MagicMock())
        return engine, handler

    def _failed_self_rag(self, sentinel):
        return FusionResult(self_rag_passed=False, self_rag_result=sentinel)

    async def test_recovered_replaces_response_and_clears_flag(self):
        """恢复成功要同时做三件事：换响应、记 crag_recovered、**清掉 self_rag_failed 标记**。

        只换响应不清标记的话，医生看到的仍是"需要复核"，
        CRAG 的兜底就白做了。
        """
        sentinel = object()
        new_response = _response(0.95, name="crag")
        engine, handler = self._crag_engine(
            crag_result=SimpleNamespace(recovered=True, new_response=new_response, reason=None)
        )
        engine._layered_validator = _StubValidator(
            FusionResult(
                self_rag_passed=False,
                self_rag_result=sentinel,
                needs_review=True,
                review_reason="self_rag_failed",
            )
        )

        result = await engine.fuse(
            FusionRequest(agent_responses=[_response(0.6)], use_crag=True)
        )

        assert result.crag_triggered is True
        assert result.crag_recovered is True
        assert result.final_response.agent_name == "crag"
        assert result.needs_review is False
        assert result.review_reason is None
        # P0-2：CRAG 必须拿到校验器评估出的**真实**结果，而不是现场伪造的失败标记
        assert handler.handle.await_args.kwargs["self_rag_result"] is sentinel

    async def test_not_recovered_keeps_needs_review(self):
        engine, _ = self._crag_engine(
            crag_result=SimpleNamespace(
                recovered=False, new_response=None, reason="no_better_evidence"
            )
        )
        engine._layered_validator = _StubValidator(
            FusionResult(
                self_rag_passed=False,
                needs_review=True,
                review_reason="self_rag_failed",
            )
        )

        result = await engine.fuse(FusionRequest(agent_responses=[_response(0.4)]))

        assert result.crag_triggered is True
        assert result.crag_recovered is False
        assert result.needs_review is True
        assert result.review_reason == "self_rag_failed"
        assert result.final_response.agent_name == "a"

    async def test_crag_exception_marks_triggered_without_raising(self):
        """CRAG 自身炸了也必须吞掉：它是兜底手段，不能反过来把融合结果弄没。"""
        engine, _ = self._crag_engine(raises=RuntimeError("crag down"))
        engine._layered_validator = _StubValidator(
            FusionResult(self_rag_passed=False, needs_review=True, review_reason="self_rag_failed")
        )

        result = await engine.fuse(FusionRequest(agent_responses=[_response(0.4)]))

        assert result.crag_triggered is True
        assert result.crag_recovered is False
        assert result.needs_review is True
        assert result.final_response is not None

    async def test_skipped_when_use_crag_false(self):
        engine, handler = self._crag_engine(crag_result=SimpleNamespace(recovered=True))
        engine._layered_validator = _StubValidator(FusionResult(self_rag_passed=False))

        await engine.fuse(FusionRequest(agent_responses=[_response(0.4)], use_crag=False))

        handler.handle.assert_not_awaited()

    async def test_skipped_when_self_rag_passed(self):
        """Self-RAG 过了就不该触发补充检索（那是白花检索预算）。"""
        engine, handler = self._crag_engine(crag_result=SimpleNamespace(recovered=True))
        engine._layered_validator = _StubValidator(FusionResult(self_rag_passed=True))

        await engine.fuse(FusionRequest(agent_responses=[_response(0.9)]))

        handler.handle.assert_not_awaited()

    async def test_skipped_when_handler_missing(self):
        engine = _engine(rag_service=MagicMock())
        engine._layered_validator = _StubValidator(FusionResult(self_rag_passed=False))

        result = await engine.fuse(FusionRequest(agent_responses=[_response(0.4)]))

        assert result.crag_triggered is False

    async def test_skipped_when_rag_service_missing(self):
        """RAG 不可用就跳过 CRAG（可降级），但仍保留校验阶段的复核标记。"""
        handler = MagicMock()
        handler.handle = AsyncMock(return_value=SimpleNamespace(recovered=True))
        engine = _engine(crag_handler=handler)
        engine._layered_validator = _StubValidator(
            FusionResult(self_rag_passed=False, needs_review=True)
        )

        result = await engine.fuse(FusionRequest(agent_responses=[_response(0.4)]))

        handler.handle.assert_not_awaited()
        assert result.needs_review is True

    async def test_skipped_when_already_triggered(self):
        """校验阶段已触发过 CRAG 时不重复触发（去重条件 `not result.crag_triggered`）。"""
        engine, handler = self._crag_engine(crag_result=SimpleNamespace(recovered=True))
        engine._layered_validator = _StubValidator(
            FusionResult(self_rag_passed=False, crag_triggered=True)
        )

        result = await engine.fuse(FusionRequest(agent_responses=[_response(0.4)]))

        handler.handle.assert_not_awaited()
        assert result.crag_triggered is True


class TestSpecialistFlagMerge:
    async def test_specialist_needs_review_propagates(self):
        """专科自己标了 needs_review，融合层必须把它带出去（否则复核标记会丢）。"""
        engine = _engine()
        engine._layered_validator = _StubValidator()

        result = await engine.fuse(
            FusionRequest(agent_responses=[_response(0.7, needs_review=True)])
        )

        assert result.needs_review is True
        assert result.review_reason == "specialist_flagged"

    async def test_existing_reason_is_not_overwritten(self):
        engine = _engine()
        engine._layered_validator = _StubValidator(
            FusionResult(needs_review=False, review_reason="conflict_detected")
        )

        result = await engine.fuse(
            FusionRequest(agent_responses=[_response(0.7, needs_review=True)])
        )

        assert result.needs_review is True
        assert result.review_reason == "conflict_detected"


class TestHealthCheckAndClose:
    async def test_healthy(self):
        assert await _engine().health_check() is True

    async def test_broken_instance_returns_false(self):
        """防御分支：实例状态被破坏时返回 False 而不是抛异常。

        `is not None` 本身不会抛，所以要让 `_conflict_detector` 这个属性**不存在**
        （模拟半构造 / 被外部清掉的对象）。
        """
        engine = _engine()
        del engine._conflict_detector

        assert await engine.health_check() is False

    async def test_close_is_idempotent(self):
        engine = _engine(name="probe_engine")

        await engine.close()
        await engine.close()


class TestSelfRagResultReuse:
    """P0-2 修复的落点：复用校验器评估出的真实 Self-RAG 结果，而不是伪造一个。"""

    async def test_last_result_is_cached_from_validator(self):
        sentinel = object()
        engine = _engine()
        engine._layered_validator = _StubValidator(FusionResult(self_rag_result=sentinel))

        await engine.fuse(FusionRequest(agent_responses=[_response(0.5)]))

        assert engine._last_self_rag_result is sentinel

    def test_fallback_result_is_marked_as_not_evaluated(self):
        """没有校验结果时给出**自陈**未评估的占位（别伪装成"评估过且失败"）。"""
        engine = _engine()
        engine._last_self_rag_result = None

        fallback = engine._get_self_rag_result(_response(0.5))

        assert fallback.passed is False
        assert fallback.reason == "self_rag_not_evaluated_in_layered_validator"
