"""请求级 request_id 注入（15-技术升级规划 P0-1.5）。

## 为什么需要它

2026-09-28 代码实证（05-可观测性 附录A §7）：全项目此前**没有任何 request_id
生成点** —— `ErrorResponse.request_id` 字段恒为 None（错误处理器从不填），
跨组件排障只能靠时间戳对齐。

本模块补齐链路：
1. 最外层中间件为每个 HTTP 请求生成/透传 `X-Request-ID`（响应头回写）；
2. 同步写入 structlog contextvars —— 请求内**所有日志**自动携带 request_id
   （依赖 storage.py 处理器链中的 `merge_contextvars`）；
3. 错误处理器（errors.py）从 contextvar 取值填入 `ErrorResponse.request_id`。

## 安全约束

- 透传上游 `X-Request-ID` 时必须匹配白名单字符集（≤64 位 `[0-9a-zA-Z._-]`），
  否则重新生成 —— 防止上游注入超长/控制字符进响应头与日志。
"""
from __future__ import annotations

import re
import uuid
from contextvars import ContextVar, Token

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)

_REQUEST_ID_HEADER = "x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[0-9a-zA-Z._-]{1,64}$")


def get_current_request_id() -> str | None:
    return current_request_id.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        incoming = request.headers.get(_REQUEST_ID_HEADER, "")
        request_id = incoming if _SAFE_REQUEST_ID.match(incoming) else uuid.uuid4().hex

        token: Token = current_request_id.set(request_id)
        structlog.contextvars.bind_contextvars(request_id=request_id)
        try:
            response = await call_next(request)
        finally:
            current_request_id.reset(token)
            structlog.contextvars.unbind_contextvars("request_id")

        response.headers[_REQUEST_ID_HEADER] = request_id
        return response
