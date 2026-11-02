"""A.109 / N1：熔断必须有**可执行**的恢复路径。

缺口实测：`resume()` / `cancel_pending_suspension()` 在代码里，但 app/ 内零调用、
无 API；且 `check_pending_timeout()` 只有测试调用 → pending 永不过期，重启又被
快照恢复回来，一旦触发就永久 `needs_review`。

本组守卫钉四件事：
1) 看门狗真的周期调用晋级判定，单次异常不杀死自己，间隔<=0 时启动即返回；
2) 三个恢复端点直接可调（不必过 HTTP），行为与 409/400 语义正确；
3) 每次恢复都落**数据库审计**（action + actor + 原因）；
4) 源码级防回退：lifespan 必须启动/取消看门狗。
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace

import pytest

from app.api.v1.errors import BadRequestException
from app.api.v1.obs_dashboard import (
    BreakerActionRequest,
    cancel_pending,
    resume_breaker,
    resume_suspended_disease,
)
from app.observability.cusum import watchdog as wd


class _FakeBreaker:
    def __init__(self, *, pending: bool = True, disease: bool = True):
        self.state = "pending_confirmation" if pending else "active"
        self.disease = disease
        self.resumed: list[str] = []
        self.cancelled: list[str] = []
        self.sweeps = 0
        self.escalate = False

    def check_pending_timeout(self) -> bool:
        self.sweeps += 1
        return self.escalate

    @property
    def suspension_info(self) -> dict:
        return {"state": self.state, "reason": "r", "cusum_s_plus": 4.2}

    def resume(self, resumed_by: str = "system_admin") -> None:
        self.resumed.append(resumed_by)
        self.state = "active"

    def cancel_pending_suspension(self, canceller: str = "department_head") -> bool:
        if self.state != "pending_confirmation":
            return False
        self.cancelled.append(canceller)
        self.state = "active"
        return True

    def resume_disease(self, disease_type: str) -> bool:
        return self.disease


class _FakeAudit:
    def __init__(self):
        self.calls: list[dict] = []

    async def log_action(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace()


# ---------------------------------------------------------------- watchdog


def test_sweep_once_calls_breaker():
    b = _FakeBreaker()
    assert wd.sweep_once(b) is False
    assert b.sweeps == 1


def test_watchdog_loop_sweeps_and_stops():
    b = _FakeBreaker()

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(wd.run_pending_timeout_watchdog(b, interval_s=0.01, stop=stop))
        await asyncio.sleep(0.03)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(main())
    assert b.sweeps >= 2


def test_watchdog_survives_single_failure():
    class _Boom(_FakeBreaker):
        def check_pending_timeout(self) -> bool:
            self.sweeps += 1
            if self.sweeps == 1:
                raise RuntimeError("boom")
            return False

    b = _Boom()

    async def main():
        stop = asyncio.Event()
        task = asyncio.create_task(wd.run_pending_timeout_watchdog(b, interval_s=0.01, stop=stop))
        await asyncio.sleep(0.05)
        stop.set()
        await asyncio.wait_for(task, timeout=1.0)

    asyncio.run(main())
    assert b.sweeps >= 2  # 第一次抛错后仍在跑


def test_watchdog_disabled_when_interval_non_positive():
    async def main():
        await asyncio.wait_for(wd.run_pending_timeout_watchdog(_FakeBreaker(), interval_s=0), 1.0)

    asyncio.run(main())  # 立即返回，不挂起


def test_sweep_seconds_reads_setting(monkeypatch):
    from app.config.settings import settings

    monkeypatch.setattr(settings, "cusum_pending_sweep_seconds", 12.5)
    assert wd.sweep_seconds() == 12.5
    monkeypatch.setattr(settings, "cusum_pending_sweep_seconds", 0)
    assert wd.sweep_seconds() == 0.0


# ---------------------------------------------------------------- endpoints


def test_resume_endpoint_calls_resume_and_audits():
    b, audit = _FakeBreaker(), _FakeAudit()

    async def main():
        return await resume_breaker(
            BreakerActionRequest(reason="统计波动已确认", actor="qc_1"),
            breaker=b,
            audit_service=audit,
            current_user=None,
        )

    out = asyncio.run(main())
    assert b.resumed == ["qc_1"]
    assert out["ok"] is True
    assert out["current"]["state"] == "active"
    assert audit.calls[0]["action"] == "ai_assist_resume_manual"
    assert audit.calls[0]["actor"] == "qc_1"
    assert audit.calls[0]["detail"]["reason"] == "统计波动已确认"


def test_cancel_pending_rejects_wrong_state():
    b = _FakeBreaker(pending=False)
    audit = _FakeAudit()

    async def main():
        await cancel_pending(
            BreakerActionRequest(reason="x"), breaker=b, audit_service=audit, current_user=None
        )

    with pytest.raises(BadRequestException):
        asyncio.run(main())
    assert audit.calls == []  # 失败动作不写"已取消"审计


def test_cancel_pending_happy_path():
    b, audit = _FakeBreaker(), _FakeAudit()

    async def main():
        return await cancel_pending(
            BreakerActionRequest(reason="窗口内判定为波动", actor="qc_1"),
            breaker=b,
            audit_service=audit,
            current_user=None,
        )

    out = asyncio.run(main())
    assert b.cancelled == ["qc_1"] and out["ok"] is True
    assert audit.calls[0]["action"] == "ai_assist_cancel_pending"


def test_resume_disease_rejects_unknown():
    async def main():
        await resume_suspended_disease(
            BreakerActionRequest(reason="x"),
            disease_type="thyroid",
            breaker=_FakeBreaker(disease=False),
            audit_service=_FakeAudit(),
            current_user=None,
        )

    with pytest.raises(BadRequestException):
        asyncio.run(main())


# ---------------------------------------------------------------- wiring


def test_lifespan_starts_and_cancels_watchdog():
    from app import main as main_mod

    src = inspect.getsource(main_mod)
    assert "start_watchdog(app)" in src
    assert "cusum_watchdog_task" in src
    assert "watchdog_task.cancel()" in src


def test_router_exposes_recovery_routes():
    from app.api.v1 import obs_dashboard

    paths = {r.path for r in obs_dashboard.router.routes}
    assert {"/breaker/resume", "/breaker/cancel-pending", "/breaker/resume-disease"} <= paths
