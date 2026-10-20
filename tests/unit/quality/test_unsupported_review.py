"""A.69 / R17 续守卫：核对表的「重合率」与「自动分流」必须可解释、可复算。

三类分流的边界（写死）：
* `< 0.2`  → `COVERAGE_GAP`（资料里几乎没有对应内容）
* `>= 0.5` → `JUDGE_STRICT`（资料有相近表述却判不支持）
* 其余 → `AMBIGUOUS`
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_AUDIT = Path(__file__).resolve().parents[3] / "scripts" / "audit"


def _load():
    spec = importlib.util.spec_from_file_location(
        "_p45_under_test", _AUDIT / "p45_unsupported_review.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_overlap_is_high_when_context_repeats_statement():
    p45 = _load()
    stmt = "建议每 6 至 12 个月复查超声"
    ctx = "指南建议：每 6 至 12 个月复查超声，观察结节大小与形态变化。"
    assert p45.overlap_ratio(stmt, ctx) >= 0.5


def test_overlap_is_low_when_context_is_unrelated():
    p45 = _load()
    stmt = "甲状腺乳头状癌占全部甲状腺癌的绝大多数"
    ctx = "乳腺超声检查应关注肿块边界、形态与血流信号。"
    assert p45.overlap_ratio(stmt, ctx) < 0.2


def test_overlap_handles_empty_inputs():
    p45 = _load()
    assert p45.overlap_ratio("", "任意资料") == 0.0
    assert p45.overlap_ratio("任意断言", "") == 0.0
    assert p45.overlap_ratio("短", "短") == 0.0  # 单字无 2-gram


def test_bucket_boundaries_are_fixed():
    p45 = _load()
    assert p45.bucket_of(0.0) == "COVERAGE_GAP"
    assert p45.bucket_of(0.199) == "COVERAGE_GAP"
    assert p45.bucket_of(0.2) == "AMBIGUOUS"
    assert p45.bucket_of(0.499) == "AMBIGUOUS"
    assert p45.bucket_of(0.5) == "JUDGE_STRICT"
    assert p45.bucket_of(1.0) == "JUDGE_STRICT"


def test_build_rows_only_keeps_unsupported_knowledge_statements():
    p45 = _load()
    report = {
        "per_sample": [
            {
                "request_id": "r1",
                "organ": "thyroid",
                "detail": [
                    {"statement": "结节边界不清", "bucket": "image", "verdict": 0},
                    {"statement": "指南建议每 6 至 12 个月复查超声", "bucket": "knowledge", "verdict": 0},
                    {"statement": "甲状腺癌以乳头状癌最常见", "bucket": "knowledge", "verdict": 1},
                ],
            },
            {
                "request_id": "r2",
                "organ": "liver",
                "detail": [
                    {"statement": "肝脏回声均匀", "bucket": "image", "verdict": 0},
                ],
            },
        ]
    }
    items = {
        "r1": {"clinical_text": "甲状腺结节怎么随访", "conclusion": "建议随访", "context": ["指南建议每 6 至 12 个月复查超声"]},
        "r2": {"clinical_text": "肝脏", "conclusion": "回声均匀", "context": ["肝脏超声"]},
    }
    rows, summary = p45.build_rows(report, items, max_rows=20)
    # r2 没有知识断言 → 不出行
    assert len(rows) == 1
    assert rows[0]["request_id"] == "r1"
    assert rows[0]["n_knowledge"] == 2
    assert rows[0]["n_unsupported"] == 1
    assert rows[0]["unsupported"][0]["class"] == "JUDGE_STRICT"
    assert summary["unsupported_statements_shown"] == 1


def test_summary_shares_sum_to_one_when_non_empty():
    p45 = _load()
    report = {
        "per_sample": [
            {
                "request_id": "r1",
                "organ": "thyroid",
                "detail": [
                    {"statement": "完全不相关的断言内容", "bucket": "knowledge", "verdict": 0},
                ],
            }
        ]
    }
    items = {"r1": {"context": ["乳腺超声关注边界与血流"]}}
    _rows, summary = p45.build_rows(report, items, max_rows=20)
    assert summary["unsupported_statements_shown"] == 1
    assert summary["class_share"]["COVERAGE_GAP"] == 1.0
    assert abs(sum(v for v in summary["class_share"].values() if v)) - 1.0 < 1e-9
