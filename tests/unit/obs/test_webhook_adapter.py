import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from httpx import AsyncClient, ASGITransport
from app.observability.webhook_adapter import (
    app,
    _format_alerts_text,
    _build_feishu_payload,
    _build_dingtalk_payload,
    _build_wecom_payload,
)


def _make_mock_async_client():
    mock = AsyncMock()
    mock.post = AsyncMock(return_value=MagicMock(status_code=200))
    return mock


@pytest.mark.track_obs
class TestFormatAlertsText:
    def test_single_alert(self):
        alerts = [{"labels": {"alertname": "HighCPU"}, "status": "firing"}]
        result = _format_alerts_text(alerts)
        assert isinstance(result, str)
        assert "HighCPU" in result

    def test_multiple_alerts(self):
        alerts = [
            {"labels": {"alertname": "HighCPU"}, "status": "firing"},
            {"labels": {"alertname": "LowMemory"}, "status": "resolved"},
        ]
        result = _format_alerts_text(alerts)
        assert "HighCPU" in result
        assert "LowMemory" in result

    def test_empty_alerts(self):
        result = _format_alerts_text([])
        assert isinstance(result, str)


@pytest.mark.track_obs
class TestBuildPayloads:
    def test_feishu_payload_structure(self):
        payload = _build_feishu_payload("alert text")
        assert isinstance(payload, dict)

    def test_dingtalk_payload_structure(self):
        payload = _build_dingtalk_payload("alert text")
        assert isinstance(payload, dict)

    def test_wecom_payload_structure(self):
        payload = _build_wecom_payload("alert text")
        assert isinstance(payload, dict)


@pytest.mark.track_obs
class TestWebhookEndpoints:
    @pytest.fixture
    async def client(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_feishu_503_when_url_not_configured(self, client):
        with patch("app.observability.webhook_adapter.FEISHU_WEBHOOK_URL", ""):
            resp = await client.post("/feishu", json={"alerts": []})
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_feishu_200_when_configured(self, client):
        mock_client = _make_mock_async_client()
        with patch("app.observability.webhook_adapter.FEISHU_WEBHOOK_URL", "https://hook.example.com"), \
             patch("app.observability.webhook_adapter.httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/feishu", json={"alerts": [{"labels": {"alertname": "Test"}, "status": "firing"}]})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_feishu_400_for_invalid_json(self, client):
        with patch("app.observability.webhook_adapter.FEISHU_WEBHOOK_URL", "https://hook.example.com"):
            resp = await client.post("/feishu", content=b"bad{json", headers={"Content-Type": "application/json"})
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_dingtalk_503_when_url_not_configured(self, client):
        with patch("app.observability.webhook_adapter.DINGTALK_WEBHOOK_URL", ""):
            resp = await client.post("/dingtalk", json={"alerts": []})
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_dingtalk_200_when_configured(self, client):
        mock_client = _make_mock_async_client()
        with patch("app.observability.webhook_adapter.DINGTALK_WEBHOOK_URL", "https://hook.example.com"), \
             patch("app.observability.webhook_adapter.httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/dingtalk", json={"alerts": [{"labels": {"alertname": "Test"}, "status": "firing"}]})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_dingtalk_400_for_invalid_json(self, client):
        with patch("app.observability.webhook_adapter.DINGTALK_WEBHOOK_URL", "https://hook.example.com"):
            resp = await client.post("/dingtalk", content=b"bad{json", headers={"Content-Type": "application/json"})
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_wecom_503_when_url_not_configured(self, client):
        with patch("app.observability.webhook_adapter.WECOM_WEBHOOK_URL", ""):
            resp = await client.post("/wecom", json={"alerts": []})
            assert resp.status_code == 503

    @pytest.mark.asyncio
    async def test_wecom_200_when_configured(self, client):
        mock_client = _make_mock_async_client()
        with patch("app.observability.webhook_adapter.WECOM_WEBHOOK_URL", "https://hook.example.com"), \
             patch("app.observability.webhook_adapter.httpx.AsyncClient", return_value=mock_client):
            resp = await client.post("/wecom", json={"alerts": [{"labels": {"alertname": "Test"}, "status": "firing"}]})
            assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_wecom_400_for_invalid_json(self, client):
        with patch("app.observability.webhook_adapter.WECOM_WEBHOOK_URL", "https://hook.example.com"):
            resp = await client.post("/wecom", content=b"bad{json", headers={"Content-Type": "application/json"})
            assert resp.status_code == 400


@pytest.mark.track_obs
class TestHealthEndpoint:
    @pytest.fixture
    async def client(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac

    @pytest.mark.asyncio
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}
