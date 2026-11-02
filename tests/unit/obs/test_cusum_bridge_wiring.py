"""A.113 / G17：病种级 CUSUM 不能是空壳。

缺口实测：`register_disease_monitor` 与 `set_circuit_breaker` 在 app/ 内**零调用** →
`report_hallucination_result(disease_type=...)` 里 `disease_type in _disease_monitors`
恒假；即便注册了，监控器也没有 breaker 引用，触发后到不了 `suspend_disease`。

取舍：**显式注册**（启动时对受支持器官注册），不用"首次上报即建" ——
后者会让病种名随调用方脏数据膨胀，并把"配置行为"变成隐式副作用。
本组守卫：
1) 显式注册后上报会更新并回给调用方；未注册的病种仍不追踪（保持既有契约）；
2) 上限防病种名膨胀；
3) 监控器触发告警时，**真的**调到熔断器的 `_on_disease_cusum_alert`；
4) lifespan 真的把 breaker 交给 bridge、并注册受支持器官。
"""

from __future__ import annotations

import inspect
from types import SimpleNamespace

from app.observability.cusum.bridge import HallucinationCUSUMBridge
from app.observability.cusum.monitor import CUSUMConfig, CUSUMMonitor


class _FakeBreaker:
    def __init__(self):
        self.disease_alerts: list[tuple] = []

    def _on_disease_cusum_alert(self, disease_type, direction, value):
        self.disease_alerts.append((disease_type, direction, value))


def _bridge() -> HallucinationCUSUMBridge:
    return HallucinationCUSUMBridge(CUSUMMonitor(CUSUMConfig(target_rate=0.05, decision_threshold=3.0)))


def test_explicit_registration_then_report():
    bridge = _bridge()
    assert bridge.get_disease_monitor("thyroid") is None
    # 未注册 → 不追踪（既有契约不变）
    assert "disease_thyroid" not in bridge.report_hallucination_result(False, disease_type="thyroid")
    bridge.register_disease_monitor("thyroid")
    assert "disease_thyroid" in bridge.report_hallucination_result(False, disease_type="thyroid")


def test_cap_prevents_unbounded_growth(monkeypatch):
    bridge = _bridge()
    monkeypatch.setattr(bridge, "MAX_DISEASE_MONITORS", 2)
    for name in ("a", "b", "c"):
        bridge.register_disease_monitor(name)
    assert len(bridge._disease_monitors) == 2
    assert bridge.get_disease_monitor("c") is None


def test_disease_alert_reaches_breaker():
    bridge = _bridge()
    breaker = _FakeBreaker()
    bridge.set_circuit_breaker(breaker)
    bridge.register_disease_monitor("liver")
    monitor = bridge.get_disease_monitor("liver")
    assert monitor is not None
    # 直接推高统计量，触发 monitor 的 alert 回调链
    monitor._state.s_plus = 1e9
    monitor.update(True)
    assert breaker.disease_alerts, "病种告警没有到达熔断器（set_circuit_breaker 没接）"
    assert breaker.disease_alerts[-1][0] == "liver"


def test_lifespan_wires_breaker_into_bridge():
    from app import main as main_mod

    src = inspect.getsource(main_mod)
    assert "bridge.set_circuit_breaker(breaker)" in src
    assert "bridge.register_disease_monitor(" in src
