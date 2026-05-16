import asyncio
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.observability.alert_escalation import (
    AlertContact,
    AlertEvent,
    AlertEscalationManager,
)


@pytest.mark.track_obs
class TestAlertContact:
    def test_contact_creation(self):
        contact = AlertContact(
            name="张三",
            role="运维工程师",
            phone="13800138000",
            email="zhangsan@hospital.cn",
            superior_hospital_ops="李四",
            hospital_admin_duty="王五",
            equipment_supplier="设备供应商A",
        )
        assert contact.name == "张三"
        assert contact.role == "运维工程师"


@pytest.mark.track_obs
class TestAlertEvent:
    def test_event_creation(self):
        event = AlertEvent(
            alert_id="abc123",
            level="critical",
            metric="severe_hallucination_rate",
            value=0.008,
            threshold=0.005,
            created_at=time.time(),
        )
        assert event.is_confirmed is False
        assert event.elapsed_seconds >= 0

    def test_event_confirmed(self):
        event = AlertEvent(
            alert_id="abc123",
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
            created_at=time.time(),
            confirmed_at=time.time(),
        )
        assert event.is_confirmed is True


@pytest.mark.track_obs
class TestAlertEscalationManager:
    def test_emit_alert(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        alert = manager.emit_alert(
            level="critical",
            metric="severe_hallucination_rate",
            value=0.008,
            threshold=0.005,
            message="严重幻觉率超标",
        )
        assert alert.alert_id is not None
        assert alert.level == "critical"
        assert alert.is_confirmed is False

    def test_confirm_alert(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        alert = manager.emit_alert(
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
        )
        result = manager.confirm_alert(alert.alert_id)
        assert result is True
        assert alert.is_confirmed is True

    def test_confirm_nonexistent_alert(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        result = manager.confirm_alert("nonexistent")
        assert result is False

    @pytest.mark.asyncio
    async def test_warning_escalation_after_timeout(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=1,
            warning_escalate_seconds=2,
            critical_escalate_seconds=2,
        )
        alert = manager.emit_alert(
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
        )
        alert.created_at = time.time() - 3
        escalated = await manager.check_escalations()
        assert alert.is_reminded is True
        assert alert.escalation_level >= 1

    @pytest.mark.asyncio
    async def test_critical_escalation_after_15min_timeout(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=1,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        alert = manager.emit_alert(
            level="critical",
            metric="severe_hallucination_rate",
            value=0.008,
            threshold=0.005,
        )
        alert.created_at = time.time() - 3
        escalated = await manager.check_escalations()
        assert alert.escalation_level >= 1

    @pytest.mark.asyncio
    async def test_critical_second_escalation(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=1,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=2,
        )
        alert = manager.emit_alert(
            level="critical",
            metric="severe_hallucination_rate",
            value=0.008,
            threshold=0.005,
        )
        alert.created_at = time.time() - 5
        await manager.check_escalations()
        assert alert.escalation_level >= 1
        await manager.check_escalations()
        assert alert.escalation_level >= 2

    @pytest.mark.asyncio
    async def test_confirmed_alert_not_escalated(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=1,
            warning_escalate_seconds=2,
            critical_escalate_seconds=2,
        )
        alert = manager.emit_alert(
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
        )
        manager.confirm_alert(alert.alert_id)
        alert.created_at = time.time() - 10
        escalated = await manager.check_escalations()
        assert len(escalated) == 0

    def test_get_active_alerts(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        manager.emit_alert(level="warning", metric="m1", value=0.5, threshold=0.6)
        manager.emit_alert(level="critical", metric="m2", value=0.01, threshold=0.005)
        active = manager.get_active_alerts()
        assert len(active) == 2

    def test_load_contacts_from_env(self):
        with patch(
            "app.observability.alert_escalation.manager.settings"
        ) as mock_settings:
            mock_settings.alert_contacts_json = json.dumps([
                {
                    "name": "张三",
                    "role": "运维工程师",
                    "phone": "13800138000",
                }
            ])
            mock_settings.alert_escalation_warning_confirm_seconds = 14400
            mock_settings.alert_escalation_critical_confirm_seconds = 900
            mock_settings.alert_escalation_warning_escalate_seconds = 86400
            mock_settings.alert_escalation_critical_escalate_seconds = 3600
            mock_settings.alert_webhook_url = ""

            manager = AlertEscalationManager()
            contacts = manager.get_contacts()
            assert len(contacts) == 1
            assert contacts[0]["name"] == "张三"


@pytest.mark.track_obs
class TestAlertEventReminded:
    def test_event_reminded_at_default_none(self):
        event = AlertEvent(
            alert_id="abc123",
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
            created_at=time.time(),
        )
        assert event.reminded_at is None
        assert event.is_reminded is False

    def test_event_reminded_at_set(self):
        event = AlertEvent(
            alert_id="abc123",
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
            created_at=time.time(),
            reminded_at=time.time(),
        )
        assert event.is_reminded is True


@pytest.mark.track_obs
class TestWarningConfirmReminder:
    @pytest.mark.asyncio
    async def test_warning_reminder_after_confirm_timeout(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=900,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        alert = manager.emit_alert(
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
        )
        alert.created_at = time.time() - 2
        escalated = await manager.check_escalations()
        assert alert.is_reminded is True
        assert alert.alert_id in escalated

    @pytest.mark.asyncio
    async def test_warning_reminder_not_sent_twice(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=900,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        alert = manager.emit_alert(
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
        )
        alert.created_at = time.time() - 2
        await manager.check_escalations()
        assert alert.is_reminded is True
        escalated = await manager.check_escalations()
        assert alert.alert_id not in escalated


@pytest.mark.track_obs
class TestPendingWebhooks:
    def test_emit_critical_queues_webhook(self):
        manager = AlertEscalationManager(
            webhook_url="http://test:8000/webhook",
        )
        alert = manager.emit_alert(
            level="critical",
            metric="severe_hallucination_rate",
            value=0.008,
            threshold=0.005,
        )
        assert len(manager._pending_webhooks) == 1
        assert manager._pending_webhooks[0][0] == "critical_emitted"
        assert manager._pending_webhooks[0][1].alert_id == alert.alert_id

    def test_emit_warning_no_webhook(self):
        manager = AlertEscalationManager(
            webhook_url="http://test:8000/webhook",
        )
        manager.emit_alert(
            level="warning",
            metric="cache_hit_rate",
            value=0.35,
            threshold=0.60,
        )
        assert len(manager._pending_webhooks) == 0


@pytest.mark.track_obs
class TestMultipleCriticalAlerts:
    @pytest.mark.asyncio
    async def test_multiple_critical_alerts_escalate_independently(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=1,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=3600,
        )
        alert1 = manager.emit_alert(
            level="critical",
            metric="severe_hallucination_rate",
            value=0.008,
            threshold=0.005,
        )
        alert2 = manager.emit_alert(
            level="critical",
            metric="cache_hit_rate",
            value=0.1,
            threshold=0.4,
        )
        alert1.created_at = time.time() - 3
        alert2.created_at = time.time()
        escalated = await manager.check_escalations()
        assert alert1.escalation_level >= 1
        assert alert2.escalation_level == 0


@pytest.mark.track_obs
class TestWebhookRetryOnFailure:
    @pytest.mark.asyncio
    async def test_pending_webhook_retried_on_failure(self):
        from app.observability.alert_escalation.manager import AlertEscalationManager
        from app.observability.alert_escalation.models import AlertContact, AlertEvent
        from app.observability.enums import AlertLevel
        manager = AlertEscalationManager()
        contact = AlertContact(
            name="test",
            phone="13800138000",
            role="ops",
        )
        manager._contacts = [contact]
        event = AlertEvent(
            alert_id="test-001",
            level="warning",
            metric="test",
            value=0.5,
            threshold=0.6,
            created_at=1.0,
            message="test alert",
        )
        call_count = 0
        original_send = manager._send_webhook
        async def failing_send(level, alert):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise Exception("simulated failure")
        manager._send_webhook = failing_send
        with manager._lock:
            manager._pending_webhooks.append(("warning", event))
        await manager._flush_pending_webhooks()
        assert len(manager._pending_webhooks) == 1
        assert manager._pending_webhooks[0][1].alert_id == "test-001"


@pytest.mark.track_obs
class TestContactRoleMapping:
    def test_role_superior_hospital_ops_auto_fills_field(self):
        from app.observability.alert_escalation.contacts import _build_contact
        contact = _build_contact({"name": "上级医院运维", "role": "superior_hospital_ops", "phone": "13900001111"})
        assert contact.superior_hospital_ops == "上级医院运维"
        assert contact.role == "superior_hospital_ops"

    def test_role_hospital_admin_duty_auto_fills_field(self):
        from app.observability.alert_escalation.contacts import _build_contact
        contact = _build_contact({"name": "行政值班", "role": "hospital_admin_duty", "phone": "13900002222"})
        assert contact.hospital_admin_duty == "行政值班"
        assert contact.role == "hospital_admin_duty"

    def test_role_equipment_supplier_auto_fills_field(self):
        from app.observability.alert_escalation.contacts import _build_contact
        contact = _build_contact({"name": "设备供应商A", "role": "equipment_supplier", "phone": "13900003333"})
        assert contact.equipment_supplier == "设备供应商A"
        assert contact.role == "equipment_supplier"

    def test_explicit_field_not_overridden_by_role(self):
        from app.observability.alert_escalation.contacts import _build_contact
        contact = _build_contact({
            "name": "张三",
            "role": "hospital_admin_duty",
            "hospital_admin_duty": "李四",
        })
        assert contact.hospital_admin_duty == "李四"

    def test_non_special_role_no_auto_fill(self):
        from app.observability.alert_escalation.contacts import _build_contact
        contact = _build_contact({"name": "运维工程师", "role": "ops", "phone": "13900004444"})
        assert contact.superior_hospital_ops == ""
        assert contact.hospital_admin_duty == ""
        assert contact.equipment_supplier == ""


@pytest.mark.track_obs
class TestCriticalLevel2ContactSelection:
    @pytest.mark.asyncio
    async def test_level2_targets_role_based_contacts(self):
        manager = AlertEscalationManager(
            critical_confirm_seconds=1,
            critical_escalate_seconds=2,
        )
        from app.observability.alert_escalation.models import AlertContact
        manager._contacts = [
            AlertContact(name="运维", role="ops"),
            AlertContact(name="行政值班", role="hospital_admin_duty"),
            AlertContact(name="供应商", role="equipment_supplier"),
        ]
        alert = manager.emit_alert(level="critical", metric="test", value=1.0, threshold=0.5)
        alert.created_at = time.time() - 5
        await manager.check_escalations()
        await manager.check_escalations()
        assert alert.escalation_level >= 2

    @pytest.mark.asyncio
    async def test_level2_targets_field_based_contacts(self):
        manager = AlertEscalationManager(
            critical_confirm_seconds=1,
            critical_escalate_seconds=2,
        )
        from app.observability.alert_escalation.models import AlertContact
        manager._contacts = [
            AlertContact(name="运维", role="ops"),
            AlertContact(name="张三", role="ops", hospital_admin_duty="张三"),
        ]
        alert = manager.emit_alert(level="critical", metric="test", value=1.0, threshold=0.5)
        alert.created_at = time.time() - 5
        await manager.check_escalations()
        await manager.check_escalations()
        assert alert.escalation_level >= 2
