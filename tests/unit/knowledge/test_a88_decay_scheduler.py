"""A.88 T4-③ 守卫：时效衰减**有真实调度点**（不是"只被测试调用"）。

## 修的真问题（架构真相 §3.3）

``app/knowledge/decay.py::apply_decay`` 只被 ``tests/unit/knowledge/test_decay.py``
调用 → 生产上库里 ``confidence_score`` 恒为入库默认 1.0，衰减整条能力是壳。

## 三条不变量

1. **真的有调用方**：``Retriever.retrieve`` 会调到 ``run_scheduled_decay``
   （不是"定义了没人用"）；
2. **节流 + 留痕**：24h 内只真跑一次；命中条数进日志
   （``knowledge_decay_scheduled_run`` 的 ``updated``）——不是只看"跑过了"；
3. **失败不阻断检索**：衰减翻车时检索照常返回结果（只多一条 warning）。

反证腿：把 ``_maybe_run_decay`` 的调用从 ``retrieve`` 里删掉，
``TestSchedulerIsWired`` 全组红。
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = [pytest.mark.track_rag]

from app.embedding.protocols import (  # noqa: E402
    EmbeddingModality,
    EmbeddingResponse,
    SearchResult,
)
from app.knowledge import decay as decay_module  # noqa: E402
from app.rag.protocols import RetrievalRequest  # noqa: E402
from app.rag.retriever import Retriever  # noqa: E402


def _mk_embedding() -> MagicMock:
    emb = MagicMock()
    emb.embed_text = AsyncMock(
        return_value=EmbeddingResponse(
            embeddings=[[0.1] * 8],
            model="bge-m3-medical",
            dimension=8,
            modality=EmbeddingModality.TEXT,
        )
    )
    return emb


def _mk_vector_client() -> MagicMock:
    vc = MagicMock()
    vc.search = AsyncMock(return_value=[SearchResult(id="c1", score=0.9, metadata={})])
    return vc


def _mk_retriever() -> Retriever:
    return Retriever(
        vector_client=_mk_vector_client(), embedding_service=_mk_embedding()
    )


@pytest.fixture(autouse=True)
def _reset_schedule(monkeypatch):
    decay_module.reset_decay_schedule_for_tests()
    monkeypatch.delenv(decay_module._ENV_DECAY_INTERVAL, raising=False)
    # 默认把"落库"这些重操作打桩掉，只观察调度是否发生
    async def _no_refs(chunk_ids):
        return 0

    monkeypatch.setattr(decay_module, "record_retrieval_references", _no_refs)
    yield
    decay_module.reset_decay_schedule_for_tests()


class TestSchedulerIsWired:
    async def test_retrieve_triggers_decay(self, monkeypatch):
        """**核心**：一次检索必须把衰减调度跑起来（否则就是壳）。

        反证：删掉 ``retrieve`` 里的 ``await self._maybe_run_decay()`` → 红。
        """
        calls: list[str] = []

        async def _fake_run(*, trigger="scheduled", force=False, now=None):
            calls.append(trigger)
            return 3

        monkeypatch.setattr(decay_module, "run_scheduled_decay", _fake_run)
        result = await _mk_retriever().retrieve(
            RetrievalRequest(query="q", top_k=5), enhanced_query="q"
        )

        assert calls == ["retrieval"], "检索没有触发衰减调度"
        assert result.has_results is True

    async def test_interval_env_override_is_honored(self, monkeypatch):
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "3600")

        assert decay_module.decay_interval_seconds() == 3600.0

    def test_invalid_interval_falls_back_to_default(self, monkeypatch):
        """非法/非正值不许静默变成 0（那会导致每请求一次全表 UPDATE）。"""
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "not-a-number")
        assert (
            decay_module.decay_interval_seconds()
            == decay_module._DECAY_INTERVAL_SECONDS_DEFAULT
        )
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "-5")
        assert (
            decay_module.decay_interval_seconds()
            == decay_module._DECAY_INTERVAL_SECONDS_DEFAULT
        )


class TestThrottle:
    def test_first_run_allowed(self):
        assert decay_module.should_run_decay() is True

    def test_second_run_within_interval_is_blocked(self, monkeypatch):
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "3600")
        decay_module._last_decay_at = time.monotonic()
        assert decay_module.should_run_decay() is False

    def test_run_allowed_after_interval(self, monkeypatch):
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "60")
        decay_module._last_decay_at = time.monotonic() - 120
        assert decay_module.should_run_decay() is True

    async def test_throttled_run_does_not_touch_db(self, monkeypatch):
        """节流期内**不得**再执行 UPDATE（否则每请求一次全表重算）。"""
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "3600")
        decay_module._last_decay_at = time.monotonic()
        touched: list[int] = []

        async def _boom(*args, **kwargs):
            touched.append(1)
            return 0

        monkeypatch.setattr(decay_module, "apply_decay", _boom)
        assert await decay_module.run_scheduled_decay(trigger="retrieval") == 0
        assert touched == []

    async def test_force_bypasses_throttle(self, monkeypatch):
        monkeypatch.setenv(decay_module._ENV_DECAY_INTERVAL, "3600")
        decay_module._last_decay_at = time.monotonic()
        monkeypatch.setattr(
            decay_module, "apply_decay", AsyncMock(return_value=7)
        )
        assert await decay_module.run_scheduled_decay(trigger="ops", force=True) == 7


class TestFailureIsolation:
    async def test_decay_failure_does_not_break_retrieval(self, monkeypatch):
        """衰减翻车 → 检索照常返回（只多一条 warning）。

        反证：把 ``run_scheduled_decay`` 的 try/except 去掉并让它抛 → 红。
        """
        # 让真正的 run_scheduled_decay 在开会话时炸掉
        import app.config.database as database_module

        class _Boom:
            def __call__(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(database_module, "AsyncSessionLocal", _Boom())

        result = await _mk_retriever().retrieve(
            RetrievalRequest(query="q", top_k=5), enhanced_query="q"
        )
        assert result.has_results is True

    async def test_decay_failure_does_not_update_throttle(self, monkeypatch):
        """失败**不刷新**节流时刻 → 下一次请求可以重试（不许一次失败锁死一天）。"""
        import app.config.database as database_module

        class _Boom:
            def __call__(self):
                raise RuntimeError("db down")

        monkeypatch.setattr(database_module, "AsyncSessionLocal", _Boom())
        assert await decay_module.run_scheduled_decay(trigger="retrieval") == 0
        assert decay_module._last_decay_at is None
        assert decay_module.should_run_decay() is True

    async def test_success_updates_throttle(self, monkeypatch):
        monkeypatch.setattr(decay_module, "apply_decay", AsyncMock(return_value=4))

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def commit(self):
                return None

        import app.config.database as database_module

        monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _Session())

        assert await decay_module.run_scheduled_decay(trigger="retrieval") == 4
        assert decay_module._last_decay_at is not None
        assert decay_module.should_run_decay() is False


def test_decay_hit_count_is_logged(monkeypatch, caplog):
    """命中条数必须进日志（"只看跑过没有"等于没有留痕）。"""
    import asyncio

    events: list[tuple[str, dict]] = []

    class _Logger:
        def info(self, event, **fields):
            events.append((event, fields))

        def warning(self, event, **fields):
            events.append((event, fields))

    monkeypatch.setattr(decay_module, "logger", _Logger())

    async def _run():
        monkeypatch.setattr(decay_module, "apply_decay", AsyncMock(return_value=11))

        class _Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc_info):
                return False

            async def commit(self):
                return None

        import app.config.database as database_module

        monkeypatch.setattr(database_module, "AsyncSessionLocal", lambda: _Session())
        return await decay_module.run_scheduled_decay(trigger="retrieval")

    assert asyncio.run(_run()) == 11
    payloads = [f for e, f in events if e == "knowledge_decay_scheduled_run"]
    assert payloads, "衰减调度没有留痕"
    assert payloads[0]["updated"] == 11
    assert payloads[0]["trigger"] == "retrieval"
