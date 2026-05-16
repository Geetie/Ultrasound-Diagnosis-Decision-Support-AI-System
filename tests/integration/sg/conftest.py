"""SG集成测试专用配置"""
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
