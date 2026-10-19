"""A.68 / R3-① 守卫：放宽分组必须「题变多」且「gold 不歧义」。

两条判据写死在 p43 里（跑之前就定）：
* 可构造对数 ≥ 现状的 3 倍；
* 组内文档数 ≤ 30（超过就有多篇都正确 → gold 歧义，必须人工抽检）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p43_under_test", _AUDIT / "p43_hard_set_grouping_quant.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _row(scheme: str, pairs: int, max_docs: int) -> dict:
    return {"scheme": scheme, "query_pairs": pairs, "max_group_docs": max_docs}


def test_relaxed_grouping_recommended_when_pairs_grow_and_groups_small():
    p43 = _load()
    out = p43.verdict_of(
        [
            _row("exact", 100, 5),
            _row("prefix2", 400, 12),
        ]
    )
    assert out["prefix2"]["code"] == "RECOMMEND"
    assert out["prefix2"]["pair_gain_vs_exact"] == 4.0


def test_big_groups_are_flagged_as_ambiguous_even_if_pairs_explode():
    p43 = _load()
    out = p43.verdict_of(
        [
            _row("exact", 100, 5),
            _row("prefix1", 5000, 200),
        ]
    )
    assert out["prefix1"]["code"] == "AMBIGUOUS_GOLD_RISK"
    assert "歧义" in out["prefix1"]["why"]


def test_no_gain_is_not_enough():
    p43 = _load()
    out = p43.verdict_of(
        [
            _row("exact", 100, 5),
            _row("prefix2", 120, 6),
        ]
    )
    assert out["prefix2"]["code"] == "NOT_ENOUGH"


def test_exact_scheme_is_never_recommended():
    """精确口径是现状基线，不该被判成"推荐改动"。"""
    p43 = _load()
    out = p43.verdict_of([_row("exact", 100, 5)])
    assert out["exact"]["code"] == "NOT_ENOUGH"
