"""A.70.9 守卫：每日质量监控的计数/占比/告警判定必须可解释、可复算。

为什么要有这条：9/28–9/30 的模型故障让 328 条请求变成空结论，**当时没有任何告警**。
监控的价值全在"阈值写死 + 退出码可接告警"，所以这三件事要钉住：
1. 计数不重叠（empty / unparseable / echo / degraded / clean 五类各自独立）；
2. 占比分母是**窗口内总数**（0 条时给 None，不写 0）；
3. 超阈值 → `ok=False` 且有可读的告警串（cron 据此发信）。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p51_daily_quality_monitor.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p51_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_count_of_buckets_are_exclusive():
    p51 = _load()
    items = [
        {"kind": "empty", "echo": None},
        {"kind": "grade_unparseable", "echo": None},
        {"kind": "lvef_unparseable", "echo": None},
        {"kind": "llm_unavailable", "echo": None},
        {"kind": "not_degraded", "echo": None},
        {"kind": "not_degraded", "echo": "placeholder_chunk_id"},  # 干净文本但被标记 → 计入 echo
    ]
    counts = p51.count_of(items)
    assert counts["total"] == 6
    assert counts["empty"] == 1
    assert counts["unparseable"] == 2
    assert counts["degraded"] == 1
    assert counts["clean"] == 2
    assert counts["echo"] == 1


def test_rates_none_when_empty_window():
    p51 = _load()
    rates = p51.rates_of({"total": 0, "empty": 0, "unparseable": 0, "echo": 0, "degraded": 0, "clean": 0})
    assert all(v is None for v in rates.values())


def test_rates_use_total_as_denominator():
    p51 = _load()
    counts = {"total": 10, "empty": 2, "unparseable": 1, "echo": 1, "degraded": 1, "clean": 5}
    rates = p51.rates_of(counts)
    assert rates["empty_rate"] == 0.2
    assert rates["unparseable_rate"] == 0.1
    assert rates["echo_rate"] == 0.1


def test_evaluate_flags_only_above_threshold():
    p51 = _load()
    ok = p51.evaluate({"empty_rate": 0.2, "unparseable_rate": 0.1, "echo_rate": 0.01, "degraded_rate": 0.05})
    assert ok["ok"] is True and ok["alerts"] == []  # 等于阈值不算超

    bad = p51.evaluate({"empty_rate": 0.5, "unparseable_rate": 0.0, "echo_rate": 0.0, "degraded_rate": 0.0})
    assert bad["ok"] is False
    assert "empty_rate=0.5 > 0.2" in bad["alerts"]


def test_window_filter_excludes_none():
    p51 = _load()
    cutoff = datetime(2026, 10, 13)
    assert p51.in_window(datetime(2026, 10, 20), cutoff) is True
    assert p51.in_window(datetime(2026, 10, 1), cutoff) is False
    assert p51.in_window(None, cutoff) is False
    assert p51.in_window(datetime(2026, 10, 13) - timedelta(seconds=1), cutoff) is False


def test_window_filter_handles_mixed_timezone():
    """真机踩坑：库里的时间是 aware，`datetime.now()` 是 naive → 直接比较会抛 TypeError。"""
    from datetime import timezone

    p51 = _load()
    cutoff_aware = datetime(2026, 10, 13, tzinfo=timezone.utc)
    assert p51.in_window(datetime(2026, 10, 20, tzinfo=timezone.utc), cutoff_aware) is True
    # naive 行与 aware cutoff 混用：不许抛异常
    assert p51.in_window(datetime(2026, 10, 20), cutoff_aware) is True
    assert p51.in_window(datetime(2026, 10, 1), cutoff_aware) is False


def test_thresholds_are_written_down_in_source():
    """阈值必须写死在脚本里（跑之前定好），不许运行时随便改。"""
    src = _SRC.read_text(encoding="utf-8")
    assert "THRESHOLDS" in src
    for key in ("empty_rate", "unparseable_rate", "echo_rate", "degraded_rate"):
        assert key in src
    assert "from app.inference.output_guard import detect_prompt_echo" in src
    assert "from p49_degraded_cause import degraded_kind" in src
