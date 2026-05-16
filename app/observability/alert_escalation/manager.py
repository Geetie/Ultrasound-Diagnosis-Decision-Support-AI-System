import asyncio
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.config.settings import settings

from .contacts import load_contacts, parse_contacts, verify_contact_reachability
from .models import AlertContact, AlertEvent
from .webhook import send_webhook

_logger = structlog.get_logger("app.observability.alert_escalation")


class AlertEscalationManager:
    def __init__(
        self,
        warning_confirm_seconds: Optional[int] = None,
        critical_confirm_seconds: Optional[int] = None,
        warning_escalate_seconds: Optional[int] = None,
        critical_escalate_seconds: Optional[int] = None,
        webhook_url: Optional[str] = None,
    ):
        self._warning_confirm_seconds = (
            warning_confirm_seconds
            or settings.alert_escalation_warning_confirm_seconds
        )
        self._critical_confirm_seconds = (
            critical_confirm_seconds
            or settings.alert_escalation_critical_confirm_seconds
        )
        self._warning_escalate_seconds = (
            warning_escalate_seconds
            or settings.alert_escalation_warning_escalate_seconds
        )
        self._critical_escalate_seconds = (
            critical_escalate_seconds
            or settings.alert_escalation_critical_escalate_seconds
        )
        self._webhook_url = webhook_url or settings.alert_webhook_url
        self._contacts: List[AlertContact] = []
        self._active_alerts: Dict[str, AlertEvent] = {}
        self._pending_webhooks: List[Tuple[str, AlertEvent]] = []
        self._check_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._lock = threading.Lock()
        self._load_contacts()

    def _load_contacts(self) -> None:
        self._contacts = load_contacts(settings.alert_contacts_json)
        if not self._contacts and settings.alert_contacts_json:
            _logger.warning("alert_contacts_load_failed_empty_result",
                           raw_config_length=len(settings.alert_contacts_json))

    def emit_alert(
        self,
        level: str,
        metric: str,
        value: float,
        threshold: float,
        message: str = "",
    ) -> AlertEvent:
        alert = AlertEvent(
            alert_id=str(uuid.uuid4()),
            level=level,
            metric=metric,
            value=value,
            threshold=threshold,
            created_at=time.time(),
            message=message,
        )
        if level == "safety_meltdown":
            alert.confirmed_at = time.time()
        with self._lock:
            self._active_alerts[alert.alert_id] = alert
        _logger.critical(
            "alert_emitted",
            alert_id=alert.alert_id,
            level=level,
            metric=metric,
            value=value,
            threshold=threshold,
        )
        if level in ("critical", "safety_meltdown") and self._webhook_url:
            self._pending_webhooks.append((f"{level}_emitted", alert))
        return alert

    def confirm_alert(self, alert_id: str) -> bool:
        with self._lock:
            alert = self._active_alerts.get(alert_id)
            if alert is None:
                return False
            alert.confirmed_at = time.time()
        _logger.info("alert_confirmed", alert_id=alert_id)
        return True

    async def check_escalations(self) -> List[str]:
        escalated = []
        now = time.time()
        alerts_snapshot = await asyncio.to_thread(self._snapshot_active_alerts)
        for alert_id, alert in alerts_snapshot:
            if alert.is_confirmed:
                continue
            if alert.level == "safety_meltdown":
                continue
            elapsed = now - alert.created_at
            if alert.level == "warning":
                if (
                    elapsed >= self._warning_confirm_seconds
                    and not alert.is_confirmed
                ):
                    if not alert.is_reminded or (
                        alert.reminded_at is not None
                        and (now - alert.reminded_at) >= self._warning_confirm_seconds
                    ):
                        alert.reminded_at = time.time()
                        await self._warning_confirm_reminder(alert)
                        escalated.append(alert_id)
                if elapsed >= self._warning_escalate_seconds and alert.escalation_level < 1:
                    await self._escalate_warning(alert)
                    escalated.append(alert_id)
            elif alert.level == "critical":
                if elapsed >= self._critical_confirm_seconds and alert.escalation_level < 1:
                    await self._escalate_critical(alert)
                    escalated.append(alert_id)
                if elapsed >= self._critical_escalate_seconds and alert.escalation_level < 2:
                    await self._escalate_critical_level2(alert)
                    escalated.append(alert_id)
        return escalated

    def emit_safety_meltdown(
        self,
        metric: str,
        value: float,
        threshold: float,
        reason: str = "",
    ) -> AlertEvent:
        return self.emit_alert(
            level="safety_meltdown",
            metric=metric,
            value=value,
            threshold=threshold,
            message=reason or f"安全熔断自动触发: {metric}={value} 超过阈值 {threshold}",
        )

    async def _warning_confirm_reminder(self, alert: AlertEvent) -> None:
        _logger.critical(
            "alert_warning_confirm_timeout",
            alert_id=alert.alert_id,
            metric=alert.metric,
            elapsed_seconds=alert.elapsed_seconds,
            confirm_deadline_seconds=self._warning_confirm_seconds,
        )
        if self._webhook_url:
            await self._send_webhook("warning_confirm_reminder", alert)

    async def _escalate_warning(self, alert: AlertEvent) -> None:
        alert.escalation_level = 1
        alert.escalated_at = time.time()
        _logger.critical(
            "alert_escalation_warning_to_superior",
            alert_id=alert.alert_id,
            metric=alert.metric,
            elapsed_seconds=alert.elapsed_seconds,
        )
        if self._webhook_url:
            await self._send_webhook("warning_escalated", alert)

    async def _escalate_critical(self, alert: AlertEvent) -> None:
        alert.escalation_level = 1
        alert.escalated_at = time.time()
        _logger.critical(
            "alert_escalation_critical_to_superior_hospital",
            alert_id=alert.alert_id,
            metric=alert.metric,
            elapsed_seconds=alert.elapsed_seconds,
        )
        if self._webhook_url:
            await self._send_webhook("critical_escalated", alert)

    async def _escalate_critical_level2(self, alert: AlertEvent) -> None:
        alert.escalation_level = 2
        _logger.critical(
            "alert_escalation_critical_to_admin_and_supplier",
            alert_id=alert.alert_id,
            metric=alert.metric,
            elapsed_seconds=alert.elapsed_seconds,
        )
        if self._webhook_url:
            await self._send_webhook("critical_escalated_level2", alert)
        for contact in self._contacts:
            role = getattr(contact, "role", "")
            is_target = role in ("hospital_admin_duty", "equipment_supplier")
            if not is_target:
                is_target = bool(
                    getattr(contact, "hospital_admin_duty", "")
                    or getattr(contact, "equipment_supplier", "")
                )
            if is_target:
                _logger.info(
                    "critical_level2_targeted_notification",
                    alert_id=alert.alert_id,
                    contact_name=getattr(contact, "name", ""),
                    contact_role=role,
                )

    async def _send_webhook(self, level: str, alert: AlertEvent) -> None:
        await send_webhook(self._webhook_url, level, alert)

    async def start(self, check_interval: float = 60.0) -> None:
        if self._check_task is not None and not self._check_task.done():
            return
        self._shutdown_event.clear()
        self._check_task = asyncio.create_task(self._check_loop(check_interval))
        _logger.info("alert_escalation_manager_started")

    async def stop(self) -> None:
        self._shutdown_event.set()
        if self._check_task and not self._check_task.done():
            self._check_task.cancel()
            try:
                await self._check_task
            except asyncio.CancelledError:
                pass
        _logger.info("alert_escalation_manager_stopped")

    async def _check_loop(self, interval: float) -> None:
        while not self._shutdown_event.is_set():
            try:
                await self._flush_pending_webhooks()
                await self.check_escalations()
            except Exception as e:
                _logger.error("escalation_check_failed", error=str(e))
            await asyncio.sleep(interval)

    async def _flush_pending_webhooks(self) -> None:
        pending = await asyncio.to_thread(self._drain_pending_webhooks)
        if not pending:
            return
        failed: List[Tuple[str, AlertEvent]] = []
        for level, alert in pending:
            try:
                await self._send_webhook(level, alert)
            except Exception as e:
                _logger.error(
                    "pending_webhook_send_failed",
                    alert_id=alert.alert_id,
                    error=str(e),
                )
                failed.append((level, alert))
        if failed:
            await asyncio.to_thread(self._requeue_failed_webhooks, failed)

    def _snapshot_active_alerts(self) -> List[Tuple[str, AlertEvent]]:
        with self._lock:
            return list(self._active_alerts.items())

    def _drain_pending_webhooks(self) -> List[Tuple[str, AlertEvent]]:
        with self._lock:
            if not self._pending_webhooks:
                return []
            pending = self._pending_webhooks[:]
            self._pending_webhooks.clear()
            return pending

    def _requeue_failed_webhooks(self, failed: List[Tuple[str, AlertEvent]]) -> None:
        with self._lock:
            self._pending_webhooks.extend(failed)

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        return [
            {
                "alert_id": a.alert_id,
                "level": a.level,
                "metric": a.metric,
                "value": a.value,
                "threshold": a.threshold,
                "message": a.message,
                "created_at": a.created_at,
                "confirmed_at": a.confirmed_at,
                "escalated_at": a.escalated_at,
                "escalation_level": a.escalation_level,
                "is_confirmed": a.is_confirmed,
                "elapsed_seconds": a.elapsed_seconds,
            }
            for a in self._active_alerts.values()
        ]

    def get_contacts(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": c.name,
                "role": c.role,
                "phone": c.phone,
                "email": c.email,
            }
            for c in self._contacts
        ]

    async def update_contacts(self, contacts_json: str) -> bool:
        new_contacts = parse_contacts(contacts_json)
        if new_contacts is None:
            return False

        if not await verify_contact_reachability(self._webhook_url, new_contacts):
            _logger.warning("update_contacts_reachability_failed_rollback")
            self.emit_alert(
                level="warning",
                metric="alert_contacts_reachability",
                value=0.0,
                threshold=1.0,
                message="告警联系人可达性验证失败，已回滚至旧配置",
            )
            return False

        self._contacts = new_contacts
        _logger.info("alert_contacts_hot_updated", count=len(new_contacts))
        return True


_alert_escalation_manager: Optional[AlertEscalationManager] = None
_alert_escalation_manager_lock = threading.Lock()


def get_alert_escalation_manager() -> AlertEscalationManager:
    global _alert_escalation_manager
    if _alert_escalation_manager is None:
        with _alert_escalation_manager_lock:
            if _alert_escalation_manager is None:
                _alert_escalation_manager = AlertEscalationManager()
    return _alert_escalation_manager


def _reset_alert_escalation_manager() -> None:
    global _alert_escalation_manager
    _alert_escalation_manager = None
