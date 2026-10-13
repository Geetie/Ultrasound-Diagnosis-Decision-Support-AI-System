"""diagnosis_pipeline 单环节容错（_safe_dispatch/_safe_fuse）分支覆盖。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_inf]

import app.services.diagnosis_pipeline as mod
from app.services.diagnosis_pipeline import _safe_dispatch, _safe_fuse


@pytest.fixture
def clear_desens():
    yield
    mod._desens_l1_cache.clear()
    mod._desens_l2_cache.clear()
    mod._desens_l3_done.clear()


@pytest.mark.anyio
class TestSafeDispatch:
    async def test_dispatcher_unavailable(self, clear_desens):
        fake_app = SimpleNamespace(state=SimpleNamespace(dispatcher=None))
        with patch("app.main.app", fake_app):
            out = await _safe_dispatch(
                SimpleNamespace(organ="thyroid", session_id="s1"), "r1"
            )
        assert out[0].confidence == 0.0
        # A.59：可读中文 + 转人工；机器标记留在 trace（`_make_failed_response` 的 reason 参数）
        assert "判读流程未能启动" in out[0].content
        assert out[0].needs_review is True
        assert any("dispatcher_unavailable" in t for t in out[0].reasoning_trace)

    async def test_invoke_success_with_routing(self, clear_desens):
        response = SimpleNamespace(confidence=0.9, content="ok")
        dispatcher = MagicMock()
        dispatcher.invoke = AsyncMock(return_value=response)
        fake_app = SimpleNamespace(
            state=SimpleNamespace(dispatcher=dispatcher)
        )
        request = SimpleNamespace(
            organ="thyroid", session_id="s1", clinical_text="患者男"
        )
        routing = SimpleNamespace(
            retrieval_profile=SimpleNamespace(
                as_retrieval_kwargs=lambda: {"top_k": 5}
            )
        )
        with patch("app.main.app", fake_app):
            out = await _safe_dispatch(request, "r2", routing=routing)
        assert out == [response]
        dispatcher.invoke.assert_awaited_once()

    async def test_invoke_exception_degrades(self, clear_desens):
        dispatcher = MagicMock()
        dispatcher.invoke = AsyncMock(side_effect=RuntimeError("boom"))
        fake_app = SimpleNamespace(
            state=SimpleNamespace(dispatcher=dispatcher)
        )
        request = SimpleNamespace(
            organ="thyroid", session_id="s1", clinical_text=None
        )
        with patch("app.main.app", fake_app):
            out = await _safe_dispatch(request, "r3")
        assert out[0].confidence == 0.0
        # A.59：可读中文 + 转人工；机器标记（dispatcher_failed: <ExcType>）留在 trace
        assert "判读流程未能启动" in out[0].content
        assert out[0].needs_review is True
        assert any("dispatcher_failed" in t for t in out[0].reasoning_trace)

    async def test_l1_desensitize_called(self, clear_desens, monkeypatch):
        dispatcher = MagicMock()
        dispatcher.invoke = AsyncMock(
            return_value=SimpleNamespace(confidence=0.5, content="x")
        )
        fake_app = SimpleNamespace(
            state=SimpleNamespace(dispatcher=dispatcher)
        )
        monkeypatch.setattr(
            mod,
            "get_three_layer_desensitization_manager",
            lambda: MagicMock(
                process_standardization_layer=AsyncMock(
                    return_value=({"clinical_text": "已脱敏"}, {"modified": True})
                )
            ),
        )
        request = SimpleNamespace(
            organ="thyroid", session_id="s1", clinical_text="原文"
        )
        with patch("app.main.app", fake_app):
            await _safe_dispatch(request, "r4")
        ctx = dispatcher.invoke.await_args.args[0]
        assert ctx.user_query == "已脱敏"


@pytest.mark.anyio
class TestSafeFuse:
    async def test_fusion_engine_unavailable(self, clear_desens):
        fake_app = SimpleNamespace(state=SimpleNamespace(fusion_engine=None))
        with patch("app.main.app", fake_app):
            out = await _safe_fuse(
                SimpleNamespace(organ="thyroid"), [], "r5"
            )
        assert out.needs_review is True
        assert out.review_reason == "fusion_layer_failed"

    async def test_fuse_success_with_skip_self_rag(self, clear_desens):
        engine = MagicMock()
        engine.fuse = AsyncMock(return_value=SimpleNamespace(final_response=None))
        fake_app = SimpleNamespace(state=SimpleNamespace(fusion_engine=engine))
        with patch("app.main.app", fake_app):
            await _safe_fuse(
                SimpleNamespace(organ="thyroid"), [], "r6", skip_self_rag=True
            )
        fr = engine.fuse.await_args.args[0]
        assert fr.use_self_rag is False

    async def test_fuse_exception_degrades(self, clear_desens):
        engine = MagicMock()
        engine.fuse = AsyncMock(side_effect=RuntimeError("boom"))
        fake_app = SimpleNamespace(state=SimpleNamespace(fusion_engine=engine))
        with patch("app.main.app", fake_app):
            out = await _safe_fuse(SimpleNamespace(organ="liver"), [], "r7")
        assert out.needs_review is True
