"""守卫：同意状态在**跨服务边界**上必须是枚举值（`granted`），不是枚举 repr。

## 为什么需要它（2026-09-29 真机第 1 例真实诊断暴露）

SG 的 `/api/v1/consent-status/{patient_id}` 原先写 `str(consent_status)`，而
`ConsentStatus` 是 `(str, Enum)` —— py3.11+ 的默认 `__str__` 给出
**`"ConsentStatus.GRANTED"`**（不是 `"granted"`）。主服务拿到后原样回传给
`SafetyCheckRequest.consent_status_at_input` 的
`Literal["granted","denied","withdrawn","pending"]` → **422**：

    主服务日志：safety_gateway_check_failed session_id=... status_code=422
    → consecutive_failures=3 → safety_gateway_fast_fail（看起来像"网关不可用"）
    → 诊断恒 failed

两侧都钉：SG 侧返回 `.value`；主服务侧对"对端怪值"做归一，未知值 fail-closed。
"""
from __future__ import annotations

from enum import Enum

from app.services.safety_gateway_client import _normalize_consent_status


class _FakeStatus(str, Enum):
    GRANTED = "granted"
    WITHDRAWN = "withdrawn"


class TestSafetyGatewaySide:
    def test_枚举值而不是repr(self):
        from safety_gateway.main import _consent_status_value

        assert _consent_status_value(_FakeStatus.GRANTED) == "granted"
        assert _consent_status_value(_FakeStatus.WITHDRAWN) == "withdrawn"

    def test_None仍为None(self):
        from safety_gateway.main import _consent_status_value

        assert _consent_status_value(None) is None

    def test_普通字符串原样返回(self):
        from safety_gateway.main import _consent_status_value

        assert _consent_status_value("pending") == "pending"

    def test_源码不再直接str枚举(self):
        """反例守卫：`str(consent_status)` 那种写法一旦回来就会重新触发 422。"""
        import pathlib

        src = (
            pathlib.Path(__file__).resolve().parents[3] / "safety_gateway" / "main.py"
        ).read_text(encoding="utf-8")

        assert "consent_status=str(consent_status)" not in src
        assert "_consent_status_value(consent_status)" in src


class TestClientSideNormalization:
    def test_枚举repr被归一(self):
        assert _normalize_consent_status("ConsentStatus.GRANTED") == "granted"

    def test_大小写被归一(self):
        assert _normalize_consent_status("GRANTED") == "granted"
        assert _normalize_consent_status(" Withdrawn ") == "withdrawn"

    def test_契约值原样通过(self):
        for value in ("granted", "denied", "withdrawn", "pending"):
            assert _normalize_consent_status(value) == value

    def test_未知值一律None即fail_closed(self):
        """不猜测未知状态：调用方据此走 fail-closed，而不是把怪值透传给 SG。"""
        assert _normalize_consent_status("ConsentStatus.UNKNOWN_NEW_VALUE") is None
        assert _normalize_consent_status("something-else") is None
        assert _normalize_consent_status("") is None
        assert _normalize_consent_status(None) is None

    def test_归一后的值必然落在SG的Literal集合内(self):
        """闭环：归一结果只能是 SG 请求体接受的那四个字面量。"""
        allowed = {"granted", "denied", "withdrawn", "pending"}
        samples = [
            "ConsentStatus.GRANTED",
            "DENIED",
            "Pending",
            "withdrawn",
            "bogus",
            None,
        ]

        for raw in samples:
            normalized = _normalize_consent_status(raw)
            assert normalized is None or normalized in allowed
