"""T2a 分类器客户端（``app/inference/t2a_classifier.py``）单测 + 反证腿。

## 钉住的三条不变量

1. **失败原因必须分开**（AGENTS.md 硬规则 8）："取不到影像" / "服务没起" /
   "服务起了但模型没加载" / "返回体不可解析" 是四种不同的事，聚合处不许写死
   成同一个默认值。反证腿：把任一分支改成统一的 ``service_unavailable`` 即红。
2. **永不抛异常**：它是旁路，任何异常都必须变成 ``available=False + reason``，
   不许把 8003 的抖动传染给主判读。
3. **不编造概率**：响应体形状不符时报 ``invalid_response``，不许默认 0/0.5，
   更不许把"解析失败"说成"良性"。

## 为什么不用真实 8003

本文件全部走 ``httpx.MockTransport``：`/v1/classify` 只在真机 GPU 服务器上存在
（🔬 真机项，见 ``docs/合规/覆盖率豁免清单.json`` 口径），单测只钉"客户端行为"。
"""
from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.inference import t2a_classifier as mod
from app.inference.t2a_classifier import (
    classify_frame,
    classify_reason_text,
    classify_url,
    parse_classify_payload,
    probe_t2a_classifier,
)

PNG = b"\x89PNG\r\n\x1a\nfake-frame"

GOOD_BODY: dict[str, Any] = {
    "results": [
        {
            "probs": {"Benign": 0.72, "Malignant": 0.21, "Normal": 0.07},
            "predicted": "Benign",
        }
    ]
}


def _patch_client(monkeypatch, handler) -> None:
    """把 ``httpx.AsyncClient`` 换成走 MockTransport 的版本（不改生产代码形状）。

    生产代码用 ``async with httpx.AsyncClient(...) as client``，因此这里替换的
    工厂只需返回一个同样可当 async context manager 的实例。
    """
    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return real_client(transport=transport, timeout=5.0)

    monkeypatch.setattr(mod.httpx, "AsyncClient", factory)


class TestClassifyFrameSuccess:
    async def test_returns_probs_and_prediction(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/v1/classify"
            assert request.method == "POST"
            assert b'name="images"' in request.content, "multipart 字段名必须是 images"
            return httpx.Response(200, json=GOOD_BODY)

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="127.0.0.1", port=8003)

        assert out["available"] is True
        assert out["predicted"] == "Benign"
        assert out["malignant_prob"] == pytest.approx(0.21)
        assert out["probs"]["Normal"] == pytest.approx(0.07)
        assert out["endpoint"] == "http://127.0.0.1:8003/v1/classify"

    async def test_predicted_falls_back_to_argmax(self, monkeypatch):
        """服务端没给 predicted 时按概率取最大 —— 这是**服务端给了概率**的推导，不是编造。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, json={"results": [{"probs": {"Benign": 0.1, "Malignant": 0.9}}]}
            )

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["predicted"] == "Malignant"


class TestFailureReasonsAreDistinct:
    """反证腿集：每个分支必须报**自己的**原因。"""

    async def test_empty_bytes_reports_no_image_bytes(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("空字节不该发请求")

        _patch_client(monkeypatch, handler)

        out = await classify_frame(b"", host="h", port=1)

        assert out == {
            "available": False,
            "reason": "no_image_bytes",
            "detail": "empty frame bytes",
            "endpoint": "http://h:1/v1/classify",
        }

    async def test_connect_error_is_service_unavailable(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused", request=request)

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["available"] is False
        assert out["reason"] == "service_unavailable"
        assert "ConnectError" in out["detail"]

    async def test_timeout_is_service_unavailable_and_logged(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        _patch_client(monkeypatch, handler)
        warnings: list[str] = []

        class _Logger:
            def warning(self, event, **kw):
                warnings.append(event)

            def info(self, event, **kw):
                pass

        monkeypatch.setattr(mod, "logger", _Logger())

        out = await classify_frame(PNG, host="h", port=1, timeout_s=0.01)

        assert out["reason"] == "service_unavailable"
        assert "t2a_classify_timeout" in warnings

    async def test_503_with_model_marker_is_model_missing(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="model not loaded, warmup pending")

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["reason"] == "model_missing"
        assert out["http_status"] == 503

    async def test_503_without_marker_is_service_unavailable(self, monkeypatch):
        """反证腿：把 model_missing 判据放宽成"任何 5xx" → 本用例红。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="upstream gateway timeout")

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["reason"] == "service_unavailable"

    async def test_404_model_marker_is_model_missing(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text='{"detail": "checkpoint not found"}')

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["reason"] == "model_missing"

    async def test_non_json_body_is_invalid_response(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<html>ok</html>")

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["reason"] == "invalid_response"

    async def test_missing_results_is_invalid_response(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        _patch_client(monkeypatch, handler)

        out = await classify_frame(PNG, host="h", port=1)

        assert out["reason"] == "invalid_response"
        assert "results" in out["detail"]


class TestParseIsStrict:
    """解析器不许"猜"：形状不符一律 None，绝不默认概率。"""

    @pytest.mark.parametrize(
        "body",
        [
            None,
            [],
            {},
            {"results": []},
            {"results": ["Benign"]},
            {"results": [{}]},
            {"results": [{"probs": {}}]},
            {"results": [{"probs": "0.9"}]},
            # 单个标签分数非数字 → 整条不可信（部分可信也是不可信）
            {"results": [{"probs": {"Benign": 0.9, "Malignant": "high"}}]},
        ],
    )
    def test_malformed_payloads_return_none(self, body):
        assert parse_classify_payload(body) is None

    def test_missing_malignant_label_yields_none_prob(self):
        """没有 Malignant 标签 → malignant_prob=None（不是 0.0，两者语义不同）。"""
        parsed = parse_classify_payload(
            {"results": [{"probs": {"Benign": 0.8, "Normal": 0.2}, "predicted": "Benign"}]}
        )

        assert parsed is not None
        assert parsed["malignant_prob"] is None


class TestReasonText:
    def test_known_reasons_are_chinese_and_short(self):
        for reason in (
            "disabled",
            "no_image_ids",
            "image_not_indexed",
            "no_image_bytes",
            "frame_decode_failed",
            "service_unavailable",
            "model_missing",
            "invalid_response",
        ):
            text = classify_reason_text(reason)
            assert text and reason not in text

    def test_unknown_reason_is_not_faked(self):
        text = classify_reason_text("some_new_reason")

        assert "some_new_reason" in text
        assert "未知" in text


class TestClassifyUrl:
    def test_url_shape(self):
        assert classify_url(host="127.0.0.1", port=8003) == (
            "http://127.0.0.1:8003/v1/classify"
        )


class _EnabledSwitch:
    """临时打开/关闭 ``t2a_classifier_in_pipeline_enabled``。

    为什么不用 ``monkeypatch.setattr``：pydantic Settings 是 frozen 模型，
    ``monkeypatch`` 的 setattr 会因 frozen 而失败；本仓库既有测试
    （如 ``test_nodule_contour_bypass.py:126``）因此统一用 ``object.__setattr__``
    并在 ``finally`` 里**还原原值**（还原成"原来那个值"而不是 delattr ——
    delattr 会打掉类字段，后续用例就再也拿不到默认值）。
    """

    def __init__(self, value: bool) -> None:
        self.value = value

    def __enter__(self):
        from app.config.settings import settings

        self._settings = settings
        self._old = settings.__dict__.get("t2a_classifier_in_pipeline_enabled", None)
        object.__setattr__(settings, "t2a_classifier_in_pipeline_enabled", self.value)
        return settings

    def __exit__(self, *exc):
        if self._old is None:
            # 类字段没被覆盖过 → 从实例 dict 摘掉，回落到类默认值
            self._settings.__dict__.pop("t2a_classifier_in_pipeline_enabled", None)
        else:
            object.__setattr__(
                self._settings, "t2a_classifier_in_pipeline_enabled", self._old
            )
        return False


class TestProbe:
    async def test_disabled_short_circuits_without_network(self, monkeypatch):
        """开关关着时**不发请求**（每 30s 一次的 health 不该产生跨进程调用）。"""

        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("开关关着时不该连 8003")

        _patch_client(monkeypatch, handler)

        with _EnabledSwitch(False):
            out = await probe_t2a_classifier(host="h", port=8003)

        assert out["status"] == "disabled"
        assert out["enabled"] is False
        assert out["endpoint"] == "http://h:8003/v1/classify"

    async def test_enabled_and_healthy_is_ready(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/health"
            return httpx.Response(200, json={"status": "ok"})

        _patch_client(monkeypatch, handler)

        with _EnabledSwitch(True):
            out = await probe_t2a_classifier(host="h", port=8003)

        assert out["status"] == "ready"

    async def test_unreachable_reports_probe_error(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        _patch_client(monkeypatch, handler)

        with _EnabledSwitch(True):
            out = await probe_t2a_classifier(host="h", port=8003)

        assert out["status"] == "unreachable"
        assert "ConnectError" in out["probe_error"]

    async def test_model_missing_is_visible_not_ready(self, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="classifier model not loaded")

        _patch_client(monkeypatch, handler)

        with _EnabledSwitch(True):
            out = await probe_t2a_classifier(host="h", port=8003)

        assert out["status"] == "model_missing"
