"""A.88 守卫：`AppBaseException` 显式传入的 `detail` 必须真的进响应体。

## 为什么单独钉这一条

签发门禁（`app/api/v1/report.py::_assert_signable`）按 A.32 的契约抛
``BadRequestException(error_code="E1030", detail=json({"missing_blockers": [...]}))``
—— 前端要靠 ``detail.missing_blockers`` 高亮具体缺哪一段。

但 ``AppBaseException.__init__`` 里 `super().__init__(detail=error_message)`
会把 `self.detail` **覆写成错误文案**，而 `app_exception_handler` 拼响应时
读的正是 `exc.detail`（`app/api/v1/errors.py:831`）→ 结构化 detail 静默丢失。
`test_error_response_detail.py` 覆盖的是 `HTTPException` 直抛路径，
**没有**覆盖 `AppBaseException` 子类 → 这个洞一直没被钉住。

修法是"只在显式传入时保留"：不传时仍等于 error_message，
所以对既有子类的响应体零变化（本文件里两条用例分别钉这两个方向）。
"""
from __future__ import annotations

import json

from app.api.v1.errors import (
    AppBaseException,
    BadRequestException,
    NotFoundException,
    app_exception_handler,
)


class _Req:
    """最小 Request 替身：handler 只用到 url.path（且仅在 general handler 里）。"""

    class url:
        # 刻意小写：模拟 Starlette 的 Request.url 属性名。
        # 本仓库未启用 N801，故不需要也不应写行内豁免指令（会变成未使用豁免）。
        path = "/test"


def _body(exc: AppBaseException) -> dict:
    import asyncio

    response = asyncio.run(app_exception_handler(_Req(), exc))
    return json.loads(response.body)


class TestExplicitDetailSurvives:
    def test_结构化detail进入响应体(self):
        """反证腿：去掉 `if detail is not None: self.detail = detail` 这一行即红。"""
        exc = BadRequestException(
            error_code="E1030",
            error_message="报告缺少必需段落，不能签发：超声所见",
            detail=json.dumps({"missing_blockers": ["findings"]}, ensure_ascii=False),
        )

        payload = _body(exc)

        assert payload["error_code"] == "E1030"
        # error_message 仍是一句人话（不被 detail 顶掉）
        assert "不能签发" in payload["error_message"]
        # 结构化字段真的到了
        assert json.loads(payload["detail"])["missing_blockers"] == ["findings"]

    def test_detail优先于error_message而不是被它覆盖(self):
        exc = AppBaseException(
            error_code="E1001", error_message="人话文案", detail="结构化载荷"
        )

        assert exc.detail == "结构化载荷"
        assert _body(exc)["detail"] == "结构化载荷"


class TestNoDetailKeepsLegacyBehaviour:
    def test_未传detail时detail等于error_message(self):
        """零变化保证：既有子类（绝大多数不传 detail）的响应体必须一模一样。"""
        exc = NotFoundException(error_message="判读结果不存在：req_1")

        assert exc.detail == "判读结果不存在：req_1"
        payload = _body(exc)
        assert payload["detail"] == "判读结果不存在：req_1"
        assert payload["error_message"] == "判读结果不存在：req_1"

    def test_默认构造的detail仍是类默认文案(self):
        exc = NotFoundException()

        assert exc.detail == exc.error_message
