"""A.113 / G20：熔断事件必须有生产订阅者（写审计库）。

缺口：`register_suspension_callback` 全仓只有测试调用 → 熔断"触发"不进审计库，
而"恢复"（resume/端点）反而有留痕，形成"恢复有记录、触发没记录"的倒挂。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.observability.cusum import audit_bridge


class _FakeAudit:
    def __init__(self):
        self.calls: list[dict] = []

    async def log_action(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace()


class _FakeBreaker:
    def __init__(self):
        self._suspension_callbacks: list = []

    def register_suspension_callback(self, cb):
        self._suspension_callbacks.append(cb)


def test_callback_writes_audit_within_loop():
    audit = _FakeAudit()

    async def main():
        cb = audit_bridge.make_suspension_audit_callback(audit_service_factory=lambda: audit)
        cb("suspended", "cusum_s_plus_exceeded:9.9")
        await asyncio.sleep(0)  # 让 create_task 跑起来
        await asyncio.sleep(0)

    asyncio.run(main())
    assert audit.calls, "熔断事件没有落审计"
    call = audit.calls[0]
    assert call["action"] == "ai_safety_breaker_suspended"
    assert call["target_type"] == "safety_breaker"
    assert call["detail"]["reason"] == "cusum_s_plus_exceeded:9.9"


def test_callback_without_loop_falls_back_to_log(monkeypatch):
    """同步上下文（无事件循环）→ 不抛，走 log storage 退化留痕。"""
    written: list[str] = []
    monkeypatch.setattr(
        audit_bridge,
        "_fallback_log",
        lambda event, reason: written.append(f"{event}:{reason}"),
    )
    cb = audit_bridge.make_suspension_audit_callback(audit_service_factory=lambda: _FakeAudit())
    cb("pending_confirmation", "weekly_rate_exceeded")
    assert written == ["pending_confirmation:weekly_rate_exceeded"]


def test_audit_failure_is_swallowed_with_warning():
    class _Boom:
        async def log_action(self, **kwargs):
            raise RuntimeError("db down")

    async def main():
        ok = await audit_bridge.write_breaker_audit("suspended", "x", audit_service=_Boom())
        assert ok is False

    asyncio.run(main())


def test_register_is_idempotent():
    breaker = _FakeBreaker()
    cb1 = audit_bridge.register_breaker_audit_subscriber(breaker)
    cb2 = audit_bridge.register_breaker_audit_subscriber(breaker)
    assert breaker._suspension_callbacks == [cb1]
    assert cb1 is cb2 or breaker._suspension_callbacks == [cb2]


def test_lifespan_registers_subscriber():
    import inspect

    from app import main as main_mod

    assert "register_breaker_audit_subscriber(breaker)" in inspect.getsource(main_mod)
