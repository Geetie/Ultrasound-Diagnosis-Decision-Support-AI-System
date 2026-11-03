"""G4/G7/N6：检索降级必须能到医生眼前（此前只写日志）。

每个原因码与中文说明**一一对应**；缺数据不得用 False 冒充"没降级"。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.rag.degradation import summarize_degradation
from app.rag.protocols import RetrievalResult

pytestmark = pytest.mark.track_rag


def test_clean_result_is_not_degraded():
    r = RetrievalResult(rerank_engine="bge")
    out = summarize_degradation(r)
    assert out.degraded is False
    assert out.reasons == []


def test_rerank_disabled_reported():
    out = summarize_degradation(RetrievalResult(rerank_engine="none"))
    assert out.degraded is True
    assert "rerank_disabled" in out.reasons
    assert any("未做精排" in n for n in out.notes)


def test_rerank_heuristic_has_distinct_wording():
    out = summarize_degradation(RetrievalResult(rerank_engine="heuristic"))
    assert "rerank_heuristic" in out.reasons
    assert any("启发式" in n for n in out.notes)
    # 与"未做精排"的措辞必须不同 —— 医生要能区分"按设计跳过"和"服务挂了"
    assert not any("未做精排" in n for n in out.notes)


def test_bm25_fallback_reported():
    out = summarize_degradation(RetrievalResult(rerank_engine="bge", triggered_bm25_fallback=True))
    assert "bm25_fallback" in out.reasons


def test_image_leg_skip_carries_reason():
    out = summarize_degradation(
        RetrievalResult(rerank_engine="bge", image_leg_skip_reason="no_session")
    )
    assert "image_leg_skipped" in out.reasons
    assert out.image_leg_skip_reason == "no_session"


def test_p99_and_dropped_rows_reported():
    out = summarize_degradation(
        RetrievalResult(rerank_engine="bge", p99_violation=True, dropped_missing_row=2)
    )
    assert "p99_violation" in out.reasons
    assert "missing_row_dropped" in out.reasons


def test_all_reasons_in_stable_order():
    r = RetrievalResult(
        rerank_engine="heuristic",
        triggered_bm25_fallback=True,
        image_leg_skip_reason="x",
        p99_violation=True,
        dropped_missing_row=1,
    )
    out = summarize_degradation(r)
    assert out.reasons == [
        "rerank_heuristic",
        "bm25_fallback",
        "image_leg_skipped",
        "p99_violation",
        "missing_row_dropped",
    ]
    assert len(out.notes) == len(out.reasons)  # 每条原因都有说明


def test_none_result_means_not_executed_not_degraded():
    """result=None = 根本没检索 —— 不得报成"降级"（也不得报成"正常"）。"""
    out = summarize_degradation(None)
    assert out.degraded is False
    assert out.reasons == []


def test_accepts_dict_shape():
    out = summarize_degradation({"rerank_engine": "heuristic", "triggered_bm25_fallback": False})
    assert out.reasons == ["rerank_heuristic"]


def test_as_dict_is_json_safe():
    import json

    out = summarize_degradation(SimpleNamespace(rerank_engine="none"))
    json.dumps(out.as_dict())  # 不抛即为 JSON 安全
