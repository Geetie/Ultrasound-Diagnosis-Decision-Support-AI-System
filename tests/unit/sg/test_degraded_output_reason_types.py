"""守卫：`apply_degraded_output` 必须同时接受**枚举成员**与**枚举值字符串**。

## 为什么需要它（2026-09-29 真机第 1 例真实诊断暴露）

`CheckResult` 声明了 `model_config = ConfigDict(use_enum_values=True)`，于是它的
`degraded_reason` 字段里存的是**字符串**（枚举值），而 `apply_degraded_output` 内部写的是
`reason.value`。调用方把 `check_result.degraded_reason` 直接传进来时：

    sg_unexpected_error error='str' object has no attribute 'value' prefix=safety_check
    → POST /api/v1/safety/check 503 → 主服务只能给 needs_review

而且这条路径此前**从未被走到**：上一版 consent 值形态不对（`"ConsentStatus.GRANTED"`），
请求在 pydantic 校验阶段就被 422 挡住了 —— 典型的"错的东西挡住了更深的错"。
"""
from __future__ import annotations

import pathlib

import pytest

from safety_gateway.degraded_output import apply_degraded_output
from safety_gateway.validation_result import DegradedReason


def _data() -> dict:
    return {"organ": "thyroid", "findings": "低回声结节", "diagnostic_conclusion": "建议随访"}


class TestAcceptsBothForms:
    def test_枚举成员可接受(self):
        out = apply_degraded_output(_data(), reason=DegradedReason.SENSITIVE_DATA_DETECTED)

        assert out["degraded_reason"] == DegradedReason.SENSITIVE_DATA_DETECTED.value

    @pytest.mark.parametrize(
        "reason",
        [
            DegradedReason.SENSITIVE_DATA_DETECTED.value,
            DegradedReason.CONSENT_STATUS_UNKNOWN.value,
            DegradedReason.SG_UNAVAILABLE_CONSENT_WITHDRAWN.value,
        ],
    )
    def test_枚举值字符串可接受(self, reason):
        """真机上炸的正是这一形态（CheckResult 的 use_enum_values 产物）。"""
        out = apply_degraded_output(_data(), reason=reason)

        assert out["degraded_reason"] == reason

    def test_未知原因不抛异常(self):
        out = apply_degraded_output(_data(), reason="some_future_reason")

        assert out["degraded_reason"] == "some_future_reason"

    def test_源码里不会出现裸的reason_value(self):
        """反例守卫：`reason.value` 这类写法必须建立在"已归一为枚举"之上。"""
        src = (
            pathlib.Path(__file__).resolve().parents[3]
            / "safety_gateway"
            / "degraded_output.py"
        ).read_text(encoding="utf-8")

        assert "isinstance(reason, DegradedReason)" in src
