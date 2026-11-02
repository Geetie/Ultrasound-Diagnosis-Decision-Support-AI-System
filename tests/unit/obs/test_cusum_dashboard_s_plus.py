"""A.107 / N2：运维看板的 CUSUM 进度必须是**真值**，不许恒 None。

背景：`obs_dashboard.py` 曾把 `cusum_s_plus` 写死 None —— 熔断进度对运维不可见，
而真值一直在 `CUSUMMonitor.get_state()["s_plus"]` 里。这里钉三件事：
①取得到就取真值；②取不到返回 None（不拿 0.0 冒充"没有累计"）；
③看板不再写死 None（防回退，源码级断言）。
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

from app.api.v1 import obs_dashboard
from app.observability.cusum.circuit_breaker import AISafetyCircuitBreaker


class _Monitor:
    def __init__(self, state):
        self._state = state

    def get_state(self):
        return self._state


def _breaker_with(monitor) -> AISafetyCircuitBreaker:
    b = AISafetyCircuitBreaker.__new__(AISafetyCircuitBreaker)  # 不触发单例/线程初始化
    b._monitor = monitor
    return b


def test_returns_real_s_plus():
    b = _breaker_with(_Monitor({"s_plus": 1.75}))
    assert b.monitor_s_plus() == 1.75


def test_none_when_monitor_absent():
    b = AISafetyCircuitBreaker.__new__(AISafetyCircuitBreaker)
    b._monitor = None
    assert b.monitor_s_plus() is None


def test_none_when_state_broken():
    broken = MagicMock()
    broken.get_state.side_effect = RuntimeError("boom")
    b = _breaker_with(broken)
    assert b.monitor_s_plus() is None


def test_none_when_s_plus_not_numeric():
    b = _breaker_with(_Monitor({"s_plus": "NaN"}))
    assert b.monitor_s_plus() is None


def test_dashboard_no_longer_hardcodes_none():
    src = inspect.getsource(obs_dashboard.dashboard_data)
    assert '"cusum_s_plus": None' not in src
    assert "monitor_s_plus()" in src
