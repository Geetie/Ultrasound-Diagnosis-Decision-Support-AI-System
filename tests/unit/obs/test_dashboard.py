from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.observability.dashboard import router


@pytest.fixture()
def app_client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def mock_collector():
    collector = MagicMock()
    collector.get_gauge_value = MagicMock(return_value=0.0)
    collector.get_histogram_p99 = MagicMock(return_value=0.0)
    collector.gpu_memory_utilization = None
    return collector


@pytest.fixture()
def mock_cache_monitor():
    monitor = MagicMock()
    monitor.get_all_metrics = MagicMock(return_value={
        "penetration": {"bloom_filter_interception_rate": 0.95},
        "breakdown": {"mutex_lock_success_rate": 0.98},
        "avalanche": {"warmup_completion_rate": 1.0},
        "overall": {"overall_hit_rate": 0.85},
    })
    return monitor


@pytest.mark.track_obs
class TestSummaryEndpoint:
    def test_returns_all_sections(self, app_client, mock_collector, mock_cache_monitor):
        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=mock_cache_monitor):
            resp = app_client.get("/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert "gpu" in data
        assert "hallucination" in data
        assert "latency" in data
        assert "cache" in data
        assert "infra" in data

    def test_degradation_on_collector_exception(self, app_client, mock_cache_monitor):
        broken = MagicMock()
        broken.get_gauge_value = MagicMock(side_effect=RuntimeError("collector down"))
        broken.get_histogram_p99 = MagicMock(side_effect=RuntimeError("collector down"))
        broken.gpu_memory_utilization = None

        with patch("app.observability.dashboard.get_metrics_collector", return_value=broken), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=mock_cache_monitor):
            resp = app_client.get("/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["hallucination"] == {}
        assert data["latency"] == {}
        assert data["infra"] == {}

    def test_degradation_on_cache_monitor_exception(self, app_client, mock_collector):
        broken = MagicMock()
        broken.get_all_metrics = MagicMock(side_effect=RuntimeError("cache monitor down"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=broken):
            resp = app_client.get("/summary")

        assert resp.status_code == 200
        data = resp.json()
        assert data["cache"] == {}


@pytest.mark.track_obs
class TestGpuEndpoint:
    def test_returns_gpu_structure(self, app_client, mock_collector):
        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector):
            resp = app_client.get("/gpu")

        assert resp.status_code == 200
        data = resp.json()
        assert "memory_utilization" in data
        assert "fragmentation_rate" in data
        assert "temperature" in data
        assert "overheat_duration_seconds" in data
        assert "fragmentation_duration_seconds" in data

    def test_degradation_on_exception(self, app_client):
        broken = MagicMock()
        broken.gpu_memory_utilization = MagicMock(side_effect=RuntimeError("gpu error"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=broken):
            resp = app_client.get("/gpu")

        assert resp.status_code == 200
        data = resp.json()
        assert all(v == {} for v in data.values())


@pytest.mark.track_obs
class TestHallucinationEndpoint:
    def test_returns_hallucination_structure(self, app_client, mock_collector):
        mock_collector.get_gauge_value = MagicMock(side_effect=lambda name: {
            "severe_hallucination_rate": 0.003,
            "overall_hallucination_rate": 0.04,
            "high_risk_hallucination_rate": 0.008,
            "retrieval_empty_rate": 0.01,
        }.get(name, 0.0))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector):
            resp = app_client.get("/hallucination")

        assert resp.status_code == 200
        data = resp.json()
        assert "severe_rate" in data
        assert "overall_rate" in data
        assert "high_risk_rate" in data
        assert "retrieval_empty_rate" in data
        assert data["severe_rate"] == 0.003
        assert data["overall_rate"] == 0.04

    def test_degradation_on_exception(self, app_client):
        broken = MagicMock()
        broken.get_gauge_value = MagicMock(side_effect=RuntimeError("hallucination error"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=broken):
            resp = app_client.get("/hallucination")

        assert resp.status_code == 200
        assert resp.json() == {}


@pytest.mark.track_obs
class TestLatencyEndpoint:
    def test_returns_latency_structure(self, app_client, mock_collector):
        mock_collector.get_histogram_p99 = MagicMock(side_effect=lambda name: {
            "nav_latency": 0.15,
            "assessment_latency": 0.20,
            "diagnosis_latency": 3.0,
            "multi_organ_latency": 5.0,
            "edge_nav_latency_seconds": 0.08,
        }.get(name, 0.0))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector):
            resp = app_client.get("/latency")

        assert resp.status_code == 200
        data = resp.json()
        assert "nav_p99_seconds" in data
        assert "assessment_p99_seconds" in data
        assert "diagnosis_p99_seconds" in data
        assert "multi_organ_p99_seconds" in data
        assert "edge_nav_p99_seconds" in data
        assert data["nav_p99_seconds"] == 0.15
        assert data["diagnosis_p99_seconds"] == 3.0

    def test_degradation_on_exception(self, app_client):
        broken = MagicMock()
        broken.get_histogram_p99 = MagicMock(side_effect=RuntimeError("latency error"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=broken):
            resp = app_client.get("/latency")

        assert resp.status_code == 200
        assert resp.json() == {}


@pytest.mark.track_obs
class TestCacheEndpoint:
    def test_returns_cache_structure(self, app_client, mock_collector, mock_cache_monitor):
        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=mock_cache_monitor):
            resp = app_client.get("/cache")

        assert resp.status_code == 200
        data = resp.json()
        assert "hit_rate" in data
        assert "bloom_filter_interception_rate" in data
        assert "null_value_hit_rate" in data
        assert "db_penetration_rate" in data
        assert "mutex_lock_success_rate" in data
        assert "mutex_lock_timeout_count" in data
        assert "hotkey_permanent_coverage" in data
        assert "simultaneous_expiry_5min" in data
        assert "warmup_completion_rate" in data
        assert "write_latency_p99_seconds" in data
        assert "read_latency_p99_seconds" in data
        assert "penetration" in data
        assert "breakdown" in data
        assert "avalanche" in data
        assert "overall" in data

    def test_cache_includes_monitor_data(self, app_client, mock_collector, mock_cache_monitor):
        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=mock_cache_monitor):
            resp = app_client.get("/cache")

        data = resp.json()
        assert data["penetration"] == {"bloom_filter_interception_rate": 0.95}
        assert data["breakdown"] == {"mutex_lock_success_rate": 0.98}
        assert data["avalanche"] == {"warmup_completion_rate": 1.0}
        assert data["overall"] == {"overall_hit_rate": 0.85}

    def test_degradation_on_collector_exception(self, app_client, mock_cache_monitor):
        broken = MagicMock()
        broken.get_gauge_value = MagicMock(side_effect=RuntimeError("collector down"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=broken), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=mock_cache_monitor):
            resp = app_client.get("/cache")

        assert resp.status_code == 200
        assert resp.json() == {}

    def test_degradation_on_cache_monitor_exception(self, app_client, mock_collector):
        broken = MagicMock()
        broken.get_all_metrics = MagicMock(side_effect=RuntimeError("cache monitor down"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector), \
             patch("app.observability.dashboard.get_cache_monitor", return_value=broken):
            resp = app_client.get("/cache")

        assert resp.status_code == 200
        assert resp.json() == {}


@pytest.mark.track_obs
class TestInfraEndpoint:
    def test_returns_infra_structure(self, app_client, mock_collector):
        mock_collector.get_gauge_value = MagicMock(side_effect=lambda name: {
            "service_health_rate": 0.99,
            "safety_gateway_health": 1.0,
            "redis_process_alive": 1.0,
            "pg_connection_pool_utilization": 0.45,
            "victoriametrics_process_resident_memory_bytes": 536870912.0,
        }.get(name, 0.0))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=mock_collector):
            resp = app_client.get("/infra")

        assert resp.status_code == 200
        data = resp.json()
        assert "service_health_rate" in data
        assert "safety_gateway_healthy" in data
        assert "redis_alive" in data
        assert "pg_connection_pool_utilization" in data
        assert "victoriametrics_rss_bytes" in data
        assert data["service_health_rate"] == 0.99
        assert data["safety_gateway_healthy"] == 1.0
        assert data["redis_alive"] == 1.0

    def test_degradation_on_exception(self, app_client):
        broken = MagicMock()
        broken.get_gauge_value = MagicMock(side_effect=RuntimeError("infra error"))

        with patch("app.observability.dashboard.get_metrics_collector", return_value=broken):
            resp = app_client.get("/infra")

        assert resp.status_code == 200
        assert resp.json() == {}
