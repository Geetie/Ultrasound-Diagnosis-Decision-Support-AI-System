"""A.74 守卫：数据链审计必须"取第一个失败环"，且各环判定不许互相吞掉。

为什么要钉这个：A.62~A.73 我一直在算比率，却没把链路拆开 —— 于是"2.9%""98.7%"
都说不清死在哪一环。链路审计的全部价值在于**归因到环**，所以：
* 顺序固定：input → retrieval → generate → parse → fusion → persist → display → ok；
* 前面环失败就不再往下判（否则一个"没图"的记录会被算成"融合失败"）；
* `fusion_result.degraded=True` 归 `display`（可读降级），**不算 ok**。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p55_chain_audit.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p55_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ok_findings(text="甲状腺：右叶低回声结节，分级 TR3。"):
    return {"fusion_result": {"content": text}}


def test_full_chain_ok():
    p55 = _load()
    link, reason = p55.classify("甲状腺结节三个月复查", ["img1"], ["k1"], _ok_findings())
    assert (link, reason) == ("ok", "")


def test_input_link_wins_over_later_failures():
    p55 = _load()
    # 无图 + 无文本 → 必须先判 input，而不是后面的 persist/fusion
    link, reason = p55.classify("", [], [], {"status": "processing"})
    assert link == "input" and reason == "no_text_no_image"
    # 有文本但无图 → 仍是 input（本产品是影像判读）
    link2, _ = p55.classify("甲状腺结节复查" * 3, [], ["k1"], _ok_findings())
    assert link2 == "input"


def test_retrieval_before_generate():
    p55 = _load()
    link, reason = p55.classify("甲状腺结节复查", ["img1"], [], {"error": "x", "error_type": "E"})
    assert (link, reason) == ("retrieval", "no_chunks")


def test_persist_link_detects_stuck_and_null():
    p55 = _load()
    assert p55.classify("甲状腺结节复查", ["img1"], ["k1"], {"status": "processing"})[0] == "persist"
    assert p55.classify("甲状腺结节复查", ["img1"], ["k1"], None)[0] == "persist"


def test_generate_and_parse_links():
    p55 = _load()
    assert p55.classify(
        "甲状腺结节复查", ["img1"], ["k1"], {"error": "boom", "error_type": "TimeoutError"}
    ) == ("generate", "error:TimeoutError")
    assert p55.classify(
        "甲状腺结节复查",
        ["img1"],
        ["k1"],
        {"fusion_result": {"content": "[thyroid] grade_unparseable: no grade"}},
    ) == ("parse", "grade_unparseable")


def test_fusion_empty_and_degraded_display():
    p55 = _load()
    assert p55.classify(
        "甲状腺结节复查", ["img1"], ["k1"], {"fusion_result": {"content": ""}}
    ) == ("fusion", "fusion_empty")
    link, reason = p55.classify(
        "甲状腺结节复查",
        ["img1"],
        ["k1"],
        {"fusion_result": {"content": "本次判读未完成，需人工复核", "degraded": True, "reason": "stale_processing"}},
    )
    assert link == "display" and reason.startswith("degraded:")


def test_summarize_counts_each_link_and_image_subset():
    p55 = _load()
    rows = [
        {"link": "ok", "reason": "", "organ": "thyroid", "has_image": True},
        {"link": "parse", "reason": "grade_unparseable", "organ": "thyroid", "has_image": True},
        {"link": "input", "reason": "no_image", "organ": "liver", "has_image": False},
    ]
    out = p55.summarize(rows)
    assert out["total"] == 3
    assert out["by_link"]["ok"] == 1
    assert out["by_link"]["parse"] == 1
    assert out["with_image"] == {"total": 2, "ok": 1, "ok_rate": 0.5}
    assert out["by_organ"]["thyroid"]["ok_rate"] == 0.5
