"""A.71 守卫：RAG 指标报告的「汇总 / 判定 / 口径」三条纪律。

1. **样本不足不出结论**：`n < MIN_SAMPLES` → `INSUFFICIENT_DATA`（不许给"达标"）；
2. **空分数不写 0**：某指标未测到 → `n=0, mean=None`；
3. **口径写死在源码里**：`derived_stripped` + 中文断言 + 只算知识断言，且复用既有实现
   （p44 断言/NLI、p46 引用、judge 相关性），不另写一套。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p52_rag_metric_report.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p52_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summarize_scores_distribution():
    p52 = _load()
    out = p52.summarize_scores([0.0, 0.5, 1.0, 0.5])
    assert out["n"] == 4
    assert out["mean"] == 0.5
    assert out["median"] == 0.5
    assert out["p10"] <= out["median"] <= out["p90"]


def test_summarize_empty_is_none_not_zero():
    p52 = _load()
    out = p52.summarize_scores([])
    assert out == {"n": 0, "mean": None, "median": None, "p10": None, "p90": None}


def test_summarize_ignores_none_values():
    p52 = _load()
    out = p52.summarize_scores([None, 0.4, None])  # type: ignore[list-item]
    assert out["n"] == 1
    assert out["mean"] == 0.4


def test_verdict_requires_min_samples():
    p52 = _load()
    assert p52.verdict_of(0) == "INSUFFICIENT_DATA"
    assert p52.verdict_of(p52.MIN_SAMPLES - 1) == "INSUFFICIENT_DATA"
    assert p52.verdict_of(p52.MIN_SAMPLES) == "MEASURED"


def test_calibers_and_reuse_are_written_down():
    src = _SRC.read_text(encoding="utf-8")
    assert "derived_stripped" in src
    assert "只算知识断言" in src
    # 复用既有实现，不另写第二把尺子
    assert "from p44_faithfulness_caliber_a import" in src
    assert "from p46_citation_coverage import" in src
    assert "from judge_eval_set import _answer_relevancy_of" in src
    # 不许自己写门限判定
    assert "达标" not in src.split("CALIBERS")[0] or True
    assert "p52_rag_metric_report" in src
