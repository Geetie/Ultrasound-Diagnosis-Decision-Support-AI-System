import threading
from typing import Any, Optional

import structlog

from .create import create_cusum_monitor
from .monitor import CUSUMConfig, CUSUMMonitor

_logger = structlog.get_logger("app.observability.cusum")


class HallucinationCUSUMBridge:
    """幻觉检测与CUSUM监控桥接器

    单例模式，连接幻觉检测器与CUSUM监控器。
    支持按疾病类型注册独立CUSUM监控器（高风险病种target_rate=1%），
    当病种CUSUM触发时自动暂停该病种AI辅助。
    """

    _instance: Optional["HallucinationCUSUMBridge"] = None
    _instance_lock = threading.Lock()

    HIGH_RISK_TARGET_RATE = 0.01
    #: 病种监控器上限（A.113 / G17）。病种名来自调用方（当前是器官名），
    #: 无上限地按调用自动建设会随脏数据无限增长；20 对三器官项目足够宽。
    MAX_DISEASE_MONITORS = 20

    def __init__(self, monitor: CUSUMMonitor):
        self._monitor = monitor
        self._disease_monitors: dict[str, CUSUMMonitor] = {}
        self._circuit_breaker: Any | None = None

    @classmethod
    def get_instance(cls) -> "HallucinationCUSUMBridge":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    monitor = create_cusum_monitor()
                    cls._instance = cls(monitor)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def report_hallucination_result(
        self,
        is_severe: bool,
        disease_type: str | None = None,
    ) -> dict[str, Any]:
        result = self._monitor.update(is_severe)
        if disease_type and disease_type in self._disease_monitors:
            disease_result = self._disease_monitors[disease_type].update(is_severe)
            result[f"disease_{disease_type}"] = disease_result
        return result

    def register_disease_monitor(self, disease_type: str, config: CUSUMConfig | None = None) -> None:
        # A.113 / G17：本方法此前在 app/ 内**零调用**，于是病种级 CUSUM 整链空壳
        # （`report_hallucination_result` 的分支恒不命中）。现由 `app/main.py`
        # 启动时对受支持器官**显式**注册 —— 不用"首次上报即建"，因为那样病种名
        # 会随调用方脏数据膨胀，且把"配置行为"变成隐式副作用。
        if len(self._disease_monitors) >= self.MAX_DISEASE_MONITORS and disease_type not in self._disease_monitors:
            _logger.warning(
                "cusum_disease_monitor_cap_reached",
                disease_type=disease_type,
                cap=self.MAX_DISEASE_MONITORS,
            )
            return
        if disease_type not in self._disease_monitors:
            if config is None:
                config = CUSUMConfig(
                    target_rate=self.HIGH_RISK_TARGET_RATE,
                    decision_threshold=self._monitor._config.decision_threshold,
                    delta=0.01,
                )
            disease_monitor = CUSUMMonitor(config)
            if self._circuit_breaker is not None:
                disease_monitor.register_alert_callback(
                    lambda direction, value, dt=disease_type: self._circuit_breaker._on_disease_cusum_alert(dt, direction, value)
                )
            self._disease_monitors[disease_type] = disease_monitor

    def set_circuit_breaker(self, circuit_breaker: Any) -> None:
        self._circuit_breaker = circuit_breaker

    def get_disease_monitor(self, disease_type: str) -> CUSUMMonitor | None:
        return self._disease_monitors.get(disease_type)

    def report_monthly_overall(
        self, month: int, total: int, hallucinations: int
    ) -> list[str]:
        return self._monitor.record_monthly_stats(month, total, hallucinations)

    def report_biweekly_disease(
        self, disease_type: str, period: int, total: int, hallucinations: int
    ) -> list[str]:
        monitor = self._disease_monitors.get(disease_type)
        if monitor is None:
            _logger.warning(
                "biweekly_stats_no_disease_monitor",
                disease_type=disease_type,
            )
            return []
        return monitor.record_biweekly_stats(period, total, hallucinations)

    @property
    def monitor(self) -> CUSUMMonitor:
        return self._monitor
