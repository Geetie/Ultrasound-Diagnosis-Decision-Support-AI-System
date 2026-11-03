"""A.143 / G21：同器官对比采样（医生已拍板接受 2× 成本）。

口径：
1) 开关默认关 —— 行为与改造前**逐字一致**（单次采样）；
2) 开启且 `contrastive_samples>=2` → 同一 specialist 用不同 temperature 独立推理，
   聚合走既有 `aggregate()`（分级一致性判定留给融合层 ConflictDetector）；
3) 未升级签名的专科（不收 temperature）→ 降级为无温度调用，不炸；
4) 样本失败 → 占位响应（needs_review），不拖垮整批；
5) 温度上限 `contrastive_max_samples` 防滥用。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.dispatcher import DispatcherAgentImpl
from app.agents.protocols import AgentContext
from app.config.settings import settings
from app.models.schemas import Organ, ResponseTier

pytestmark = pytest.mark.track_agt


def _context(**kw) -> AgentContext:
    base = dict(
        request_id="r1",
        session_id="s1",
        organ=Organ.THYROID,
        user_query="甲状腺结节",
        contrastive_samples=2,
    )
    base.update(kw)
    return AgentContext(**base)


def _sample_plan(context):
    return DispatcherAgentImpl._sample_plan(context)


def test_disabled_by_default_means_single_shot():
    assert getattr(settings, "contrastive_sampling_enabled") is False
    assert _sample_plan(_context()) == []


def test_enabled_yields_two_temperatures(monkeypatch):
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)
    plan = _sample_plan(_context())
    assert plan == [settings.contrastive_sampling_default, settings.contrastive_sampling_secondary]


def test_max_samples_caps(monkeypatch):
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)
    plan = _sample_plan(_context(contrastive_samples=9))
    assert len(plan) == settings.contrastive_max_samples


def test_explicit_temperatures_win(monkeypatch):
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)
    plan = _sample_plan(_context(sample_temperatures=[0.2, 0.9]))
    assert plan == [0.2, 0.9]


def test_single_sample_means_no_plan(monkeypatch):
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)
    assert _sample_plan(_context(contrastive_samples=1)) == []


@pytest.mark.asyncio
async def test_multi_sample_invokes_specialist_per_temperature(monkeypatch):
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)
    calls: list[float | None] = []

    class _Spec:
        def get_handler_name(self):
            return "fake"

        async def invoke(self, context, temperature=None):
            calls.append(temperature)
            from app.agents.protocols import AgentResponse

            return AgentResponse(
                agent_name="fake",
                organ=Organ.THYROID,
                content=f"TI-RADS {3 if temperature == 0.1 else 4}",
                confidence=0.8,
            )

    dispatcher = DispatcherAgentImpl.__new__(DispatcherAgentImpl)
    dispatcher._name = "dispatcher"
    dispatcher._specialists = {Organ.THYROID: _Spec()}
    dispatcher._capabilities = set()

    responses = await dispatcher._dispatch_multi_sample(_context(), [0.1, 0.7])
    assert calls == [0.1, 0.7]
    assert len(responses) == 2


@pytest.mark.asyncio
async def test_unupgraded_specialist_falls_back(monkeypatch):
    """不收 temperature 的专科 → 降级为无温度调用（不炸）。"""
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)
    calls: list[tuple] = []

    class _OldSpec:
        async def invoke(self, context):
            calls.append((None,))
            from app.agents.protocols import AgentResponse

            return AgentResponse(agent_name="old", organ=Organ.THYROID, content="ok", confidence=0.7)

    dispatcher = DispatcherAgentImpl.__new__(DispatcherAgentImpl)
    dispatcher._name = "dispatcher"
    dispatcher._specialists = {Organ.THYROID: _OldSpec()}
    responses = await dispatcher._dispatch_multi_sample(_context(), [0.1, 0.7])
    assert len(responses) == 2
    assert calls  # 降级路径确实被走到


@pytest.mark.asyncio
async def test_sample_failure_yields_placeholder(monkeypatch):
    monkeypatch.setattr(settings, "contrastive_sampling_enabled", True)

    class _Boom:
        async def invoke(self, context, temperature=None):
            if temperature == 0.7:
                raise RuntimeError("boom")
            from app.agents.protocols import AgentResponse

            return AgentResponse(agent_name="b", organ=Organ.THYROID, content="ok", confidence=0.8)

    dispatcher = DispatcherAgentImpl.__new__(DispatcherAgentImpl)
    dispatcher._name = "dispatcher"
    dispatcher._specialists = {Organ.THYROID: _Boom()}
    responses = await dispatcher._dispatch_multi_sample(_context(), [0.1, 0.7])
    assert responses[0].confidence == 0.8
    assert responses[1].confidence == 0.0 and responses[1].needs_review is True
