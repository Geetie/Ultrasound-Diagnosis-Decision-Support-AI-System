"""G4/G7/N6：检索降级从 trace 反解到 findings（纯函数）。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.diagnosis_pipeline import _retrieval_degradation_from_trace

pytestmark = pytest.mark.track_services


def _resp(*lines):
    return SimpleNamespace(reasoning_trace=list(lines))


def test_none_when_no_responses():
    assert _retrieval_degradation_from_trace([]) is None
    assert _retrieval_degradation_from_trace(None) is None


def test_none_when_trace_lacks_degradation_lines():
    """未采集 ≠ 没降级 —— 必须返回 None，不得返回 degraded=False。"""
    assert _retrieval_degradation_from_trace([_resp("rag_chunks: 5")]) is None


def test_extracts_reasons_in_canonical_order():
    out = _retrieval_degradation_from_trace([
        _resp(
            "retrieval_degraded: true",
            "retrieval_degraded_reason: p99_violation",
            "retrieval_degraded_reason: rerank_heuristic",
        )
    ])
    assert out["degraded"] is True
    assert out["reasons"] == ["rerank_heuristic", "p99_violation"]
    assert len(out["notes"]) == 2


def test_clean_result_reports_not_degraded():
    out = _retrieval_degradation_from_trace([_resp("retrieval_degraded: false")])
    assert out["degraded"] is False
    assert out["reasons"] == []


def test_any_sample_degraded_wins():
    out = _retrieval_degradation_from_trace([
        _resp("retrieval_degraded: false"),
        _resp("retrieval_degraded: true", "retrieval_degraded_reason: bm25_fallback"),
    ])
    assert out["degraded"] is True
    assert out["reasons"] == ["bm25_fallback"]


def test_image_leg_flag_reported():
    out = _retrieval_degradation_from_trace([
        _resp("retrieval_degraded: true", "retrieval_degraded_reason: image_leg_skipped")
    ])
    assert out["image_leg_skip_reason"] == "image_leg_skipped"
