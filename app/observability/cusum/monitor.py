"""CUSUM序贯检验监控器模块

实现累积和(CUSUM)双边序贯检验算法，用于compact模式下幻觉率的实时漂移检测。
支持周统计、双周统计和月统计三种统计窗口，以及compact模式下的辅助判断规则。
"""

import threading
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

import structlog

from app.config.settings import settings
from app.observability.constants import CUSUMConstants

_logger = structlog.get_logger("app.observability.cusum")


@dataclass
class CUSUMState:
    s_plus: float = 0.0
    s_minus: float = 0.0
    total_observations: int = 0
    total_severe: int = 0
    alert_triggered: bool = False
    last_reset_time: Optional[float] = None


@dataclass
class CUSUMConfig:
    target_rate: float = 0.005
    decision_threshold: float = 3.0
    delta: float = 0.005
    overall_hallucination_warning_rate: float = 0.03
    overall_hallucination_critical_rate: float = 0.05


class CUSUMMonitor:

    def __init__(self, config: Optional[CUSUMConfig] = None):
        self._config = config or CUSUMConfig(
            target_rate=settings.obs_cusum_target_rate,
            decision_threshold=settings.obs_cusum_decision_threshold,
        )
        self._state = CUSUMState()
        self._alert_callbacks: List[Callable] = []
        self._weekly_rate_exceeded_callbacks: List[Callable] = []
        self._weekly_stats: List[Dict[str, Any]] = []
        self._biweekly_stats: List[Dict[str, Any]] = []
        self._monthly_stats: List[Dict[str, Any]] = []
        self._alert_escalation_callback: Optional[Callable[[str, str, float, float, str], None]] = None
        self._lock = threading.Lock()

    def update(self, is_severe_hallucination: bool) -> Dict[str, Any]:
        pending_notifications: List[tuple] = []
        with self._lock:
            self._state.total_observations += 1
            if is_severe_hallucination:
                self._state.total_severe += 1

            x = 1.0 if is_severe_hallucination else 0.0
            p0 = self._config.target_rate
            delta = self._config.delta
            k = p0 + delta / 2

            self._state.s_plus = max(0.0, self._state.s_plus + (x - k))
            self._state.s_minus = max(0.0, self._state.s_minus + (k - x))

            triggered = False
            if self._state.s_plus > self._config.decision_threshold:
                triggered = True
                self._state.alert_triggered = True
                _logger.critical(
                    "cusum_threshold_exceeded",
                    s_plus=self._state.s_plus,
                    threshold=self._config.decision_threshold,
                    total_observations=self._state.total_observations,
                    total_severe=self._state.total_severe,
                )
                pending_notifications.append(("s_plus", self._state.s_plus))
                self._state.s_plus = 0.0

            if self._state.s_minus > self._config.decision_threshold:
                _logger.info(
                    "cusum_s_minus_improvement_detected",
                    s_minus=self._state.s_minus,
                    threshold=self._config.decision_threshold,
                    total_observations=self._state.total_observations,
                )
                pending_notifications.append(("s_minus", self._state.s_minus))
                self._state.s_minus = 0.0

            if self._state.alert_triggered and self._state.s_plus == 0.0:
                current_rate = self._state.total_severe / max(self._state.total_observations, 1)
                if current_rate <= self._config.target_rate:
                    self._state.alert_triggered = False
                    _logger.info(
                        "cusum_alert_triggered_cleared",
                        current_rate=current_rate,
                        target_rate=self._config.target_rate,
                    )

            result = {
                "s_plus": self._state.s_plus,
                "s_minus": self._state.s_minus,
                "triggered": triggered,
                "total_observations": self._state.total_observations,
                "total_severe": self._state.total_severe,
                "current_rate": self._state.total_severe / max(self._state.total_observations, 1),
            }

        for direction, value in pending_notifications:
            self._notify_alerts(direction, value)

        return result

    def reset(self) -> None:
        with self._lock:
            self._state = CUSUMState()
            _logger.info("cusum_reset")

    def get_state(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "s_plus": self._state.s_plus,
                "s_minus": self._state.s_minus,
                "total_observations": self._state.total_observations,
                "total_severe": self._state.total_severe,
                "current_rate": self._state.total_severe / max(self._state.total_observations, 1),
                "alert_triggered": self._state.alert_triggered,
            }

    def record_weekly_stats(self, week: int, total: int, severe: int) -> None:
        if total == 0:
            _logger.info("weekly_stats_window_empty", week=week)
            return
        if total < 350:
            _logger.warning(
                "weekly_stats_window_small",
                week=week,
                total=total,
                expected_minimum=350,
            )
        rate = severe / total
        with self._lock:
            self._weekly_stats.append({
                "week": week,
                "total": total,
                "severe": severe,
                "rate": rate,
            })

        if rate > self._config.target_rate:
            _logger.critical(
                "weekly_severe_hallucination_rate_exceeded",
                week=week,
                rate=rate,
                threshold=self._config.target_rate,
                total=total,
                severe=severe,
            )
            for callback in self._weekly_rate_exceeded_callbacks:
                try:
                    callback(week, rate)
                except Exception as e:
                    _logger.error("weekly_rate_exceeded_callback_failed", error=str(e))

    def record_biweekly_stats(self, period: int, total: int, severe: int) -> None:
        if total == 0:
            _logger.info("biweekly_stats_no_data", period=period)
            return
        rate = severe / total
        with self._lock:
            self._biweekly_stats.append({
                "period": period,
                "total": total,
                "severe": severe,
                "rate": rate,
            })

        if rate > CUSUMConstants.BIWEEKLY_HIGH_RISK_RATE:
            _logger.critical(
                "biweekly_severe_hallucination_rate_exceeded",
                period=period,
                rate=rate,
                threshold=CUSUMConstants.BIWEEKLY_HIGH_RISK_RATE,
                total=total,
                severe=severe,
            )

    def record_monthly_stats(self, month: int, total: int, hallucinations: int) -> List[str]:
        rate = hallucinations / max(total, 1)
        with self._lock:
            self._monthly_stats.append({
                "month": month,
                "total": total,
                "hallucinations": hallucinations,
                "rate": rate,
            })

        alerts = []
        if rate > self._config.overall_hallucination_critical_rate:
            _logger.critical(
                "monthly_overall_hallucination_rate_critical",
                month=month,
                rate=rate,
                threshold=self._config.overall_hallucination_critical_rate,
                total=total,
                hallucinations=hallucinations,
            )
            alerts.append("monthly_overall_hallucination_critical")
            self._escalate_monthly_alert("critical", month, rate, total, hallucinations)
        elif rate > self._config.overall_hallucination_warning_rate:
            _logger.warning(
                "monthly_overall_hallucination_rate_warning",
                month=month,
                rate=rate,
                threshold=self._config.overall_hallucination_warning_rate,
                total=total,
                hallucinations=hallucinations,
            )
            alerts.append("monthly_overall_hallucination_warning")
            self._escalate_monthly_alert("warning", month, rate, total, hallucinations)
        return alerts

    def _escalate_monthly_alert(self, level: str, month: int, rate: float, total: int, hallucinations: int) -> None:
        try:
            callback = self._alert_escalation_callback
            if callback is not None:
                callback(
                    level,
                    f"monthly_overall_hallucination_rate_{level}",
                    rate,
                    self._config.overall_hallucination_critical_rate if level == "critical" else self._config.overall_hallucination_warning_rate,
                    f"月度幻觉率告警: month={month}, total={total}, hallucinations={hallucinations}, rate={rate:.4f}",
                )
            else:
                _logger.warning("monthly_alert_no_escalation_callback_registered", level=level, rate=rate)
        except Exception as e:
            _logger.error("monthly_alert_escalation_failed", error=str(e))

    def register_alert_escalation_callback(self, callback: Callable[[str, str, float, float, str], None]) -> None:
        self._alert_escalation_callback = callback

    def check_compact_rules(self) -> List[str]:
        with self._lock:
            weekly_snapshot = list(self._weekly_stats)
            biweekly_snapshot = list(self._biweekly_stats)

        alerts = []
        if len(weekly_snapshot) >= 3:
            recent_3 = weekly_snapshot[-3:]
            consecutive_with_severe = sum(1 for w in recent_3 if w["severe"] >= 1)
            if consecutive_with_severe >= 3:
                alerts.append("consecutive_3_weeks_with_severe")

        if weekly_snapshot:
            latest = weekly_snapshot[-1]
            if latest["severe"] >= 3:
                alerts.append("single_week_3_plus_severe")
            if latest["rate"] > self._config.target_rate:
                alerts.append("weekly_rate_exceeded")

        if len(biweekly_snapshot) >= 2:
            recent_2 = biweekly_snapshot[-2:]
            consecutive_with_severe = sum(1 for w in recent_2 if w["severe"] >= 1)
            if consecutive_with_severe >= 2:
                alerts.append("consecutive_2_biweekly_periods_with_severe")
            for bw in recent_2:
                if bw["severe"] >= 1 and bw["rate"] > CUSUMConstants.BIWEEKLY_HIGH_RISK_RATE:
                    alerts.append("biweekly_high_risk_severe")
                    break
            for bw in recent_2:
                if bw["rate"] > CUSUMConstants.BIWEEKLY_HIGH_RISK_RATE:
                    alerts.append("biweekly_high_risk_rate_exceeded")
                    break

        return alerts

    def register_alert_callback(self, callback: Callable) -> None:
        self._alert_callbacks.append(callback)

    def register_weekly_rate_exceeded_callback(self, callback: Callable) -> None:
        self._weekly_rate_exceeded_callbacks.append(callback)

    def _notify_alerts(self, direction: str, value: float) -> None:
        for callback in self._alert_callbacks:
            try:
                callback(direction, value)
            except Exception as e:
                _logger.error("cusum_alert_callback_failed", error=str(e))
