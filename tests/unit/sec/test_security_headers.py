"""T-G1-INT-04 安全响应头 / CSP 测试。

覆盖：
- CSP 策略拼装（含"不得出现 unsafe-eval"的硬约束）；
- 纯 ASGI 中间件给 HTTP 响应补头、且不覆盖路由已设置的同名头；
- 患者数据类响应禁缓存、静态资源不误加；
- WebSocket 作用域不介入；
- 报告模式下改用 Report-Only 头；
- 主应用确实挂载了该中间件（防止回调回归）。
"""
from __future__ import annotations

from typing import Any

import pytest

from app.security.security_headers import (
    CSP_DIRECTIVES,
    NO_STORE_PREFIXES,
    STATIC_SECURITY_HEADERS,
    SecurityHeadersMiddleware,
    build_csp_policy,
    create_security_headers_middleware,
)


async def _call_middleware(
    path: str,
    *,
    middleware_factory=SecurityHeadersMiddleware,
    inner_headers: list[tuple[bytes, bytes]] | None = None,
) -> dict[bytes, bytes]:
    """用一个回显下游应用驱动中间件，返回最终响应头。"""
    captured: dict[str, Any] = {}

    async def inner(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": list(inner_headers or [(b"content-type", b"application/json")]),
            }
        )
        await send({"type": "http.response.body", "body": b"{}"})

    async def send(message):
        if message["type"] == "http.response.start":
            captured["headers"] = dict(message["headers"])

    middleware = middleware_factory(inner)
    await middleware({"type": "http", "path": path, "headers": []}, None, send)
    return captured["headers"]


class TestCspPolicy:
    def test_default_policy_has_hardening_directives(self):
        policy = build_csp_policy()
        assert "default-src 'self'" in policy
        assert "object-src 'none'" in policy
        assert "base-uri 'self'" in policy
        assert "frame-ancestors 'none'" in policy

    def test_no_unsafe_eval_or_inline_script(self):
        policy = build_csp_policy()
        assert "unsafe-eval" not in policy
        script_src = " ".join(CSP_DIRECTIVES["script-src"])
        assert "unsafe-inline" not in script_src

    def test_inline_styles_allowed_for_css_in_js(self):
        # antd 运行时注入样式，必须放开内联样式，否则界面会整体错乱
        assert "'unsafe-inline'" in " ".join(CSP_DIRECTIVES["style-src"])

    def test_websocket_and_blob_sources_present(self):
        policy = build_csp_policy()
        assert "ws:" in policy and "wss:" in policy
        assert "blob:" in policy

    def test_custom_directives_override(self):
        policy = build_csp_policy({"default-src": ("'none'",)})
        assert policy == "default-src 'none'"


class TestMiddlewareHeaders:
    @pytest.mark.asyncio
    async def test_adds_static_headers_and_csp(self):
        headers = await _call_middleware("/api/v1/health")
        assert headers[b"content-security-policy"].decode() == build_csp_policy()
        for name, value in STATIC_SECURITY_HEADERS.items():
            assert headers[name.lower().encode()].decode() == value

    @pytest.mark.asyncio
    async def test_patient_data_responses_are_not_cached(self):
        for prefix in NO_STORE_PREFIXES:
            headers = await _call_middleware(f"{prefix}/x")
            assert headers[b"cache-control"] == b"no-store", prefix

    @pytest.mark.asyncio
    async def test_static_assets_keep_cacheable(self):
        headers = await _call_middleware("/assets/index-abc.js")
        assert b"cache-control" not in headers

    @pytest.mark.asyncio
    async def test_does_not_override_route_headers(self):
        headers = await _call_middleware(
            "/api/v1/health",
            inner_headers=[(b"content-security-policy", b"default-src 'none'")],
        )
        assert headers[b"content-security-policy"] == b"default-src 'none'"

    @pytest.mark.asyncio
    async def test_report_only_mode(self):
        headers = await _call_middleware(
            "/api/v1/health",
            middleware_factory=lambda app: SecurityHeadersMiddleware(app, report_only=True),
        )
        assert b"content-security-policy" not in headers
        assert headers[b"content-security-policy-report-only"].decode() == build_csp_policy()

    @pytest.mark.asyncio
    async def test_hsts_only_when_enabled(self):
        headers = await _call_middleware("/api/v1/health")
        assert b"strict-transport-security" not in headers

        headers = await _call_middleware(
            "/api/v1/health",
            middleware_factory=lambda app: SecurityHeadersMiddleware(
                app, hsts_max_age=31536000
            ),
        )
        assert b"max-age=31536000" in headers[b"strict-transport-security"]

    @pytest.mark.asyncio
    async def test_websocket_scope_passes_through(self):
        seen: dict[str, Any] = {}

        async def inner(scope, receive, send):
            seen["scope_type"] = scope["type"]
            await send({"type": "websocket.accept"})

        sent: list[dict[str, Any]] = []

        async def send(message):
            sent.append(message)

        middleware = SecurityHeadersMiddleware(inner)
        await middleware({"type": "websocket", "path": "/api/v1/realtime/nav"}, None, send)

        assert seen["scope_type"] == "websocket"
        assert sent == [{"type": "websocket.accept"}]

    @pytest.mark.asyncio
    async def test_permissions_policy_allows_microphone(self):
        headers = await _call_middleware("/api/v1/health")
        policy = headers[b"permissions-policy"].decode()
        assert "microphone=(self)" in policy
        assert "camera=()" in policy


class TestFactoryAndWiring:
    def test_factory_reads_settings(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "csp_policy", "default-src 'none'", raising=False)
        middleware = create_security_headers_middleware(lambda *a: None)
        assert middleware.csp_policy == "default-src 'none'"

    def test_main_app_mounts_middleware(self):
        """防止中间件被误删（CI 的静态 CSP 检查同样覆盖这一点）。"""
        from app.main import app

        names = [m.cls.__name__ for m in app.user_middleware if hasattr(m, "cls")]
        assert "SecurityHeadersMiddleware" in names
