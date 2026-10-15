"""A.66 WP1-① 守卫：难度分层体检的**分层口径守恒**与**单一尺子**。

钉住两件事：
1. 四层（top1 / top3 / top10 / miss）之和必须等于总条数 —— 分层统计最容易出的错就是
   "漏算/重复算"，而报告里这句话是要被引用的（A.65 的教训：判据/口径必须能自证）；
2. 命中判定复用 `gate2_bge_top10.doc_level_hit_at_k`、占位块检测复用
   `p39_graph_neighbor_ranking_sim.count_placeholder_chunks` —— 禁止本探针自造。
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p40_under_test", _AUDIT / "p40_golden_difficulty_profile.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_layer_mapping():
    p40 = _load()
    assert p40.layer_of(1) == "top1"
    assert p40.layer_of(2) == "top3"
    assert p40.layer_of(3) == "top3"
    assert p40.layer_of(4) == "top10"
    assert p40.layer_of(10) == "top10"
    assert p40.layer_of(11) == "miss"
    assert p40.layer_of(None) == "miss"
    assert p40.layer_of(0) == "miss"


def test_layers_are_conserved():
    p40 = _load()
    rows = [
        {"rank": 1},
        {"rank": 2},
        {"rank": 3},
        {"rank": 7},
        {"rank": None},
        {"rank": 99},
    ]
    out = p40.summarize_layers(rows)
    assert out["total"] == 6
    assert out["counts"] == {"top1": 1, "top3": 2, "top10": 1, "miss": 2}
    assert sum(out["counts"].values()) == out["total"]
    assert abs(sum(out["ratio"].values()) - 1.0) < 1e-6


def test_empty_input_is_zero_not_crash():
    p40 = _load()
    out = p40.summarize_layers([])
    assert out["total"] == 0
    assert all(v == 0 for v in out["counts"].values())
    assert all(v == 0.0 for v in out["ratio"].values())


def test_instrument_verdict_uses_cumulative_hit_at_10():
    """饱和判定必须用**累计** hit@10（首版用单层占比 → 把 0.9853 的集合判成"还有余量"）。"""
    p40 = _load()
    saturated_layers = {
        "total": 136,
        "counts": {"top1": 129, "top3": 4, "top10": 1, "miss": 2},
        "ratio": {"top1": 0.9485, "top3": 0.0294, "top10": 0.0074, "miss": 0.0147},
    }
    out = p40.instrument_verdict(saturated_layers)
    assert out["cumulative_hit_at_10"] == 0.9853
    assert out["saturated"] is True
    assert "SATURATED" in out["note"]

    headroom = {
        "total": 100,
        "counts": {"top1": 50, "top3": 20, "top10": 10, "miss": 20},
        "ratio": {"top1": 0.5, "top3": 0.2, "top10": 0.1, "miss": 0.2},
    }
    out2 = p40.instrument_verdict(headroom)
    assert out2["saturated"] is False
    assert "HAS_HEADROOM" in out2["note"]


def test_reuses_single_source_and_placeholder_guard():
    src = (_AUDIT / "p40_golden_difficulty_profile.py").read_text(encoding="utf-8")
    assert "from gate2_bge_top10 import doc_level_hit_at_k" in src
    assert "from p39_graph_neighbor_ranking_sim import count_placeholder_chunks" in src
    assert "def doc_level_hit" not in src
    assert "def cosine" not in src
    # 占位块必须显式失败（A.65 踩坑：session_factory=None 会静默算成全 0）
    assert "占位块占多数" in src
