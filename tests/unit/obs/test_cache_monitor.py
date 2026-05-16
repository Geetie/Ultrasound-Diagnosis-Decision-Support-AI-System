import pytest
from app.observability.cache_monitor import CacheMonitor, CacheEventType


@pytest.fixture(autouse=True)
def reset_monitor():
    CacheMonitor.reset_instance()
    yield
    CacheMonitor.reset_instance()


@pytest.mark.track_obs
class TestCacheMonitorSingleton:
    def test_get_instance(self):
        m1 = CacheMonitor.get_instance()
        m2 = CacheMonitor.get_instance()
        assert m1 is m2

    def test_reset_instance(self):
        m1 = CacheMonitor.get_instance()
        CacheMonitor.reset_instance()
        m2 = CacheMonitor.get_instance()
        assert m1 is not m2


@pytest.mark.track_obs
class TestCachePenetrationMetrics:
    def test_bloom_filter_interception_rate(self):
        monitor = CacheMonitor()
        for _ in range(95):
            monitor.record_bloom_filter_query(intercepted=True)
        for _ in range(5):
            monitor.record_bloom_filter_query(intercepted=False)

        metrics = monitor.get_all_metrics()
        assert metrics["penetration"]["bloom_filter_interception_rate"] == 0.95

    def test_null_cache_hit_rate(self):
        monitor = CacheMonitor()
        monitor._penetration.total_queries = 100
        for _ in range(80):
            monitor.record_null_cache_result(hit=True)
        for _ in range(20):
            monitor.record_null_cache_result(hit=False)

        metrics = monitor.get_all_metrics()
        assert metrics["penetration"]["null_value_hit_rate"] == 0.8

    def test_db_penetration_rate(self):
        monitor = CacheMonitor()
        monitor._penetration.total_queries = 1000
        for _ in range(5):
            monitor.record_db_penetration()

        metrics = monitor.get_all_metrics()
        assert metrics["penetration"]["db_penetration_rate"] == 0.005


@pytest.mark.track_obs
class TestCacheBreakdownMetrics:
    def test_mutex_lock_success_rate(self):
        monitor = CacheMonitor()
        for _ in range(98):
            monitor.record_mutex_lock_attempt(success=True)
        for _ in range(2):
            monitor.record_mutex_lock_attempt(success=False)

        metrics = monitor.get_all_metrics()
        assert metrics["breakdown"]["mutex_lock_success_rate"] == 0.98
        assert metrics["breakdown"]["mutex_lock_timeout_count"] == 2

    def test_hotkey_permanent_coverage(self):
        monitor = CacheMonitor()
        monitor.set_hotkey_coverage(total=100, permanent=100)

        metrics = monitor.get_all_metrics()
        assert metrics["breakdown"]["hotkey_permanent_coverage"] == 1.0

    def test_hotkey_permanent_coverage_partial(self):
        monitor = CacheMonitor()
        monitor.set_hotkey_coverage(total=100, permanent=95)

        metrics = monitor.get_all_metrics()
        assert metrics["breakdown"]["hotkey_permanent_coverage"] == 0.95


@pytest.mark.track_obs
class TestCacheAvalancheMetrics:
    def test_ttl_randomization(self):
        monitor = CacheMonitor()
        monitor.set_ttl_randomization(10.0)

        metrics = monitor.get_all_metrics()
        assert metrics["avalanche"]["ttl_randomization_offset_percent"] == 10.0

    def test_warmup_completion(self):
        monitor = CacheMonitor()
        monitor.record_warmup(total_keys=100, warmed_keys=100, duration_seconds=15.0)

        metrics = monitor.get_all_metrics()
        assert metrics["avalanche"]["warmup_completion_rate"] == 1.0
        assert metrics["avalanche"]["warmup_duration_seconds"] == 15.0

    def test_warmup_partial_failure(self):
        monitor = CacheMonitor()
        monitor.record_warmup(total_keys=100, warmed_keys=80, duration_seconds=20.0)

        metrics = monitor.get_all_metrics()
        assert metrics["avalanche"]["warmup_completion_rate"] == 0.8


@pytest.mark.track_obs
class TestCacheOverallMetrics:
    def test_cache_hit_rate(self):
        monitor = CacheMonitor()
        for _ in range(70):
            monitor.record_cache_read(hit=True, latency_seconds=0.001)
        for _ in range(30):
            monitor.record_cache_read(hit=False, latency_seconds=0.01)

        metrics = monitor.get_all_metrics()
        assert metrics["overall"]["overall_hit_rate"] == 0.7

    def test_cache_write_latency(self):
        monitor = CacheMonitor()
        for i in range(100):
            monitor.record_cache_write(latency_seconds=0.001 * (i + 1))

        metrics = monitor.get_all_metrics()
        assert metrics["overall"]["write_latency_p99_seconds"] > 0

    def test_cache_read_latency(self):
        monitor = CacheMonitor()
        for i in range(100):
            monitor.record_cache_read(hit=True, latency_seconds=0.001 * (i + 1))

        metrics = monitor.get_all_metrics()
        assert metrics["overall"]["read_latency_p99_seconds"] > 0


@pytest.mark.track_obs
class TestCacheAlerts:
    def test_no_alerts_when_healthy(self):
        monitor = CacheMonitor()
        for _ in range(80):
            monitor.record_cache_read(hit=True, latency_seconds=0.001)
        for _ in range(20):
            monitor.record_cache_read(hit=False, latency_seconds=0.01)

        alerts = monitor.check_alerts()
        assert len(alerts) == 0

    def test_critical_alert_low_hit_rate(self):
        monitor = CacheMonitor()
        for _ in range(30):
            monitor.record_cache_read(hit=True, latency_seconds=0.001)
        for _ in range(70):
            monitor.record_cache_read(hit=False, latency_seconds=0.01)

        alerts = monitor.check_alerts()
        assert any(a["metric"] == "cache_hit_rate" and a["level"] == "critical" for a in alerts)

    def test_warning_alert_moderate_hit_rate(self):
        monitor = CacheMonitor()
        for _ in range(55):
            monitor.record_cache_read(hit=True, latency_seconds=0.001)
        for _ in range(45):
            monitor.record_cache_read(hit=False, latency_seconds=0.01)

        alerts = monitor.check_alerts()
        assert any(a["metric"] == "cache_hit_rate" and a["level"] == "warning" for a in alerts)

    def test_critical_alert_high_db_penetration(self):
        monitor = CacheMonitor()
        monitor._penetration.total_queries = 100
        for _ in range(15):
            monitor.record_db_penetration()

        alerts = monitor.check_alerts()
        assert any(a["metric"] == "db_penetration_rate" and a["level"] == "critical" for a in alerts)

    def test_critical_alert_simultaneous_expiry(self):
        monitor = CacheMonitor()
        for _ in range(110):
            monitor.record_key_expiry()

        alerts = monitor.check_alerts()
        assert any(a["metric"] == "simultaneous_expiry_5min" for a in alerts)


@pytest.mark.track_obs
class TestCacheEventType:
    def test_event_types(self):
        assert CacheEventType.BLOOM_FILTER_LOADED == "bloom_filter_loaded"
        assert CacheEventType.NULL_CACHE_WRITE == "null_cache_write"
        assert CacheEventType.MUTEX_LOCK_FAILED == "mutex_lock_failed"
        assert CacheEventType.WARMUP_COMPLETED == "warmup_completed"
        assert CacheEventType.WARMUP_FAILED == "warmup_failed"
        assert CacheEventType.TTL_RANDOMIZATION_CHECK == "ttl_randomization_check"


@pytest.mark.track_obs
class TestCacheP99SmallData:
    def test_p99_with_single_value(self):
        monitor = CacheMonitor()
        result = monitor._calculate_p99([0.005])
        assert abs(result - 0.005) < 1e-10

    def test_p99_with_two_values(self):
        monitor = CacheMonitor()
        result = monitor._calculate_p99([0.001, 0.010])
        assert 0.001 <= result <= 0.010

    def test_p99_with_empty_data(self):
        monitor = CacheMonitor()
        result = monitor._calculate_p99([])
        assert result == 0.0

    def test_p99_with_100_values(self):
        monitor = CacheMonitor()
        data = [float(i) for i in range(100)]
        result = monitor._calculate_p99(data)
        assert result >= 98.0


@pytest.mark.track_obs
class TestCacheWarmupLogLevel:
    def test_warmup_complete_failure_error(self, caplog):
        import logging
        monitor = CacheMonitor()
        with caplog.at_level(logging.ERROR, logger="app.observability.cache_monitor"):
            monitor.record_warmup(total_keys=100, warmed_keys=0, duration_seconds=5.0)
        assert any("cache_warmup_failed" in r for r in caplog.messages)

    def test_warmup_partial_warning(self, caplog):
        import logging
        monitor = CacheMonitor()
        with caplog.at_level(logging.WARNING, logger="app.observability.cache_monitor"):
            monitor.record_warmup(total_keys=100, warmed_keys=95, duration_seconds=5.0)
        assert any("cache_warmup_partial" in r for r in caplog.messages)

    def test_warmup_complete_success_info(self, caplog):
        import logging
        monitor = CacheMonitor()
        with caplog.at_level(logging.INFO, logger="app.observability.cache_monitor"):
            monitor.record_warmup(total_keys=100, warmed_keys=100, duration_seconds=5.0)
        assert any("cache_warmup_completed" in r for r in caplog.messages)


@pytest.mark.track_obs
class TestCacheAlertEscalation:
    def test_alert_escalation_callback_called(self):
        monitor = CacheMonitor()
        callback_calls = []
        monitor.register_alert_escalation_callback(lambda a: callback_calls.append(a))
        monitor._overall.overall_hit_rate = 0.3
        monitor._prometheus_dirty = True
        alerts = monitor.check_alerts_and_escalate()
        assert len(callback_calls) > 0

    def test_no_alert_no_callback(self):
        monitor = CacheMonitor()
        callback_calls = []
        monitor.register_alert_escalation_callback(lambda a: callback_calls.append(a))
        monitor._overall.overall_hit_rate = 0.8
        monitor._prometheus_dirty = True
        alerts = monitor.check_alerts_and_escalate()
        hit_rate_alerts = [a for a in callback_calls if a.get("metric") == "overall_hit_rate"]
        assert len(hit_rate_alerts) == 0
