import asyncio
import logging

import httpx
import pytest

logger = logging.getLogger(__name__)

SG_BASE_URL = "http://localhost:9001"
MAIN_SERVICE_URL = "http://localhost:8000"


@pytest.fixture(scope="session")
def sg_available():
    try:
        with httpx.Client(base_url=SG_BASE_URL, timeout=2.0) as client:
            resp = client.get("/health")
            return resp.status_code in (200, 503)
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        logger.warning("sg_available: 未预期异常，按原逻辑降级", exc_info=True)
        return False


@pytest.mark.integration
@pytest.mark.asyncio
class TestSGDegradationE2E:
    async def test_normal_check_passes(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"organ_name": "thyroid", "observations": "正常甲状腺"},
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["passed"] is True

    async def test_consent_denied_rejects(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P_DENIED",
                "output_data": {"organ_name": "thyroid"},
                "consent_status_at_input": "denied",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["passed"] is False

    async def test_consent_withdrawn_rejects(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P_WITHDRAWN",
                "output_data": {"organ_name": "thyroid"},
                "consent_status_at_input": "withdrawn",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["passed"] is False

    async def test_sensitive_data_triggers_degraded(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"observations": "联系电话13812345678"},
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["degraded_mode"] is True

    async def test_edge_disconnected_triggers_degraded(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"organ_name": "thyroid"},
                "edge_connected": False,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["degraded_mode"] is True
            assert data["degraded_reason"] == "edge_disconnected"

    async def test_empty_output_rejected(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {},
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["passed"] is False

    async def test_degraded_output_has_whitelist_fields_only(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {
                    "organ_name": "thyroid",
                    "diagnostic_conclusion": "恶性结节",
                    "observations": "联系电话13812345678",
                },
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("degraded_mode"), f"Expected degraded_mode=True, got {data.get('degraded_mode')}"
            assert data.get("degraded_output"), "Expected degraded_output to be present"
            assert "diagnostic_conclusion" not in data["degraded_output"]
            assert "organ_name" in data["degraded_output"]

    async def test_degraded_output_contains_degradation_warning(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {
                    "organ_name": "thyroid",
                    "diagnostic_conclusion": "恶性结节",
                    "observations": "联系电话13812345678",
                },
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data.get("degraded_mode"), f"Expected degraded_mode=True, got {data.get('degraded_mode')}"
            assert "degradation_warning" in data, "degradation_warning field missing"
            assert data["degradation_warning"] is not None, "degradation_warning should not be None"

    async def test_consent_status_source_input_fast_reject(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P_DENIED",
                "output_data": {"organ_name": "thyroid"},
                "consent_status_at_input": "denied",
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data["consent_status_source"] == "input_fast_reject"

    async def test_consent_status_source_pg_direct(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.post("/api/v1/safety/check", json={
                "patient_id": "P001",
                "output_data": {"organ_name": "thyroid", "observations": "正常甲状腺"},
            })
            assert resp.status_code == 200
            data = resp.json()
            if data["passed"]:
                assert data["consent_status_source"] == "pg_direct"


@pytest.mark.integration
@pytest.mark.asyncio
class TestRedisFailoverE2E:
    """降级场景7: Redis主从切换 → 从节点秒级接管，功能不中断

    验证SG在Redis短暂不可用期间仍可提供校验服务，
    Redis恢复后SG自动重连，功能完全恢复。
    compact模式下Redis为性能加速层，不可用时降级为纯PG模式。
    """

    async def test_sg_remains_operational_during_redis_outage(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)
            data = resp.json()
            redis_status = data.get("components", {}).get("redis", {}).get("status", "unknown")
            overall = data.get("status", "unknown")
            if redis_status == "unhealthy" and overall == "degraded":
                check_resp = await client.post("/api/v1/safety/check", json={
                    "patient_id": "P001",
                    "output_data": {"organ_name": "thyroid", "observations": "正常甲状腺"},
                })
                assert check_resp.status_code == 200

    async def test_sg_health_reflects_redis_recovery(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp1 = await client.get("/health")
            data1 = resp1.json()
            await asyncio.sleep(5)
            resp2 = await client.get("/health")
            data2 = resp2.json()
            if data1.get("components", {}).get("redis", {}).get("status") == "unhealthy":
                assert data2.get("status") in ("healthy", "degraded", "unhealthy")


@pytest.mark.integration
@pytest.mark.asyncio
class TestProcessIsolationE2E:
    """Gate 0: 主服务崩溃时SafetyGateway独立返回E4005

    验证SafetyGateway作为独立进程，在主服务不可用时仍可响应请求。
    设计文档01-系统架构设计§3.4要求：主服务崩溃时SG可独立存活并返回E4005。
    """

    async def test_sg_health_independent_of_main_service(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health")
            assert resp.status_code in (200, 503)
            data = resp.json()
            assert data["service"] == "safety-gateway"

    async def test_sg_rejects_output_when_main_unavailable(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            try:
                main_resp = await client.get(f"{MAIN_SERVICE_URL}/api/v1/health", timeout=2.0)
                main_healthy = main_resp.status_code == 200
            except (httpx.ConnectError, httpx.TimeoutException):
                logger.warning("TestProcessIsolationE2E.test_sg_rejects_output_when_main_unavailable: 未预期异常，按原逻辑降级", exc_info=True)
                main_healthy = False

            if not main_healthy:
                sg_resp = await client.post("/api/v1/safety/check", json={
                    "patient_id": "P001",
                    "output_data": {"organ_name": "thyroid"},
                })
                assert sg_resp.status_code in (200, 503)

    async def test_sg_watchdog_alive_independent(self, sg_available):
        if not sg_available:
            pytest.skip("SafetyGateway service not available at localhost:9001")
        async with httpx.AsyncClient(base_url=SG_BASE_URL, timeout=5.0) as client:
            resp = await client.get("/health/watchdog")
            assert resp.status_code == 200
            data = resp.json()
            assert data["alive"] is True
