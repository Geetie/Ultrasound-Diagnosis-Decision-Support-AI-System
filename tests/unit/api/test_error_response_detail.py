"""守卫：`HTTPException.detail` 可以是 dict（健康检查就这么用），处理器不能因此自崩。

## 为什么需要它（2026-09-28 真机部署暴露）

`/api/v1/health` 在 unhealthy 时抛 `HTTPException(503, detail=<健康报告 dict>)`。
原处理器把 detail 直接塞进 `ErrorResponse.error_message`（`str` 字段）→
**处理器自己抛 pydantic ValidationError** → 响应根本没机会发出，异常再穿过
`BaseHTTPMiddleware`（rate_limit）的 task group 抛出 ExceptionGroup
→ 客户端看到的是**连接被重置**（curl `http=000`），而不是 503 + 健康报告。

真机症状极具误导性："端口在 LISTEN，但任何非 2xx 请求都像被黑洞吞掉"。
"""
from __future__ import annotations

import json

import pytest
from starlette.exceptions import HTTPException

from app.api.v1.errors import http_exception_handler


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


class TestDictDetail:
    @pytest.mark.asyncio
    async def test_结构化detail不再让处理器崩溃(self):
        health_report = {
            "status": "unhealthy",
            "services": {"redis": "unhealthy", "database": "healthy"},
            "redis_error": "TimeoutError: ",
        }

        response = await http_exception_handler(
            None, HTTPException(status_code=503, detail=health_report)
        )

        assert response.status_code == 503
        payload = _body(response)
        assert payload["error_code"] == "E4005"
        # error_message 是给人看的一句话；结构化内容进 detail
        assert payload["error_message"] == "Service Unavailable"
        assert json.loads(payload["detail"])["redis_error"] == "TimeoutError: "

    @pytest.mark.asyncio
    async def test_字符串detail保持原行为(self):
        response = await http_exception_handler(
            None, HTTPException(status_code=400, detail="参数不合法")
        )

        payload = _body(response)

        assert payload["error_message"] == "参数不合法"
        assert payload["detail"] == "参数不合法"

    @pytest.mark.asyncio
    async def test_无detail时给兜底文案(self):
        response = await http_exception_handler(None, HTTPException(status_code=404))

        payload = _body(response)

        assert payload["error_code"] == "E1002"
        assert payload["error_message"] == "Not Found"

    @pytest.mark.asyncio
    async def test_不可序列化对象也不会二次崩溃(self):
        class _Weird:
            def __str__(self) -> str:
                return "weird-repr"

        response = await http_exception_handler(
            None, HTTPException(status_code=500, detail=_Weird())
        )

        assert response.status_code == 500
        assert "weird-repr" in _body(response)["detail"]
