"""N4：检索置信度归一化与升档判据（修复量纲错配）。

修复前：`retrieval_confidence < organ_threshold`（1.705~5.207 比 0.85）
→ 恒 False → 升档从未触发。守卫把这条真机事实钉住。
"""

from __future__ import annotations

import pytest

from app.agents.retrieval_confidence import (
    CONF_CEIL,
    CONF_FLOOR,
    DEFAULT_LOW_CONFIDENCE_THRESHOLD,
    is_low_retrieval_confidence,
    normalize_retrieval_confidence,
)

pytestmark = pytest.mark.track_agt


# 真机实测取值（n=12，两批采集）
REAL_VALUES = [1.7051, 2.2324, 2.3711, 2.7871, 2.8164, 3.3066, 3.4062, 3.7988, 5.2070]


def test_normalize_returns_none_for_untested():
    """未测 ≠ 0 分（N2/N4 一贯口径）。"""
    assert normalize_retrieval_confidence(None) is None


def test_normalize_clamps_outside_anchors():
    assert normalize_retrieval_confidence(CONF_FLOOR - 1) == 0.0
    assert normalize_retrieval_confidence(CONF_CEIL + 1) == 1.0


def test_normalize_is_monotonic_over_real_values():
    normalized = [normalize_retrieval_confidence(v) for v in REAL_VALUES]
    assert normalized == sorted(normalized)
    assert all(0.0 <= v <= 1.0 for v in normalized)


def test_real_lowest_case_triggers_upgrade():
    """真机最差一例（1.705）必须能触发升档 —— 修复前恒不触发。"""
    assert is_low_retrieval_confidence(1.7051) is True


def test_real_median_does_not_trigger():
    assert is_low_retrieval_confidence(2.8164) is False


def test_real_best_case_does_not_trigger():
    assert is_low_retrieval_confidence(5.2070) is False


def test_untested_never_triggers():
    """拿不到分就不升档：不能把"没测"当"很差"。"""
    assert is_low_retrieval_confidence(None) is False


def test_threshold_is_in_normalized_scale_not_confidence_scale():
    """钉住量纲：阈值必须是 0~1 归一化尺度上的一小数，而不是 0.85 这种结论阈值。"""
    assert 0.0 < DEFAULT_LOW_CONFIDENCE_THRESHOLD < 0.5


def test_upgrade_rate_matches_calibration():
    """定标口径：n=12 里只有最差 1 例升档（≈8%），成本可控。"""
    triggered = [v for v in REAL_VALUES if is_low_retrieval_confidence(v)]
    assert triggered == [1.7051]
