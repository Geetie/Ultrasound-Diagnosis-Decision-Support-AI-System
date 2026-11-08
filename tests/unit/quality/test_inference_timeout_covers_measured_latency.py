"""推理超时必须覆盖实测生成延迟（2026-11-08）。

## 缺陷

`inference_timeout_seconds` 曾是 **30.0 s**，而 `p107b` 实测 II-Medical-8B
的**单项**生成延迟为：

    p50 = 46.04 s / p90 = 94.1 s / max = 101.4 s   （104 次调用）

→ **93/104 = 89.42% 的调用必然超时** → `E2002_TIMEOUT` → `circuit_breaker_open`
→ 后续请求全被拒。

也就是说原来的 30 s 不是"保护"，而是**让系统在正常延迟下自我熔断**。
任何在该配置下测出的生产指标，测的都是"熔断率"而不是"模型能力"。

本守卫把「超时必须 ≥ 实测最大值」钉死，防止有人再按直觉把它调回 30 s。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings

#: p107b 实测的生成延迟（秒）。改这些数必须同时更新依据。
MEASURED_LATENCY_P50_S = 46.04
MEASURED_LATENCY_P90_S = 94.1
MEASURED_LATENCY_MAX_S = 101.4
MEASURED_N_CALLS = 104
MEASURED_TIMEOUT_RATE_AT_30S = 93 / 104  # 89.42%


def test_timeout_covers_measured_max_latency():
    """超时必须 ≥ 实测单项最大值，否则正常请求会系统性超时。"""
    assert settings.inference_timeout_seconds >= MEASURED_LATENCY_MAX_S, (
        f"inference_timeout_seconds={settings.inference_timeout_seconds} "
        f"< 实测 max {MEASURED_LATENCY_MAX_S}s —— 正常请求会被误判超时"
    )


def test_timeout_not_the_old_30s_value():
    """钉住：不允许回到 30 s。该值下 89.42% 的调用必然超时。"""
    assert settings.inference_timeout_seconds > 30.0, (
        "inference_timeout_seconds 回到 30s：p107b 实测 89.42% 调用会超时"
        "（p50 就有 46.04s）"
    )


def test_timeout_leaves_headroom_above_max():
    """留余量：超时应至少是实测 max 的 1.5 倍（应对长尾与负载波动）。"""
    ratio = settings.inference_timeout_seconds / MEASURED_LATENCY_MAX_S
    assert ratio >= 1.5, f"余量不足：{ratio:.2f}×（要求 ≥1.5×）"


def test_timeout_below_retry_explosion():
    """上限保护：超时 × 重试次数不应超过 10 分钟（否则一个请求能挂 10+ 分钟）。"""
    worst_case = settings.inference_timeout_seconds * settings.inference_max_retries
    assert worst_case <= 600, (
        f"超时 {settings.inference_timeout_seconds}s × 重试 {settings.inference_max_retries} "
        f"= {worst_case}s 超过 600s 上限，一个请求可能挂 10+ 分钟"
    )
