"""RequestIdMiddleware 行为测试（15-技术升级规划 P0-1.5）。

覆盖：响应头回写、上游合法 id 透传、非法 id 重新生成、错误响应体携带
request_id、contextvar 在请求结束后清理。
"""
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.errors import http_exception_handler
from app.observability.request_id import (
    RequestIdMiddleware,
    current_request_id,
    get_current_request_id,
)

pytestmark = [pytest.mark.track_obs]


def _build_app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(RequestIdMiddleware)

    @app.get("/echo-id")
    async def echo_id():
        return {"request_id": get_current_request_id()}

    @app.get("/raise-http")
    async def raise_http():
        raise HTTPException(status_code=404, detail="not found")

    app.add_exception_handler(HTTPException, http_exception_handler)
    return app


@pytest.fixture()
def client():
    return TestClient(_build_app(), raise_server_exceptions=False)


class TestResponseHeader:
    def test_generates_request_id_header(self, client):
        resp = client.get("/echo-id")
        assert resp.status_code == 200
        rid = resp.headers["x-request-id"]
        assert rid and len(rid) == 32
        assert all(c in "0123456789abcdef" for c in rid)

    def test_passes_through_valid_upstream_id(self, client):
        resp = client.get("/echo-id", headers={"X-Request-ID": "frontend-abc.123"})
        assert resp.headers["x-request-id"] == "frontend-abc.123"

    def test_rejects_oversized_upstream_id(self, client):
        bad = "x" * 65
        resp = client.get("/echo-id", headers={"X-Request-ID": bad})
        rid = resp.headers["x-request-id"]
        assert rid != bad
        assert len(rid) == 32


class TestContextvar:
    def test_handler_sees_request_id(self, client):
        body = client.get("/echo-id").json()
        assert body["request_id"]

    def test_contextvar_cleared_after_request(self, client):
        client.get("/echo-id")
        assert current_request_id.get() is None


class TestErrorResponses:
    def test_http_exception_body_carries_request_id(self, client):
        resp = client.get("/raise-http")
        assert resp.status_code == 404
        header_rid = resp.headers["x-request-id"]
        body_rid = resp.json()["request_id"]
        assert body_rid == header_rid
        assert body_rid
