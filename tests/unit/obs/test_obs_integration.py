import asyncio
import threading
import time

import pytest

from app.observability.alert_escalation import (
    AlertEscalationManager,
    get_alert_escalation_manager,
)
from app.observability.cusum import (
    AISafetyCircuitBreaker,
    CUSUMConfig,
    CUSUMMonitor,
)


@pytest.mark.track_obs
class TestCUSUMAlertIntegration:
    def test_suspension_triggers_safety_meltdown_alert(self):
        from app.observability.alert_escalation import _reset_alert_escalation_manager
        _reset_alert_escalation_manager()
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)

        alert_mgr = get_alert_escalation_manager()

        def _on_safety_meltdown(*args, **kwargs) -> None:
            alert_mgr.emit_safety_meltdown(
                metric=kwargs.get("metric", args[0] if len(args) > 0 else ""),
                value=kwargs.get("value", args[1] if len(args) > 1 else 0.0),
                threshold=kwargs.get("threshold", args[2] if len(args) > 2 else 0.0),
                reason=kwargs.get("reason", args[3] if len(args) > 3 else ""),
            )

        AISafetyCircuitBreaker.register_safety_meltdown_callback(_on_safety_meltdown)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_suspended is True
        active = alert_mgr.get_active_alerts()
        meltdown_alerts = [a for a in active if a["level"] == "safety_meltdown"]
        assert len(meltdown_alerts) >= 1
        assert "AI辅助已暂停" in meltdown_alerts[0]["message"]
        AISafetyCircuitBreaker._safety_meltdown_callback = None
        _reset_alert_escalation_manager()

    def test_pending_confirmation_does_not_create_meltdown_alert(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_pending_confirmation is True
        active = alert_mgr.get_active_alerts()
        meltdown_alerts = [a for a in active if a["level"] == "safety_meltdown"]
        assert len(meltdown_alerts) == 0

    def test_cancel_pending_prevents_meltdown_alert(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        breaker.cancel_pending_suspension(canceller="department_head")
        breaker.check_pending_timeout()
        assert breaker.is_suspended is False

        active = alert_mgr.get_active_alerts()
        meltdown_alerts = [a for a in active if a["level"] == "safety_meltdown"]
        assert len(meltdown_alerts) == 0


@pytest.mark.track_obs
class TestSafetyMeltdownAlertProperties:
    def test_safety_meltdown_auto_confirmed(self):
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )
        alert = alert_mgr.emit_safety_meltdown(
            metric="ai_safety_circuit_breaker",
            value=1.0,
            threshold=0.0,
            reason="AI辅助已暂停: cusum_s_plus_exceeded",
        )
        assert alert.level == "safety_meltdown"
        assert alert.confirmed_at is not None

    def test_safety_meltdown_not_escalated(self):
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )
        alert = alert_mgr.emit_safety_meltdown(
            metric="test",
            value=1.0,
            threshold=0.0,
        )
        active = alert_mgr.get_active_alerts()
        assert alert.alert_id in [a["alert_id"] for a in active]


@pytest.mark.track_obs
class TestAlertEscalationConcurrency:
    def test_concurrent_emit_and_confirm(self):
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )
        errors = []

        def emit_alerts():
            try:
                for i in range(50):
                    alert_mgr.emit_alert(
                        level="warning",
                        metric=f"test_metric_{i}",
                        value=1.0,
                        threshold=0.5,
                    )
            except Exception as e:
                errors.append(e)

        def confirm_alerts():
            try:
                for _ in range(50):
                    active = alert_mgr.get_active_alerts()
                    if active:
                        alert_mgr.confirm_alert(active[0]["alert_id"])
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=emit_alerts)
        t2 = threading.Thread(target=confirm_alerts)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(errors) == 0

    def test_concurrent_emit_and_check_escalations(self):
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=1,
        )
        errors = []

        def emit_alerts():
            try:
                for i in range(30):
                    alert_mgr.emit_alert(
                        level="warning",
                        metric=f"concurrent_metric_{i}",
                        value=1.0,
                        threshold=0.5,
                    )
            except Exception as e:
                errors.append(e)

        async def check_loop():
            for _ in range(10):
                await alert_mgr.check_escalations()
                await asyncio.sleep(0.01)

        def emit_sync():
            emit_alerts()

        t1 = threading.Thread(target=emit_sync)
        t1.start()

        loop = asyncio.new_event_loop()
        loop.run_until_complete(check_loop())
        loop.close()

        t1.join()
        assert len(errors) == 0


@pytest.mark.track_obs
class TestWeeklyStatsZeroTotal:
    def test_zero_total_returns_early(self):
        monitor = CUSUMMonitor()
        monitor.record_weekly_stats(week=1, total=0, severe=0)
        assert len(monitor._weekly_stats) == 0

    def test_zero_total_with_nonzero_severe_returns_early(self):
        monitor = CUSUMMonitor()
        monitor.record_weekly_stats(week=1, total=0, severe=5)
        assert len(monitor._weekly_stats) == 0


@pytest.mark.track_obs
class TestAlertIdCollision:
    def test_alert_ids_are_unique(self):
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )
        ids = set()
        for i in range(100):
            alert = alert_mgr.emit_alert(
                level="warning",
                metric=f"collision_test_{i}",
                value=1.0,
                threshold=0.5,
            )
            assert alert.alert_id not in ids
            ids.add(alert.alert_id)

    def test_alert_id_is_full_uuid(self):
        alert_mgr = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=900,
        )
        alert = alert_mgr.emit_alert(
            level="warning",
            metric="uuid_length_test",
            value=1.0,
            threshold=0.5,
        )
        assert len(alert.alert_id) == 36
        assert alert.alert_id.count("-") == 4


@pytest.mark.track_obs
class TestPendingConfirmationAlertIntegration:
    def test_pending_triggers_critical_alert_via_notification_callback(self):
        from app.observability.alert_escalation import _reset_alert_escalation_manager
        _reset_alert_escalation_manager()
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)

        alert_mgr = get_alert_escalation_manager()

        def _on_pending_notification(reason: str, confirm_deadline_seconds: int) -> None:
            alert_mgr.emit_alert(
                level="critical",
                metric="ai_safety_circuit_breaker_pending",
                value=1.0,
                threshold=0.0,
                message=f"AI辅助暂停待确认: {reason}",
            )

        breaker.register_pending_notification_callback(_on_pending_notification)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_pending_confirmation is True
        active = alert_mgr.get_active_alerts()
        pending_alerts = [a for a in active if a["metric"] == "ai_safety_circuit_breaker_pending"]
        assert len(pending_alerts) >= 1
        assert "AI辅助暂停待确认" in pending_alerts[0]["message"]
        _reset_alert_escalation_manager()

    def test_no_pending_alert_when_suspension_is_immediate(self):
        from app.observability.alert_escalation import _reset_alert_escalation_manager
        _reset_alert_escalation_manager()
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)

        alert_mgr = get_alert_escalation_manager()

        def _on_pending_notification(reason: str, confirm_deadline_seconds: int) -> None:
            alert_mgr.emit_alert(
                level="critical",
                metric="ai_safety_circuit_breaker_pending",
                value=1.0,
                threshold=0.0,
                message=f"AI辅助暂停待确认: {reason}",
            )

        breaker.register_pending_notification_callback(_on_pending_notification)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_suspended is True
        active = alert_mgr.get_active_alerts()
        pending_alerts = [a for a in active if a["metric"] == "ai_safety_circuit_breaker_pending"]
        assert len(pending_alerts) == 0
        _reset_alert_escalation_manager()


@pytest.mark.track_obs
class TestAlertEscalationE2E:
    @pytest.mark.asyncio
    async def test_warning_full_escalation_flow(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=900,
            warning_escalate_seconds=2,
            critical_escalate_seconds=3600,
        )
        alert = manager.emit_alert(
            level="warning",
            metric="e2e_warning_metric",
            value=1.0,
            threshold=0.5,
        )
        alert.created_at = time.time() - 3
        await manager.check_escalations()
        assert alert.is_reminded is True
        assert alert.escalation_level >= 1

    @pytest.mark.asyncio
    async def test_critical_full_escalation_flow(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=14400,
            critical_confirm_seconds=1,
            warning_escalate_seconds=86400,
            critical_escalate_seconds=2,
        )
        alert = manager.emit_alert(
            level="critical",
            metric="e2e_critical_metric",
            value=1.0,
            threshold=0.5,
        )
        alert.created_at = time.time() - 5
        await manager.check_escalations()
        assert alert.escalation_level >= 1
        await manager.check_escalations()
        assert alert.escalation_level >= 2

    @pytest.mark.asyncio
    async def test_critical_confirmed_prevents_escalation(self):
        manager = AlertEscalationManager(
            warning_confirm_seconds=1,
            critical_confirm_seconds=1,
            warning_escalate_seconds=2,
            critical_escalate_seconds=2,
        )
        alert = manager.emit_alert(
            level="critical",
            metric="e2e_confirmed_metric",
            value=1.0,
            threshold=0.5,
        )
        manager.confirm_alert(alert.alert_id)
        alert.created_at = time.time() - 10
        result = await manager.check_escalations()
        assert result == []
