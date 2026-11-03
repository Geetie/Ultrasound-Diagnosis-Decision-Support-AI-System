"""N4 / 01-2：检索后二次判定 —— 低检索置信度升档（生产此前结构性不可达）。

背景：`_assess_routing` 在**检索之前**跑，那时没有 retrieval_confidence
（它是检索产物）→ routing 的 `is_low_confidence` 分支生产上永远走不到。
本守卫钉住"检索后用真实分做二次判定"这条通道。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.routing import AdaptiveRAGRouter
from app.models.schemas import ResponseTier
from app.services.diagnosis_pipeline import (
    _confidence_from_specialists,
    _maybe_upgrade_on_low_confidence,
)

pytestmark = pytest.mark.track_services

# 单特征查询：路由给 standard（多特征查询本身就会 target_tier=full，
# 用它测升档会与"需医生确认"的既有逻辑混淆）。
_QUERY = "甲状腺结节复查"


def _resp(conf):
    return SimpleNamespace(retrieval_confidence=conf)


def _standard_routing():
    return AdaptiveRAGRouter().assess(
        query=_QUERY,
        target_organ="thyroid",
        requested_tier=ResponseTier.STANDARD,
    )


def test_confidence_takes_max_across_specialists():
    """多专科时取 max：一条证据链够强就不该整体升档。"""
    assert _confidence_from_specialists([_resp(1.705), _resp(4.2)]) == 4.2


def test_confidence_none_when_all_missing():
    assert _confidence_from_specialists([_resp(None), _resp(None)]) is None
    assert _confidence_from_specialists([]) is None


def test_low_confidence_upgrades_to_full():
    """真机最差例 1.705（归一化 0.051）→ 必须升档 —— 生产此前走不到这里。"""
    routing = _standard_routing()
    assert routing.effective_tier == ResponseTier.STANDARD

    upgraded = _maybe_upgrade_on_low_confidence(routing, [_resp(1.705)], request_id="r1")
    assert upgraded.effective_tier == ResponseTier.FULL
    from app.agents.routing import RoutingReason

    assert RoutingReason.LOW_RETRIEVAL_CONFIDENCE in upgraded.reasons


def test_normal_confidence_does_not_upgrade():
    routing = _standard_routing()
    kept = _maybe_upgrade_on_low_confidence(routing, [_resp(2.816)], request_id="r2")
    assert kept.effective_tier == ResponseTier.STANDARD


def test_already_full_is_unchanged():
    """已经是 FULL → 不重复升级、不新增 reason。"""
    full = AdaptiveRAGRouter().assess(
        query=_QUERY, target_organ="thyroid", requested_tier=ResponseTier.FULL
    )
    out = _maybe_upgrade_on_low_confidence(full, [_resp(1.705)], request_id="r3")
    assert out.effective_tier == ResponseTier.FULL
    assert list(out.reasons) == list(full.reasons)


def test_missing_confidence_does_not_upgrade():
    """未测 ≠ 很差（N2/N4 一贯口径）。"""
    routing = _standard_routing()
    kept = _maybe_upgrade_on_low_confidence(routing, [_resp(None)], request_id="r4")
    assert kept.effective_tier == ResponseTier.STANDARD


def test_none_routing_is_safe():
    assert _maybe_upgrade_on_low_confidence(None, [_resp(1.705)]) is None
