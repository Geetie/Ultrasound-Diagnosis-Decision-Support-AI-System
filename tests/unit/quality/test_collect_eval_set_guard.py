"""A.70 守卫：采集评测集时必须**拒收**复述提示词示例的结论。

真机教训：现有 169 条评测样本里 **155 条（98.7%）** 是模板照抄 —— 采集端如果不过滤，
脏样本会持续进入评测集，之后再讨论任何门限都是拿脏数据说话。

本守卫只做**静态+行为**两层约束（采集脚本要连库，不在单测里跑）：
1. 源码必须调用**生产守卫**（`app.inference.output_guard.detect_prompt_echo`），不另写规则；
2. 拒收必须**计数**（`skipped_prompt_echo` + 原因分布），不许静默丢样本。
"""

from __future__ import annotations

from pathlib import Path

_SRC = (
    Path(__file__).resolve().parents[3] / "scripts" / "langfuse" / "collect_eval_set.py"
)


def test_collector_uses_production_guard():
    src = _SRC.read_text(encoding="utf-8")
    assert "from app.inference.output_guard import detect_prompt_echo" in src
    assert "detect_prompt_echo(conclusion)" in src
    # 不许在采集脚本里另写一套占位符规则
    assert "chunk_a1b2" not in src
    assert "PATIENT_001" not in src


def test_rejected_samples_are_counted_not_silently_dropped():
    src = _SRC.read_text(encoding="utf-8")
    assert "skipped_prompt_echo" in src
    assert "prompt_echo_reasons" in src
    # 统计必须进报告与摘要两处（否则运维只看到"条数少了"却不知道为什么）
    assert src.count("skipped_prompt_echo") >= 3
