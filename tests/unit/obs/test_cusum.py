import pytest

from app.observability.cusum import (
    AISafetyCircuitBreaker,
    CUSUMCalibrationResult,
    CUSUMCalibrator,
    CUSUMConfig,
    CUSUMMonitor,
    HallucinationCUSUMBridge,
    create_cusum_monitor,
)


@pytest.mark.track_obs
class TestCUSUMConfig:
    """CUSUM配置默认值与自定义参数验证。"""
    def test_default_config(self):
        config = CUSUMConfig()
        assert config.target_rate == 0.005
        assert config.decision_threshold == 3.0
        assert config.delta == 0.005

    def test_custom_config(self):
        config = CUSUMConfig(target_rate=0.01, decision_threshold=5.0, delta=0.01)
        assert config.target_rate == 0.01
        assert config.decision_threshold == 5.0
        assert config.delta == 0.01


@pytest.mark.track_obs
class TestCUSUMMonitor:
    """CUSUM序贯检验公式正确性：s_plus/s_minus累积、阈值触发、自动重置。"""
    def test_initial_state(self):
        monitor = CUSUMMonitor()
        state = monitor.get_state()
        assert state["s_plus"] == 0.0
        assert state["s_minus"] == 0.0
        assert state["total_observations"] == 0
        assert state["total_severe"] == 0
        assert state["current_rate"] == 0.0
        assert state["alert_triggered"] is False

    def test_update_no_hallucination(self):
        monitor = CUSUMMonitor()
        result = monitor.update(is_severe_hallucination=False)
        assert result["total_observations"] == 1
        assert result["total_severe"] == 0
        assert result["triggered"] is False

    def test_update_with_hallucination(self):
        monitor = CUSUMMonitor()
        result = monitor.update(is_severe_hallucination=True)
        assert result["total_observations"] == 1
        assert result["total_severe"] == 1
        assert result["s_plus"] > 0

    def test_s_plus_increases_with_hallucination(self):
        monitor = CUSUMMonitor()
        r1 = monitor.update(is_severe_hallucination=False)
        r2 = monitor.update(is_severe_hallucination=True)
        assert r2["s_plus"] > r1["s_plus"]

    def test_s_minus_increases_without_hallucination(self):
        config = CUSUMConfig(target_rate=0.5, delta=0.1)
        monitor = CUSUMMonitor(config)
        r1 = monitor.update(is_severe_hallucination=False)
        assert r1["s_minus"] > 0

    def test_s_plus_standard_cusum_formula(self):
        config = CUSUMConfig(target_rate=0.005, delta=0.005)
        monitor = CUSUMMonitor(config)
        k = 0.005 + 0.005 / 2
        result = monitor.update(is_severe_hallucination=True)
        assert abs(result["s_plus"] - (1.0 - k)) < 1e-10

    def test_s_minus_standard_cusum_formula(self):
        config = CUSUMConfig(target_rate=0.005, delta=0.005)
        monitor = CUSUMMonitor(config)
        k = 0.005 + 0.005 / 2
        result = monitor.update(is_severe_hallucination=False)
        assert abs(result["s_minus"] - (k - 0.0)) < 1e-10

    def test_default_params_s_minus_accumulates(self):
        config = CUSUMConfig(target_rate=0.005, delta=0.005)
        monitor = CUSUMMonitor(config)
        for _ in range(10):
            monitor.update(is_severe_hallucination=False)
        state = monitor.get_state()
        assert state["s_minus"] > 0

    def test_s_plus_resets_to_zero(self):
        config = CUSUMConfig(target_rate=0.5, delta=0.1)
        monitor = CUSUMMonitor(config)
        for _ in range(10):
            monitor.update(is_severe_hallucination=False)
        state = monitor.get_state()
        assert state["s_plus"] == 0.0

    def test_alert_triggered_when_threshold_exceeded(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        for _ in range(5):
            monitor.update(is_severe_hallucination=True)
        state = monitor.get_state()
        assert state["alert_triggered"] is True

    def test_reset(self):
        monitor = CUSUMMonitor()
        monitor.update(is_severe_hallucination=True)
        monitor.reset()
        state = monitor.get_state()
        assert state["s_plus"] == 0.0
        assert state["total_observations"] == 0

    def test_current_rate_calculation(self):
        monitor = CUSUMMonitor()
        for _ in range(100):
            monitor.update(is_severe_hallucination=False)
        monitor.update(is_severe_hallucination=True)
        state = monitor.get_state()
        assert abs(state["current_rate"] - 1 / 101) < 0.001

    def test_alert_callback(self):
        triggered = []
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        monitor.register_alert_callback(lambda d, v: triggered.append((d, v)))

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert len(triggered) > 0


@pytest.mark.track_obs
class TestCUSUMWeeklyStats:
    """compact模式周统计与熔断规则：连续3周/单周3例严重幻觉触发暂停。"""
    def test_record_weekly_stats(self):
        monitor = CUSUMMonitor()
        monitor.record_weekly_stats(week=1, total=350, severe=1)
        monitor.record_weekly_stats(week=2, total=350, severe=0)
        monitor.record_weekly_stats(week=3, total=350, severe=2)

        assert len(monitor._weekly_stats) == 3

    def test_compact_rules_consecutive_3_weeks(self):
        monitor = CUSUMMonitor()
        monitor.record_weekly_stats(week=1, total=350, severe=1)
        monitor.record_weekly_stats(week=2, total=350, severe=1)
        monitor.record_weekly_stats(week=3, total=350, severe=1)

        alerts = monitor.check_compact_rules()
        assert "consecutive_3_weeks_with_severe" in alerts

    def test_compact_rules_single_week_3_plus(self):
        monitor = CUSUMMonitor()
        monitor.record_weekly_stats(week=1, total=350, severe=3)

        alerts = monitor.check_compact_rules()
        assert "single_week_3_plus_severe" in alerts

    def test_compact_rules_no_alert(self):
        monitor = CUSUMMonitor()
        monitor.record_weekly_stats(week=1, total=350, severe=0)
        monitor.record_weekly_stats(week=2, total=350, severe=0)

        alerts = monitor.check_compact_rules()
        assert len(alerts) == 0


@pytest.mark.track_obs
class TestCUSUMBiweeklyStats:
    """compact模式双周统计：连续2个双周有严重幻觉且幻觉率>1%触发告警。"""
    def test_record_biweekly_stats(self):
        monitor = CUSUMMonitor()
        monitor.record_biweekly_stats(period=1, total=700, severe=1)
        monitor.record_biweekly_stats(period=2, total=700, severe=0)
        assert len(monitor._biweekly_stats) == 2

    def test_compact_rules_consecutive_2_biweekly_periods(self):
        monitor = CUSUMMonitor()
        monitor.record_biweekly_stats(period=1, total=700, severe=1)
        monitor.record_biweekly_stats(period=2, total=700, severe=1)

        alerts = monitor.check_compact_rules()
        assert "consecutive_2_biweekly_periods_with_severe" in alerts

    def test_biweekly_no_alert_when_no_severe(self):
        monitor = CUSUMMonitor()
        monitor.record_biweekly_stats(period=1, total=700, severe=0)
        monitor.record_biweekly_stats(period=2, total=700, severe=0)

        alerts = monitor.check_compact_rules()
        assert "consecutive_2_biweekly_periods_with_severe" not in alerts


@pytest.mark.track_obs
class TestCUSUMCalibrator:
    """CUSUM参数校准：ARL₀≥100/ARL₁≤50有效性验证与报告生成。"""
    def test_calibrate_returns_results(self):
        calibrator = CUSUMCalibrator(
            target_rate=0.005,
            daily_cases=50,
            simulation_days=10,
            num_simulations=10,
        )
        results = calibrator.calibrate(
            h_values=[2.0, 3.0],
            deltas=[0.01],
        )
        assert len(results) == 2
        for r in results:
            assert isinstance(r, CUSUMCalibrationResult)
            assert r.arl_0 > 0
            assert r.arl_1 > 0

    def test_calibration_result_validity(self):
        calibrator = CUSUMCalibrator(
            target_rate=0.005,
            daily_cases=50,
            simulation_days=10,
            num_simulations=10,
        )
        results = calibrator.calibrate(h_values=[3.0], deltas=[0.01])
        result = results[0]
        assert result.h == 3.0
        assert result.delta == 0.01
        assert isinstance(result.is_valid, bool)
        assert isinstance(result.recommendation, str)

    def test_generate_calibration_report(self):
        calibrator = CUSUMCalibrator(
            target_rate=0.005,
            daily_cases=50,
            simulation_days=10,
            num_simulations=10,
        )
        results = calibrator.calibrate(h_values=[3.0], deltas=[0.01])
        report = calibrator.generate_calibration_report(results)

        assert report["target_rate"] == 0.005
        assert report["daily_cases"] == 50
        assert report["num_simulations"] == 10
        assert "all_results" in report
        assert len(report["all_results"]) == 1

    def test_calibrator_uses_same_granularity_as_monitor(self):
        calibrator = CUSUMCalibrator(
            target_rate=0.005,
            daily_cases=50,
            simulation_days=5,
            num_simulations=5,
        )
        arl = calibrator._simulate_arl(rate=0.005, h=3.0, delta=0.01)
        assert arl > 0


@pytest.mark.track_obs
class TestCreateCUSUMMonitor:
    def test_create_with_defaults(self):
        monitor = create_cusum_monitor()
        assert monitor._config.target_rate == 0.005
        assert monitor._config.decision_threshold == 3.0

    def test_create_with_custom_params(self):
        monitor = create_cusum_monitor(target_rate=0.01, decision_threshold=5.0)
        assert monitor._config.target_rate == 0.01
        assert monitor._config.decision_threshold == 5.0


@pytest.mark.track_obs
class TestHallucinationCUSUMBridge:
    def test_bridge_reports_to_global_monitor(self):
        monitor = CUSUMMonitor()
        bridge = HallucinationCUSUMBridge(monitor)
        result = bridge.report_hallucination_result(is_severe=True)
        assert result["total_observations"] == 1
        assert result["total_severe"] == 1

    def test_bridge_reports_to_disease_monitor(self):
        monitor = CUSUMMonitor()
        bridge = HallucinationCUSUMBridge(monitor)
        bridge.register_disease_monitor("liver")
        result = bridge.report_hallucination_result(is_severe=True, disease_type="liver")
        assert "disease_liver" in result
        assert result["disease_liver"]["total_observations"] == 1

    def test_bridge_without_disease_monitor(self):
        monitor = CUSUMMonitor()
        bridge = HallucinationCUSUMBridge(monitor)
        result = bridge.report_hallucination_result(is_severe=True, disease_type="unknown")
        assert "disease_unknown" not in result

    def test_bridge_monitor_property(self):
        monitor = CUSUMMonitor()
        bridge = HallucinationCUSUMBridge(monitor)
        assert bridge.monitor is monitor


@pytest.mark.track_obs
class TestAISafetyCircuitBreaker:
    """AI安全断路器：CUSUM触发→待确认→暂停→恢复全生命周期测试。"""
    def test_circuit_breaker_pending_on_cusum_threshold(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=1)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_pending_confirmation is True
        assert breaker.is_suspended is False

    def test_circuit_breaker_suspends_after_confirm_timeout(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_suspended is True

    def test_circuit_breaker_cancel_pending_suspension(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_pending_confirmation is True
        result = breaker.cancel_pending_suspension(canceller="department_head")
        assert result is True
        assert breaker.is_suspended is False
        assert breaker.is_pending_confirmation is False

    def test_circuit_breaker_cancel_when_not_pending(self):
        monitor = CUSUMMonitor()
        breaker = AISafetyCircuitBreaker(monitor)
        result = breaker.cancel_pending_suspension()
        assert result is False

    def test_circuit_breaker_suspends_on_compact_rules(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=10.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)

        monitor.record_weekly_stats(week=1, total=350, severe=1)
        monitor.record_weekly_stats(week=2, total=350, severe=1)
        monitor.record_weekly_stats(week=3, total=350, severe=1)

        breaker.check_weekly_suspension()
        assert breaker.is_suspended is True

    def test_circuit_breaker_resume_resets_monitor(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)
        assert breaker.is_suspended is True

        breaker.resume()
        assert breaker.is_suspended is False
        state = monitor.get_state()
        assert state["total_observations"] == 0

    def test_circuit_breaker_suspension_info_active(self):
        monitor = CUSUMMonitor()
        breaker = AISafetyCircuitBreaker(monitor)

        info = breaker.suspension_info
        assert info["state"] == AISafetyCircuitBreaker.ACTIVE
        assert info["suspended_at"] is None
        assert info["reason"] is None

    def test_circuit_breaker_suspension_info_pending(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        info = breaker.suspension_info
        assert info["state"] == AISafetyCircuitBreaker.PENDING_CONFIRMATION
        assert info["pending_reason"] is not None
        assert info["remaining_seconds"] > 0

    def test_circuit_breaker_suspension_info_suspended(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        info = breaker.suspension_info
        assert info["state"] == AISafetyCircuitBreaker.SUSPENDED
        assert info["suspended_at"] is not None
        assert info["reason"] is not None

    def test_circuit_breaker_suspension_callback(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)
        events = []
        breaker.register_suspension_callback(lambda e, r: events.append((e, r)))

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert any(e[0] == "suspended" for e in events)

    def test_circuit_breaker_pending_notification_callback(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=900)
        notifications = []
        breaker.register_pending_notification_callback(
            lambda reason, deadline: notifications.append((reason, deadline))
        )

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_pending_confirmation is True
        assert len(notifications) == 1
        assert notifications[0][1] == 900

    def test_circuit_breaker_no_pending_notification_when_skip_confirmation(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=0.5, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)
        notifications = []
        breaker.register_pending_notification_callback(
            lambda reason, deadline: notifications.append((reason, deadline))
        )

        for _ in range(5):
            monitor.update(is_severe_hallucination=True)

        assert breaker.is_suspended is True
        assert len(notifications) == 0


@pytest.mark.track_obs
class TestCUSUMSMinusThreshold:
    """CUSUM s_minus阈值触发与自动重置：率值低于目标时的反向偏差检测。"""
    def test_s_minus_exceeds_threshold_and_auto_resets(self):
        config = CUSUMConfig(target_rate=0.5, decision_threshold=0.5, delta=0.1)
        monitor = CUSUMMonitor(config)
        triggered_count = 0
        def on_alert(direction, value):
            nonlocal triggered_count
            if direction == "s_minus":
                triggered_count += 1
        monitor.register_alert_callback(on_alert)
        for _ in range(20):
            monitor.update(is_severe_hallucination=False)
        state = monitor.get_state()
        assert state["s_minus"] == 0.0
        assert triggered_count > 0

    def test_s_minus_auto_reset_allows_reaccumulation(self):
        config = CUSUMConfig(target_rate=0.5, decision_threshold=0.5, delta=0.1)
        monitor = CUSUMMonitor(config)
        for _ in range(10):
            monitor.update(is_severe_hallucination=False)
        state = monitor.get_state()
        assert state["s_minus"] == 0.0
        monitor.update(is_severe_hallucination=True)
        state_after = monitor.get_state()
        assert state_after["s_minus"] == 0.0


@pytest.mark.track_obs
class TestMonthlyHallucinationStats:
    """月度整体幻觉率统计：Warning(>3%)/Critical(>5%)告警与升级集成。"""
    def test_record_monthly_stats_no_alert(self):
        config = CUSUMConfig()
        monitor = CUSUMMonitor(config)
        alerts = monitor.record_monthly_stats(month=1, total=5000, hallucinations=100)
        assert len(alerts) == 0

    def test_record_monthly_stats_warning(self):
        config = CUSUMConfig(
            overall_hallucination_warning_rate=0.03,
            overall_hallucination_critical_rate=0.05,
        )
        monitor = CUSUMMonitor(config)
        alerts = monitor.record_monthly_stats(month=1, total=1000, hallucinations=40)
        assert "monthly_overall_hallucination_warning" in alerts

    def test_record_monthly_stats_critical(self):
        config = CUSUMConfig(
            overall_hallucination_warning_rate=0.03,
            overall_hallucination_critical_rate=0.05,
        )
        monitor = CUSUMMonitor(config)
        alerts = monitor.record_monthly_stats(month=1, total=1000, hallucinations=60)
        assert "monthly_overall_hallucination_critical" in alerts

    def test_bridge_report_monthly_overall(self):
        monitor = CUSUMMonitor()
        bridge = HallucinationCUSUMBridge(monitor)
        alerts = bridge.report_monthly_overall(month=1, total=5000, hallucinations=100)
        assert isinstance(alerts, list)


@pytest.mark.track_obs
class TestAISafetyCircuitBreakerBoundary:
    def test_circuit_breaker_at_exact_threshold(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor, confirm_seconds=0)
        k = 0.005 + 0.005 / 2
        s_plus = 0.0
        count = 0
        while s_plus <= 3.0 and count < 1000:
            result = monitor.update(is_severe_hallucination=True)
            s_plus = result["s_plus"]
            count += 1
        assert breaker.is_suspended is True

    def test_circuit_breaker_not_triggered_below_threshold(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        breaker = AISafetyCircuitBreaker(monitor)
        monitor.update(is_severe_hallucination=True)
        assert breaker.is_suspended is False
        assert breaker.is_pending_confirmation is False


@pytest.mark.track_obs
class TestWeeklyStatsWindowWarning:
    def test_small_window_logs_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="app.observability.cusum"):
            monitor = CUSUMMonitor()
            monitor.record_weekly_stats(week=1, total=100, severe=0)
        assert any("weekly_stats_window_small" in r for r in caplog.messages)

    def test_normal_window_no_warning(self, caplog):
        import logging
        with caplog.at_level(logging.WARNING, logger="app.observability.cusum"):
            monitor = CUSUMMonitor()
            monitor.record_weekly_stats(week=1, total=350, severe=0)
        assert not any("weekly_stats_window_small" in r for r in caplog.messages)


@pytest.mark.track_obs
class TestWeeklyRateExceeded:
    def test_weekly_rate_exceeded_triggers_callback(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        exceeded_calls = []
        monitor.register_weekly_rate_exceeded_callback(lambda w, r: exceeded_calls.append((w, r)))
        monitor.record_weekly_stats(week=1, total=350, severe=2)
        assert len(exceeded_calls) == 1
        assert exceeded_calls[0][0] == 1

    def test_weekly_rate_not_exceeded_no_callback(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        exceeded_calls = []
        monitor.register_weekly_rate_exceeded_callback(lambda w, r: exceeded_calls.append((w, r)))
        monitor.record_weekly_stats(week=1, total=350, severe=1)
        assert len(exceeded_calls) == 0

    def test_check_compact_rules_includes_weekly_rate(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        monitor.record_weekly_stats(week=1, total=350, severe=2)
        alerts = monitor.check_compact_rules()
        assert "weekly_rate_exceeded" in alerts


@pytest.mark.track_obs
class TestBiweeklyThresholdCorrectness:
    def test_biweekly_stats_no_alert_below_1pct(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        monitor.record_biweekly_stats(period=1, total=200, severe=1)
        assert monitor._biweekly_stats[-1]["rate"] == 0.005
        assert monitor._biweekly_stats[-1]["rate"] < 0.01

    def test_biweekly_stats_alert_above_1pct(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        monitor.record_biweekly_stats(period=1, total=100, severe=2)
        assert monitor._biweekly_stats[-1]["rate"] == 0.02
        assert monitor._biweekly_stats[-1]["rate"] > 0.01


@pytest.mark.track_obs
class TestDiseaseSuspension:
    def test_suspend_disease_independent(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        cb = AISafetyCircuitBreaker(monitor)
        cb.suspend_disease("liver", "test_reason")
        assert cb.is_disease_type_suspended("liver")
        assert not cb.is_disease_type_suspended("thyroid")
        assert cb._state.name == "ACTIVE"

    def test_resume_disease(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        cb = AISafetyCircuitBreaker(monitor)
        cb.suspend_disease("liver", "test_reason")
        assert cb.is_disease_type_suspended("liver")
        cb.resume_disease("liver")
        assert not cb.is_disease_type_suspended("liver")

    def test_global_resume_clears_disease_suspensions(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        cb = AISafetyCircuitBreaker(monitor)
        cb.suspend_disease("liver", "test_reason")
        cb.suspend_disease("thyroid", "test_reason")
        cb._request_suspension("test_global")
        cb.resume()
        assert not cb.is_disease_type_suspended("liver")
        assert not cb.is_disease_type_suspended("thyroid")
        assert cb.get_suspended_diseases() == []

    def test_get_suspended_diseases(self):
        config = CUSUMConfig(target_rate=0.005, decision_threshold=3.0, delta=0.005)
        monitor = CUSUMMonitor(config)
        cb = AISafetyCircuitBreaker(monitor)
        cb.suspend_disease("liver", "test_reason")
        cb.suspend_disease("thyroid", "test_reason")
        diseases = cb.get_suspended_diseases()
        assert "liver" in diseases
        assert "thyroid" in diseases


@pytest.mark.track_obs
class TestCreateCusumMonitorZeroValue:
    def test_create_with_zero_target_rate(self):
        monitor = create_cusum_monitor(target_rate=0.0)
        assert monitor._config.target_rate == 0.0

    def test_create_with_zero_decision_threshold(self):
        monitor = create_cusum_monitor(decision_threshold=0.0)
        assert monitor._config.decision_threshold == 0.0


@pytest.mark.track_obs
class TestMonthlyAlertCallbackIsolation:
    def test_disease_monitor_monthly_alert_does_not_trigger_global_callback(self):
        global_callback_calls = []
        disease_callback_calls = []

        global_config = CUSUMConfig(
            target_rate=0.005,
            overall_hallucination_warning_rate=0.03,
            overall_hallucination_critical_rate=0.05,
        )
        global_monitor = CUSUMMonitor(global_config)
        global_monitor.register_alert_escalation_callback(
            lambda *args: global_callback_calls.append(args)
        )

        disease_config = CUSUMConfig(
            target_rate=0.01,
            overall_hallucination_warning_rate=0.03,
            overall_hallucination_critical_rate=0.05,
        )
        disease_monitor = CUSUMMonitor(disease_config)
        disease_monitor.register_alert_escalation_callback(
            lambda *args: disease_callback_calls.append(args)
        )

        disease_monitor.record_monthly_stats(month=1, total=1000, hallucinations=60)

        assert len(disease_callback_calls) == 1
        assert len(global_callback_calls) == 0

    def test_global_monitor_monthly_alert_triggers_own_callback(self):
        global_callback_calls = []

        config = CUSUMConfig(
            target_rate=0.005,
            overall_hallucination_warning_rate=0.03,
            overall_hallucination_critical_rate=0.05,
        )
        monitor = CUSUMMonitor(config)
        monitor.register_alert_escalation_callback(
            lambda *args: global_callback_calls.append(args)
        )

        monitor.record_monthly_stats(month=1, total=1000, hallucinations=60)

        assert len(global_callback_calls) == 1
