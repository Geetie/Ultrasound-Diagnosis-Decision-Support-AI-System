"""A.70.7 守卫：降级成因排查的解析与归类必须可解释。

三条纪律：
* 从降级结论里抽的是**包装层异常名**（`InferenceError`），不是根因 → 根因只能在日志里找；
* 日志签名要**能重叠命中**（一行可能同时是 timeout 与 llm_unavailable，不许只算一个）；
* 抽不到日期记 `(无日期)`，不许静默丢行。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p49_degraded_cause.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p49_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_exc_type_extracted_from_degraded_text():
    p49 = _load()
    assert p49.exc_type_of("[thyroid] llm_unavailable: InferenceError; manual review required") == (
        "InferenceError"
    )
    assert p49.exc_type_of("没有异常名") == "unknown"
    assert p49.exc_type_of("") == "unknown"


def test_degraded_kind_separates_empty_from_english_strings():
    """空结论 ≠ 降级串（451 里 358 条是空的），必须分开数。"""
    p49 = _load()
    assert p49.degraded_kind("") == "empty"
    assert p49.degraded_kind("   ") == "empty"
    assert p49.degraded_kind("[thyroid] llm_unavailable: InferenceError; x") == "llm_unavailable"
    assert p49.degraded_kind("[liver] evidence_insufficient: no chunks") == "evidence_insufficient"
    assert p49.degraded_kind("[cardiac] parse_failed: ValueError") == "parse_failed"
    assert p49.degraded_kind("甲状腺：右叶低回声结节，建议复查") == "not_degraded"


def test_summarize_records_groups_by_exc_day_organ():
    p49 = _load()
    items = [
        {"exc": "InferenceError", "day": "2026-09-20", "organ": "thyroid"},
        {"exc": "InferenceError", "day": "2026-09-20", "organ": "liver"},
        {"exc": "TimeoutError", "day": "2026-10-04", "organ": "thyroid"},
    ]
    out = p49.summarize_records(items)
    assert out["total"] == 3
    assert out["by_exc"]["InferenceError"] == 2
    assert out["by_day"]["2026-09-20"] == 2
    assert out["by_organ"]["thyroid"] == 2


def test_log_line_can_hit_multiple_signatures():
    p49 = _load()
    line = "2026-10-04 12:00:00 specialist_llm_failed error=TimeoutError: timed out"
    hits = p49.classify_log_line(line)
    assert "specialist_llm_failed" in hits
    assert "timeout" in hits


def test_summarize_logs_counts_per_day_and_keeps_undated():
    p49 = _load()
    lines = [
        "2026-10-04 12:00:00 CUDA out of memory",
        "2026-10-04 13:00:00 Connection refused",
        "no date here TimeoutError",
        "普通行，不该命中",
    ]
    out = p49.summarize_logs(lines)
    assert out["by_signature"]["cuda_oom"] == 1
    assert out["by_signature"]["conn_refused"] == 1
    assert out["by_signature"]["timeout"] == 1
    assert out["by_day"]["2026-10-04"] == {"cuda_oom": 1, "conn_refused": 1}
    assert out["by_day"]["(无日期)"] == {"timeout": 1}


def test_script_is_readonly():
    src = _SRC.read_text(encoding="utf-8")
    upper = src.upper()
    assert "UPDATE " not in upper and "DELETE FROM" not in upper
    assert "from app.inference.output_guard" not in src  # 这里不需要守卫：只分类异常，不判脏
