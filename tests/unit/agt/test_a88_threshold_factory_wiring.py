"""A.88 配置接线③：lifespan 必须真的用 ``build_dispatcher_with_thresholds``。

## 缺陷（《架构真相》§3.3「器官阈值从 DB 加载」）

``app/agents/specialist/factory.py`` 里有两个入口：

* ``build_dispatcher(...)`` —— 不查库，``organ_thresholds=None`` → 硬编码
  0.85 / 0.88 / 0.92；
* ``build_dispatcher_with_thresholds(...)`` —— 先 ``await load_organ_thresholds()``
  再建。

``app/main.py`` 的 lifespan 一直用的是**前者** → `organ_disease_thresholds` 表
**从未被读过**，"阈值可配置"只是壳。

## 本文件钉住的三条

1. **真接线**：lifespan 走 with_thresholds 入口后，专科拿到的阈值确实来自 DB
   （不是 0.85 硬编码）——用 DB 行 0.93 做区分位。
2. **降级要留真实原因**：两种失败各自留痕，不许统一写死一句
   （AGENTS.md 硬规则 8 的反面教材：``best is None`` 一律报同一个 reason）：
   * DB 不可达 → 阈值来源记为 ``defaults`` + 真实异常文本；
   * 建 dispatcher 抛错 → 回落到 ``build_dispatcher``（硬编码）+ 真实异常文本。
3. **不可回退**：lifespan 源码里必须出现 ``build_dispatcher_with_thresholds``
   （反证腿：改回裸 ``build_dispatcher`` 即红）。
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.main as main_mod
from app.agents.specialist.factory import (
    build_dispatcher_with_thresholds,
    load_organ_thresholds,
)
from app.models.schemas import Organ

pytestmark = [pytest.mark.track_agt]


class _AsyncCtx:
    def __init__(self, db):
        self._db = db

    async def __aenter__(self):
        return self._db

    async def __aexit__(self, *exc):
        return False


class _BoomCtx:
    def __init__(self, message: str):
        self._message = message

    async def __aenter__(self):
        raise RuntimeError(self._message)

    async def __aexit__(self, *exc):
        return False


def _db_with_rows(rows):
    db = MagicMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows))
    )
    return db


def _thyroid_row(threshold: float):
    return SimpleNamespace(organ="thyroid", disease_type=None, confidence_threshold=threshold)


# ----------------------------------------------------------------------
# 1) 阈值来源要可观测（降级原因不许写死）
# ----------------------------------------------------------------------


class TestThresholdSourceIsReported:
    @pytest.mark.asyncio
    async def test_database_source_reported(self, monkeypatch):
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _AsyncCtx(_db_with_rows([_thyroid_row(0.93)])),
        )
        source: dict = {}
        await build_dispatcher_with_thresholds(
            MagicMock(), MagicMock(), threshold_source=source
        )
        assert source["source"] == "database"
        assert source["reason"] is None

    @pytest.mark.asyncio
    async def test_db_failure_reports_real_reason(self, monkeypatch):
        """反证腿：把 reason 写成固定字符串（或干脆不填）即红。"""
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _BoomCtx("connection refused by pg 15432"),
        )
        source: dict = {}
        await build_dispatcher_with_thresholds(
            MagicMock(), MagicMock(), threshold_source=source
        )
        assert source["source"] == "defaults"
        assert "connection refused by pg 15432" in source["reason"]

    @pytest.mark.asyncio
    async def test_load_organ_thresholds_signature_unchanged(self, monkeypatch):
        """既有调用口径保留：不带 source 参数依旧返回纯 dict（零回归）。"""
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _AsyncCtx(_db_with_rows([_thyroid_row(0.93)])),
        )
        result = await load_organ_thresholds()
        assert result["thyroid"][0] == pytest.approx(0.93)


# ----------------------------------------------------------------------
# 2) lifespan 侧：真接线 + 两种降级都留真实原因
# ----------------------------------------------------------------------


def _patch_logger(monkeypatch) -> MagicMock:
    spy = MagicMock()
    monkeypatch.setattr(main_mod, "logger", spy)
    return spy


class TestLifespanDispatcherWiring:
    @pytest.mark.asyncio
    async def test_db_thresholds_reach_specialists(self, monkeypatch):
        """真接线：DB 的 0.93 必须传到专科，而不是硬编码 0.85。"""
        _patch_logger(monkeypatch)
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _AsyncCtx(_db_with_rows([_thyroid_row(0.93)])),
        )
        dispatcher = await main_mod._build_lifespan_dispatcher(
            inference_engine=MagicMock(),
            knowledge_agent=MagicMock(),
            real_dependencies=True,
        )
        assert dispatcher._specialists[Organ.THYROID]._organ_threshold == pytest.approx(0.93)
        # 其余器官 DB 无行 → 各自的硬编码默认（不是被 thyroid 的值污染）
        assert dispatcher._specialists[Organ.LIVER]._organ_threshold == pytest.approx(0.88)

    @pytest.mark.asyncio
    async def test_source_level_guard_lifespan_uses_threshold_factory(self):
        """不可回退：lifespan 源码里必须调用 with_thresholds 入口。"""
        source = inspect.getsource(main_mod.lifespan)
        assert "build_dispatcher_with_thresholds" in source, (
            "lifespan 必须经 build_dispatcher_with_thresholds 建 dispatcher，"
            "否则 organ_disease_thresholds 表永远读不到（阈值恒硬编码）"
        )
        assert "_build_lifespan_dispatcher" in source

    @pytest.mark.asyncio
    async def test_lifespan_body_really_calls_helper(self, monkeypatch):
        """行为级反证腿：lifespan **真的**调用 helper（不只是源码里出现过这个名字）。

        做法：直接看 lifespan 的**字节码**（``@asynccontextmanager`` 会包一层，
        所以要 ``__wrapped__``）。比纯字符串匹配更难被"注释里写一句"糊过去：
        注释不产生 LOAD_ATTR 指令。
        """
        import dis

        target = main_mod.lifespan
        code = getattr(getattr(target, "__wrapped__", target), "__code__", None)
        assert code is not None, "取不到 lifespan 的字节码（装饰器形态变了？请同步本守卫）"

        loaded = {
            instruction.argval
            for instruction in dis.get_instructions(code)
            if instruction.opname in {"LOAD_ATTR", "LOAD_GLOBAL", "LOAD_NAME"}
        }
        assert "_build_lifespan_dispatcher" in loaded, (
            f"lifespan 字节码里没有引用 helper，实际引用={sorted(n for n in loaded if isinstance(n, str))}"
        )
        # 且不再直接引用裸 build_dispatcher（避免有人把旧分支加回来）
        assert "build_dispatcher" not in loaded

    @pytest.mark.asyncio
    async def test_db_failure_degrades_to_hardcoded_with_reason(self, monkeypatch):
        """DB 挂了 → 仍能启动（硬编码阈值），但必须留下真实原因。"""
        logger_spy = _patch_logger(monkeypatch)
        monkeypatch.setattr(
            "app.config.database.AsyncSessionLocal",
            lambda: _BoomCtx("pg 15432 timeout"),
        )
        dispatcher = await main_mod._build_lifespan_dispatcher(
            inference_engine=MagicMock(),
            knowledge_agent=MagicMock(),
            real_dependencies=True,
        )
        assert dispatcher._specialists[Organ.THYROID]._organ_threshold == pytest.approx(0.85)
        logged = " ".join(repr(c) for c in logger_spy.warning.call_args_list)
        assert "pg 15432 timeout" in logged, f"降级原因必须留真实异常文本，实际日志={logged}"

    @pytest.mark.asyncio
    async def test_build_failure_falls_back_to_hardcoded_dispatcher(self, monkeypatch):
        """建 dispatcher 阶段抛错 → 回落到 build_dispatcher（硬编码），原因留痕。

        打桩点选 ``app.agents.specialist.build_dispatcher_with_thresholds``
        （而不是 ``app.main`` 上的名字）：helper 是运行时从包入口导入的，
        补丁必须落在**被导入处**才有效。
        """
        logger_spy = _patch_logger(monkeypatch)
        monkeypatch.setattr(
            "app.agents.specialist.build_dispatcher_with_thresholds",
            AsyncMock(side_effect=RuntimeError("specialist ctor exploded")),
        )
        dispatcher = await main_mod._build_lifespan_dispatcher(
            inference_engine=MagicMock(),
            knowledge_agent=MagicMock(),
            real_dependencies=True,
        )
        assert dispatcher._specialists[Organ.THYROID]._organ_threshold == pytest.approx(0.85)
        logged = " ".join(repr(c) for c in logger_spy.warning.call_args_list)
        assert "specialist ctor exploded" in logged, f"降级原因必须留真实异常文本，实际日志={logged}"

    @pytest.mark.asyncio
    async def test_missing_dependencies_use_placeholder(self, monkeypatch):
        """依赖缺失（无 RAG / 无 inference）→ 占位 specialist，不去查库。"""
        _patch_logger(monkeypatch)
        called = {"n": 0}

        async def _spy(*args, **kwargs):
            called["n"] += 1
            raise AssertionError("依赖缺失时不应尝试加载阈值")

        monkeypatch.setattr(
            main_mod, "build_dispatcher_with_thresholds", _spy, raising=False
        )
        dispatcher = await main_mod._build_lifespan_dispatcher(
            inference_engine=None,
            knowledge_agent=None,
            real_dependencies=False,
        )
        assert dispatcher is not None
        assert called["n"] == 0
