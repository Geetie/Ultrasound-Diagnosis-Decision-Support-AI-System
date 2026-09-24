"""安全响应头中间件（T-G1-INT-04：CSP 合规）。

背景：主服务此前**没有挂载任何安全响应头**（只有 CORS 与限流中间件），
浏览器侧的点击劫持、MIME 嗅探与脚本注入防护完全缺失，CSP 更无从谈起。

实现为纯 ASGI 中间件（不用 ``BaseHTTPMiddleware``）：后者会引入额外的
任务与上下文拷贝，对 WebSocket/流式响应也更容易出问题；本中间件只改写
``http.response.start`` 消息，开销可忽略。

CSP 取值依据本项目实际形态（前端由 Vite 打包、antd 运行时注入样式、
双通道 WebSocket、DICOM 用 canvas 渲染、语音需麦克风）：

- ``script-src 'self'``：无内联脚本，因此**不需要** ``unsafe-inline``/``unsafe-eval``；
- ``style-src 'self' 'unsafe-inline'``：antd 的 CSS-in-JS 需要内联样式；
- ``connect-src 'self' ws: wss:``：导航/影像双 WebSocket 通道；
- ``img-src 'self' data: blob:`` / ``media-src 'self' blob:``：DICOM 与录音回放；
- ``object-src 'none'``、``base-uri 'self'``、``frame-ancestors 'none'``：
  防插件注入、base 标签劫持与点击劫持。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.security.security_headers")


CSP_DIRECTIVES: Dict[str, Iterable[str]] = {
    "default-src": ("'self'",),
    "script-src": ("'self'",),
    "style-src": ("'self'", "'unsafe-inline'"),
    "img-src": ("'self'", "data:", "blob:"),
    "font-src": ("'self'", "data:"),
    "media-src": ("'self'", "blob:"),
    "connect-src": ("'self'", "ws:", "wss:"),
    "worker-src": ("'self'", "blob:"),
    "object-src": ("'none'",),
    "base-uri": ("'self'",),
    "frame-ancestors": ("'none'",),
    "form-action": ("'self'",),
}

# 恒定安全头（与 CSP 互补）
STATIC_SECURITY_HEADERS: Dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
    # 语音交互需要麦克风；摄像头/定位与本系统无关，显式关闭
    "Permissions-Policy": "microphone=(self), camera=(), geolocation=(), payment=()",
}

# 患者数据相关响应禁止缓存（诊断结果、报告、审计与 FHIR 读取）
NO_STORE_PREFIXES = (
    "/api/v1/diagnosis",
    "/api/v1/report",
    "/api/v1/sessions",
    "/api/v1/consent",
    "/api/v1/security",
    "/api/v1/internal",
    "/fhir",
)


def build_csp_policy(directives: Optional[Dict[str, Iterable[str]]] = None) -> str:
    """拼装 Content-Security-Policy 字符串（纯函数，便于单测）。"""
    source = directives or CSP_DIRECTIVES
    return "; ".join(
        f"{name} {' '.join(values)}" for name, values in source.items()
    )


class SecurityHeadersMiddleware:
    """给所有 HTTP 响应补充安全响应头。

    Args:
        app: 下游 ASGI 应用。
        csp_policy: 自定义 CSP；缺省用 :func:`build_csp_policy`。
        report_only: 为 True 时只上报不拦截（``Content-Security-Policy-Report-Only``），
            仅用于灰度观察，生产应保持 False。
        hsts_max_age: 大于 0 时下发 HSTS（仅应在 HTTPS 入口启用）。
    """

    def __init__(
        self,
        app: Any,
        *,
        csp_policy: Optional[str] = None,
        report_only: bool = False,
        hsts_max_age: int = 0,
    ) -> None:
        self.app = app
        self.csp_policy = csp_policy or build_csp_policy()
        self.report_only = report_only
        self.hsts_max_age = hsts_max_age
        self.csp_header_name = (
            b"content-security-policy-report-only"
            if report_only
            else b"content-security-policy"
        )

    def _headers_for(self, path: str) -> Dict[str, str]:
        headers = dict(STATIC_SECURITY_HEADERS)
        headers["Content-Security-Policy"] = self.csp_policy
        if self.report_only:
            headers.pop("Content-Security-Policy")
            headers["Content-Security-Policy-Report-Only"] = self.csp_policy
        if self.hsts_max_age > 0:
            headers["Strict-Transport-Security"] = (
                f"max-age={self.hsts_max_age}; includeSubDomains"
            )
        if any(path.startswith(prefix) for prefix in NO_STORE_PREFIXES):
            headers["Cache-Control"] = "no-store"
            headers["Pragma"] = "no-cache"
        return headers

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            # WebSocket 握手响应由 uvicorn 处理，这里不介入
            await self.app(scope, receive, send)
            return

        headers = self._headers_for(scope.get("path", ""))
        normalized = {name.lower().encode(): value.encode() for name, value in headers.items()}

        async def send_with_headers(message: Dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                raw = list(message.get("headers") or [])
                present = {key.lower() for key, _ in raw}
                for key, value in normalized.items():
                    if key not in present:
                        raw.append((key, value))
                message["headers"] = raw
            await send(message)

        await self.app(scope, receive, send_with_headers)


def create_security_headers_middleware(app: Any) -> SecurityHeadersMiddleware:
    """按 ``settings`` 构造中间件（便于在 main 中一行挂载）。"""
    return SecurityHeadersMiddleware(
        app,
        csp_policy=settings.csp_policy or None,
        report_only=settings.csp_report_only,
        hsts_max_age=settings.hsts_max_age_seconds if settings.hsts_enabled else 0,
    )


__all__ = [
    "CSP_DIRECTIVES",
    "NO_STORE_PREFIXES",
    "STATIC_SECURITY_HEADERS",
    "SecurityHeadersMiddleware",
    "build_csp_policy",
    "create_security_headers_middleware",
]
