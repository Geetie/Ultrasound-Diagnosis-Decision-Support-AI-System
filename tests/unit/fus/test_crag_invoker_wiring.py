"""CRAG 真实重生成接线守卫。

## 为什么有这条测试

2026-09-28 接线审计发现：`app/main.py` 用 `hasattr(dispatcher, "invoke_specialist")`
与 `hasattr(dispatcher, "dispatch")` **探测**能力来取 `specialist_invoker`，
而 `DispatcherAgentImpl` **这两个方法都不存在** →
`specialist_invoker` 恒为 `None` → CRAG **永远**走"仅合并证据 +0.2 confidence"的占位分支
（`app/fusion/crag.py:483`），"真实重生成"从未发生。

更隐蔽的是：占位路径**同样返回 `recovered=True`**（`crag.py:279-290`），
reason 只是加了 `_evidence_only` 后缀 —— 从 `FusionResult.crag_recovered` 看不出差异，
外部只能看到"CRAG 恢复了"，无法知道它根本没重新生成。

因此本文件同时钉住三件事：
1. `DispatcherAgentImpl.invoke` 的形状与 `SpecialistInvoker`（`crag.py:93`）**兼容**；
2. `main.py` 已改为**直接取入口**（不再做静默的能力探测）；
3. 占位分支**必须留痕**（本会话的通用原则：降级可以，静默不行）。
"""
from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.dispatcher import DispatcherAgentImpl
from app.agents.protocols import AgentResponse
from app.fusion import crag as crag_module
from app.fusion.crag import CRAGHandler
from app.fusion.protocols import FusionRequest
from app.models.schemas import Organ

MAIN_PY = Path(__file__).resolve().parents[3] / "app" / "main.py"


def _original_response(confidence: float = 0.4) -> AgentResponse:
    return AgentResponse(
        agent_name="thyroid_specialist",
        organ=Organ.THYROID,
        content="原始结论",
        confidence=confidence,
        evidence_chunk_ids=["old-1"],
        reasoning_trace=["original"],
    )


def _request(response: AgentResponse) -> FusionRequest:
    return FusionRequest(request_id="req-crag", agent_responses=[response])


def _handler(**kwargs) -> CRAGHandler:
    kwargs.setdefault("rag_service", None)
    kwargs.setdefault("knowledge_agent", None)
    kwargs.setdefault("dispatcher_agent", None)
    kwargs.setdefault("specialist_invoker", None)
    return CRAGHandler(**kwargs)


class TestInvokerShape:
    """`main.py` 现在传的是 `dispatcher.invoke`，其形状必须与 crag 期望一致。"""

    def test_dispatcher_invoke_is_async_single_context_param(self):
        signature = inspect.signature(DispatcherAgentImpl.invoke)

        assert inspect.iscoroutinefunction(DispatcherAgentImpl.invoke)
        params = [
            name
            for name in signature.parameters
            if name not in ("self",)
        ]
        assert len(params) == 1, f"SpecialistInvoker 只接受一个 AgentContext，实际 {params}"

    def test_main_takes_the_entry_directly_without_capability_probing(self):
        """直接取入口；不得再用 hasattr 探测不存在的方法（探测失败是静默的）。

        断言只看**代码行**：`main.py` 里保留了说明该缺陷的注释，注释里出现旧写法是
        有意的（记录被修掉的坑），不应让它把守卫弄红。
        """
        source = MAIN_PY.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )

        assert "specialist_invoker = dispatcher.invoke" in code
        assert 'hasattr(dispatcher, "invoke_specialist")' not in code
        assert 'hasattr(dispatcher, "dispatch")' not in code


class TestRealReinvokePath:
    async def test_invoker_is_actually_called(self):
        """给了 invoker 就必须真的调用它，并被标记为 `specialist_reinvoked=True`。"""
        called: dict[str, object] = {}

        async def invoker(context):
            called["context"] = context
            return AgentResponse(
                agent_name="thyroid_specialist",
                organ=Organ.THYROID,
                content="重生成结论",
                confidence=0.9,
                evidence_chunk_ids=["new-1"],
                reasoning_trace=["reinvoked"],
            )

        handler = _handler(specialist_invoker=invoker)
        original = _original_response()

        _, specialist_reinvoked = await handler._build_new_response(
            original=original,
            new_evidence=[SimpleNamespace(chunk_id="new-1")],
            request=_request(original),
        )

        assert "context" in called, "invoker 未被调用 —— 接线又断了"
        assert specialist_reinvoked is True


class TestPlaceholderBranchIsTraced:
    """降级允许，静默不行：无 invoker 时必须留下可检索的痕迹。"""

    async def test_placeholder_returns_not_reinvoked_and_logs(self, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(crag_module, "logger", fake_logger)

        handler = _handler(specialist_invoker=None)
        original = _original_response()

        new_response, specialist_reinvoked = await handler._build_new_response(
            original=original,
            new_evidence=[SimpleNamespace(chunk_id="new-1")],
            request=_request(original),
        )

        assert specialist_reinvoked is False
        # 占位实现的既有语义：仅合并证据 + 0.2 confidence
        assert new_response.confidence == pytest.approx(0.6)
        assert any(
            "crag_recovery_evidence_only" in tag
            for tag in new_response.reasoning_trace
        )

        events = [call.args[0] for call in fake_logger.warning.call_args_list if call.args]
        assert "crag_reinvoke_placeholder_used" in events, (
            "占位分支必须留痕，否则外部无法区分'真实重生成'与'仅合并证据'"
        )

    async def test_placeholder_reason_distinguishes_configured_but_failed(
        self, monkeypatch
    ):
        """同一占位分支有两种成因，必须能区分（未配置 vs 配置了但调用失败）。"""
        fake_logger = MagicMock()
        monkeypatch.setattr(crag_module, "logger", fake_logger)

        async def broken_invoker(context):
            raise RuntimeError("llm down")

        handler = _handler(specialist_invoker=broken_invoker)
        original = _original_response()

        await handler._build_new_response(
            original=original,
            new_evidence=[SimpleNamespace(chunk_id="new-1")],
            request=_request(original),
        )

        reasons = [
            call.kwargs.get("reason")
            for call in fake_logger.warning.call_args_list
            if call.kwargs
        ]
        assert "specialist_reinvoke_failed" in reasons
