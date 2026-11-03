"""G3：提交响应的 estimated_time_ms 必须来自真实档位预算（此前硬编码 3000/2000）。

缺陷：前端进度按 3s/2s 预期，而 worker 实际预算是 180s/10s —— 两个数量级差，
医生看到的"预计 3 秒"与真实"约 40 秒"不符。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings

pytestmark = pytest.mark.track_quality


def test_full_tier_uses_full_budget():
    assert settings.estimate_ms_for_tier("full") == int(settings.pipeline_total_budget_s * 1000)


def test_standard_tier_uses_standard_budget():
    assert settings.estimate_ms_for_tier("standard") == int(
        settings.pipeline_total_budget_standard_s * 1000
    )


def test_realtime_shares_standard_budget():
    """realtime 不走诊断路径（00 号 §2.1）→ 沿用 standard 预算。"""
    assert settings.estimate_ms_for_tier("realtime") == settings.estimate_ms_for_tier("standard")


def test_unknown_tier_falls_back_to_standard():
    assert settings.estimate_ms_for_tier(None) == settings.estimate_ms_for_tier("standard")
    assert settings.estimate_ms_for_tier("") == settings.estimate_ms_for_tier("standard")


def test_estimate_is_not_hardcoded_small():
    """钉住缺陷形态：不得再回到 3000/2000 量级。"""
    assert settings.estimate_ms_for_tier("full") >= 60_000
