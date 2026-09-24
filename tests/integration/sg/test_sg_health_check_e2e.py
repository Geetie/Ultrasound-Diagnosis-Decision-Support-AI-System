import httpx
import pytest

SG_BASE_URL = "http://localhost:9001"


@pytest.fixture(scope="session")
def sg_available():
    try:
        with httpx.Client(base_url=SG_BASE_URL, timeout=2.0) as client:
            resp = client.get("/health")
            return resp.status_code in (200, 503)
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return False


@pytest.mark.integration
@pytest.mark.asyncio
class TestSGHealthCheckE2E:
    async def test_root_endpoint(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/")
            assert resp.status_code == 200
            data = resp.json()
            assert data["service"] == "safety-gateway"
            assert data["status"] == "running"

    async def test_health_endpoint_structure(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)
            data = resp.json()
            assert "status" in data
            assert "components" in data
            assert "latency_stats" in data

    async def test_health_ready_endpoint(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health/ready")
            assert resp.status_code in (200, 503)
            data = resp.json()
            assert "status" in data

    async def test_health_watchdog_endpoint(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health/watchdog")
            assert resp.status_code == 200
            data = resp.json()
            assert "alive" in data
            assert "p99_status" in data
            assert "timestamp" in data

    async def test_health_unhealthy_contains_e4005a(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health")
            data = resp.json()
            if data["status"] == "unhealthy":
                assert "error_code" in data, "error_code field missing in unhealthy response"
                assert data["error_code"] == "E4005A", f"Expected E4005A, got {data['error_code']}"
            else:
                pytest.skip("SG service is healthy, cannot verify E4005A in unhealthy response")
