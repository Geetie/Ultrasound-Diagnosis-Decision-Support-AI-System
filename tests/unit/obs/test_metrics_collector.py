import pytest
from app.observability.metrics_collector import MetricsCollector, VictoriaMetricsPusher
from app.observability.enums import DeploymentProfile


@pytest.fixture(autouse=True)
def reset_collector():
    MetricsCollector.reset_instance()
    yield
    MetricsCollector.reset_instance()


@pytest.mark.track_obs
class TestMetricsCollectorInit:
    def test_creates_instance_with_compact_profile(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        assert collector._profile == DeploymentProfile.COMPACT

    def test_creates_instance_with_full_profile(self):
        collector = MetricsCollector(profile=DeploymentProfile.FULL)
        assert collector._profile == DeploymentProfile.FULL

    def test_singleton_pattern(self):
        c1 = MetricsCollector.get_instance(DeploymentProfile.COMPACT)
        c2 = MetricsCollector.get_instance()
        assert c1 is c2

    def test_reset_instance(self):
        c1 = MetricsCollector.get_instance()
        MetricsCollector.reset_instance()
        c2 = MetricsCollector.get_instance()
        assert c1 is not c2


@pytest.mark.track_obs
class TestMetricsCollectorMetrics:
    def test_record_nav_latency(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.record_nav_latency(0.15)
        text = collector.get_metrics_text()
        assert "ultrasound_nav_latency_seconds" in text

    def test_record_diagnosis_latency(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.record_diagnosis_latency(3.0, with_retry=False)
        collector.record_diagnosis_latency(6.0, with_retry=True)
        text = collector.get_metrics_text()
        assert "ultrasound_diagnosis_latency_seconds" in text

    def test_set_hallucination_rates(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.set_severe_hallucination_rate(0.003)
        collector.set_overall_hallucination_rate(0.04)
        collector.set_high_risk_hallucination_rate(0.008)
        text = collector.get_metrics_text()
        assert "ultrasound_severe_hallucination_rate 0.003" in text
        assert "ultrasound_overall_hallucination_rate 0.04" in text
        assert "ultrasound_high_risk_hallucination_rate 0.008" in text

    def test_set_gpu_metrics(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.set_gpu_memory_utilization("gpu:0", 0.85)
        collector.set_gpu_fragmentation_rate("gpu:0", 0.25)
        collector.set_gpu_temperature("gpu:0", 75.0)
        text = collector.get_metrics_text()
        assert "ultrasound_gpu_memory_utilization" in text
        assert "ultrasound_gpu_fragmentation_rate" in text
        assert "ultrasound_gpu_temperature_celsius" in text

    def test_set_safety_gateway_health(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.set_safety_gateway_health(True)
        text = collector.get_metrics_text()
        assert "ultrasound_safety_gateway_health 1.0" in text

    def test_set_safety_gateway_unhealthy(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.set_safety_gateway_health(False)
        text = collector.get_metrics_text()
        assert "ultrasound_safety_gateway_health 0.0" in text

    def test_inc_adaptive_rag_route(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.inc_adaptive_rag_route("standard")
        collector.inc_adaptive_rag_route("full")
        text = collector.get_metrics_text()
        assert "ultrasound_adaptive_rag_route_total" in text

    def test_set_circuit_breaker_state(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.set_circuit_breaker_state("neo4j", True)
        collector.set_circuit_breaker_state("redis", False)
        text = collector.get_metrics_text()
        assert "ultrasound_circuit_breaker_state" in text

    def test_inc_diagnosis_requests(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        collector.inc_diagnosis_requests("thyroid", "standard")
        text = collector.get_metrics_text()
        assert "ultrasound_diagnosis_requests_total" in text

    def test_get_content_type(self):
        collector = MetricsCollector(profile=DeploymentProfile.COMPACT)
        assert "text/plain" in collector.get_content_type()
        assert "charset=utf-8" in collector.get_content_type()


@pytest.mark.track_obs
class TestVictoriaMetricsPusher:
    def test_init_default(self):
        pusher = VictoriaMetricsPusher()
        assert pusher._interval == 15.0
        assert pusher._push_count == 0
        assert pusher._fail_count == 0

    def test_get_stats(self):
        pusher = VictoriaMetricsPusher(interval=30.0)
        stats = pusher.get_stats()
        assert stats["interval"] == 30.0
        assert stats["push_count"] == 0
