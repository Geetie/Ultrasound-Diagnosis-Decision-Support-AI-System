import threading
import time
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import structlog

from .bridge import HallucinationCUSUMBridge
from .monitor import CUSUMMonitor

_logger = structlog.get_logger("app.observability.cusum")


class CircuitBreakerState(str, Enum):
    SUSPENDED = "suspended"
    ACTIVE = "active"
    PENDING_CONFIRMATION = "pending_confirmation"


class AISafetyCircuitBreaker:
    """AI安全熔断器

    三态状态机：ACTIVE → PENDING_CONFIRMATION → SUSPENDED
    支持全局暂停和按病种独立暂停。
    科室主任可在15分钟内确认是否为统计波动并取消暂停。
    """

    SUSPENDED = CircuitBreakerState.SUSPENDED
    ACTIVE = CircuitBreakerState.ACTIVE
    PENDING_CONFIRMATION = CircuitBreakerState.PENDING_CONFIRMATION

    DEPARTMENT_HEAD_CONFIRM_SECONDS = 900
    SAFETY_REVIEW_DEADLINE_SECONDS = 14400

    _instance: Optional["AISafetyCircuitBreaker"] = None
    _instance_lock = threading.Lock()
    _safety_meltdown_callback: Optional[Callable[[str, float, float, str], None]] = None

    def __init__(self, cusum_monitor: CUSUMMonitor, confirm_seconds: Optional[int] = None):
        self._monitor = cusum_monitor
        self._state = self.ACTIVE
        self._suspended_at: Optional[float] = None
        self._suspension_reason: Optional[str] = None
        self._pending_reason: Optional[str] = None
        self._pending_since: Optional[float] = None
        self._confirm_seconds = confirm_seconds if confirm_seconds is not None else self.DEPARTMENT_HEAD_CONFIRM_SECONDS
        self._safety_review_deadline_at: Optional[float] = None
        self._lock = threading.Lock()
        self._suspension_callbacks: List[Callable[[str, str], None]] = []
        self._pending_notification_callbacks: List[Callable[[str, int], None]] = []
        self._suspended_diseases: Dict[str, Dict[str, Any]] = {}
        cusum_monitor.register_alert_callback(self._on_cusum_alert)
        cusum_monitor.register_weekly_rate_exceeded_callback(self._on_weekly_rate_exceeded)

    @classmethod
    def get_instance(cls) -> "AISafetyCircuitBreaker":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    bridge = HallucinationCUSUMBridge.get_instance()
                    cls._instance = cls(bridge.monitor)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        cls._instance = None

    def _on_cusum_alert(self, direction: str, value: float) -> None:
        if direction == "s_plus" and value > self._monitor._config.decision_threshold:
            self._request_suspension(f"cusum_s_plus_exceeded:{value:.2f}")

    def _on_weekly_rate_exceeded(self, week: int, rate: float) -> None:
        self._request_suspension(f"weekly_rate_exceeded:week={week},rate={rate:.4f}>threshold={self._monitor._config.target_rate:.4f}")

    def _on_disease_cusum_alert(self, disease_type: str, direction: str, value: float) -> None:
        if direction == "s_plus":
            self.suspend_disease(disease_type, f"disease_cusum_s_plus_exceeded:{disease_type}:{value:.2f}")

    def check_weekly_suspension(self) -> Optional[str]:
        alerts = self._monitor.check_compact_rules()
        if alerts:
            self._request_suspension(f"compact_rules:{','.join(alerts)}")
            return self._suspension_reason
        return None

    def _request_suspension(self, reason: str) -> None:
        skip_confirmation = False
        with self._lock:
            if self._state == self.SUSPENDED:
                return
            if self._state == self.PENDING_CONFIRMATION:
                return
            if self._confirm_seconds <= 0:
                self._state = self.SUSPENDED
                self._suspended_at = time.time()
                self._suspension_reason = reason
                skip_confirmation = True
            else:
                self._state = self.PENDING_CONFIRMATION
                self._pending_reason = reason
                self._pending_since = time.time()
        if skip_confirmation:
            self._safety_review_deadline_at = time.time() + self.SAFETY_REVIEW_DEADLINE_SECONDS
            _logger.critical("ai_assist_suspended", reason=reason,
                             safety_review_deadline_seconds=self.SAFETY_REVIEW_DEADLINE_SECONDS)
            self._notify_suspension_callbacks("suspended", reason)
            self._emit_safety_meltdown_alert(reason)
            return
        _logger.critical(
            "ai_assist_suspension_pending_confirmation",
            reason=reason,
            confirm_deadline_seconds=self._confirm_seconds,
        )
        self._notify_suspension_callbacks("pending_confirmation", reason)
        self._notify_pending_confirmation(reason)

    def cancel_pending_suspension(self, canceller: str = "department_head") -> bool:
        with self._lock:
            if self._state != self.PENDING_CONFIRMATION:
                return False
            self._state = self.ACTIVE
            cancelled_reason = self._pending_reason
            self._pending_reason = None
            self._pending_since = None
        _logger.info(
            "ai_assist_suspension_cancelled",
            cancelled_by=canceller,
            original_reason=cancelled_reason,
        )
        return True

    def check_pending_timeout(self) -> bool:
        with self._lock:
            if self._state != self.PENDING_CONFIRMATION:
                return False
            if self._pending_since is None:
                return False
            elapsed = time.time() - self._pending_since
            if elapsed < self._confirm_seconds and self._confirm_seconds > 0:
                return False
            self._state = self.SUSPENDED
            self._suspended_at = time.time()
            self._suspension_reason = self._pending_reason
            self._pending_reason = None
            self._pending_since = None
        _logger.critical("ai_assist_suspended", reason=self._suspension_reason)
        self._notify_suspension_callbacks("suspended", self._suspension_reason)
        self._emit_safety_meltdown_alert(self._suspension_reason)
        return True

    def check_safety_review_timeout(self) -> bool:
        with self._lock:
            if self._state != self.SUSPENDED:
                return False
            if self._safety_review_deadline_at is None:
                return False
            if time.time() < self._safety_review_deadline_at:
                return False
        _logger.critical(
            "safety_review_deadline_exceeded",
            reason=self._suspension_reason,
            deadline_seconds=self.SAFETY_REVIEW_DEADLINE_SECONDS,
        )
        return True

    def _suspend(self, reason: str) -> None:
        with self._lock:
            self._state = self.SUSPENDED
            self._suspended_at = time.time()
            self._suspension_reason = reason
        _logger.critical("ai_assist_suspended", reason=reason)
        self._notify_suspension_callbacks("suspended", reason)
        self._emit_safety_meltdown_alert(reason)

    def _emit_safety_meltdown_alert(self, reason: str) -> None:
        try:
            callback = AISafetyCircuitBreaker._safety_meltdown_callback
            if callback is not None:
                callback(
                    "ai_safety_circuit_breaker",
                    1.0,
                    0.0,
                    f"AI辅助已暂停: {reason}",
                )
            else:
                _logger.warning("safety_meltdown_no_callback_registered", reason=reason)
        except Exception as e:
            _logger.error("safety_meltdown_alert_emit_failed", error=str(e))

    @classmethod
    def register_safety_meltdown_callback(cls, callback: Callable[[str, float, float, str], None]) -> None:
        cls._safety_meltdown_callback = callback

    def resume(self, resumed_by: str = "system_admin") -> None:
        with self._lock:
            previous_state = self._state
            previous_reason = self._suspension_reason
            self._state = self.ACTIVE
            self._suspended_at = None
            self._suspension_reason = None
            self._pending_reason = None
            self._pending_since = None
            self._safety_review_deadline_at = None
            self._suspended_diseases.clear()
        self._monitor.reset()
        _logger.info(
            "ai_assist_resumed",
            resumed_by=resumed_by,
            previous_state=previous_state.value if hasattr(previous_state, 'value') else str(previous_state),
            previous_reason=previous_reason,
        )
        try:
            from app.observability.logging.storage import get_log_storage
            from app.observability.enums import LogCategory
            storage = get_log_storage()
            storage.write(
                category=LogCategory.AUDIT,
                message=f"AI辅助功能恢复: resumed_by={resumed_by}, previous_state={previous_state}, reason={previous_reason}",
                extra={"action": "ai_assist_resume", "resumed_by": resumed_by, "previous_reason": previous_reason},
            )
        except Exception:
            pass

    def suspend_disease(self, disease_type: str, reason: str) -> None:
        with self._lock:
            if disease_type in self._suspended_diseases:
                return
            self._suspended_diseases[disease_type] = {
                "reason": reason,
                "suspended_at": time.time(),
            }
        _logger.critical("ai_assist_disease_suspended", disease_type=disease_type, reason=reason)
        self._emit_safety_meltdown_alert(f"disease_suspended:{disease_type}: {reason}")

    def resume_disease(self, disease_type: str) -> bool:
        with self._lock:
            if disease_type not in self._suspended_diseases:
                return False
            del self._suspended_diseases[disease_type]
        _logger.info("ai_assist_disease_resumed", disease_type=disease_type)
        return True

    @property
    def is_disease_suspended(self) -> Dict[str, bool]:
        with self._lock:
            return {dt: True for dt in self._suspended_diseases}

    def get_suspended_diseases(self) -> List[str]:
        with self._lock:
            return list(self._suspended_diseases.keys())

    def is_disease_type_suspended(self, disease_type: str) -> bool:
        with self._lock:
            return disease_type in self._suspended_diseases

    def register_suspension_callback(self, callback: Callable[[str, str], None]) -> None:
        self._suspension_callbacks.append(callback)

    def register_pending_notification_callback(self, callback: Callable[[str, int], None]) -> None:
        self._pending_notification_callbacks.append(callback)

    def _notify_pending_confirmation(self, reason: str) -> None:
        for callback in self._pending_notification_callbacks:
            try:
                callback(reason, self._confirm_seconds)
            except Exception as e:
                _logger.error("pending_notification_callback_failed", error=str(e))

    def _notify_suspension_callbacks(self, event: str, reason: str) -> None:
        for callback in self._suspension_callbacks:
            try:
                callback(event, reason)
            except Exception as e:
                _logger.error("suspension_callback_failed", error=str(e))

    @property
    def is_suspended(self) -> bool:
        with self._lock:
            return self._state == self.SUSPENDED

    @property
    def is_pending_confirmation(self) -> bool:
        with self._lock:
            return self._state == self.PENDING_CONFIRMATION

    @property
    def suspension_info(self) -> Dict[str, Any]:
        with self._lock:
            info: Dict[str, Any] = {
                "state": self._state,
                "suspended_at": self._suspended_at,
                "reason": self._suspension_reason,
            }
            info["suspended_diseases"] = list(self._suspended_diseases.keys())
            if self._state == self.PENDING_CONFIRMATION:
                info["pending_reason"] = self._pending_reason
                info["pending_since"] = self._pending_since
                info["confirm_deadline"] = (
                    self._pending_since + self._confirm_seconds
                    if self._pending_since
                    else None
                )
                info["remaining_seconds"] = (
                    max(0, self._confirm_seconds - (time.time() - self._pending_since))
                    if self._pending_since
                    else 0
                )
            return info


def get_circuit_breaker() -> AISafetyCircuitBreaker:
    return AISafetyCircuitBreaker.get_instance()
