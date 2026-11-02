"""A.112 / G13：器官阈值打标必须有观测面（"误杀率"不再无从查）。

缺口：`specialist/base.py` 低于阈值时只写 reasoning_trace + needs_review + 一行日志，
**没有任何指标** —— 阈值是否过严、哪个器官误杀多，运维与临床都看不到。
本组守卫钉：①计数器存在且带 organ/scope 标签；②采集器方法真的 +1；
③判读路径真的调用（源码级）；④指标失败不影响判读（有 silent-ok 豁免 + warning）。
"""

from __future__ import annotations

import inspect

from app.observability.metrics_collector import get_metrics_collector


def test_counter_defined_with_labels():
    collector = get_metrics_collector()
    metric = collector.organ_threshold_below
    # prometheus_client 的 Counter 内部 _name 不带 _total 后缀（暴露时才拼上）
    assert metric._name == "ultrasound_organ_threshold_below"
    assert metric._labelnames == ("organ", "scope")


def test_exposed_name_has_total_suffix():
    collector = get_metrics_collector()
    collector.record_organ_threshold_below("thyroid", "organ")
    assert 'ultrasound_organ_threshold_below_total{organ="thyroid",scope="organ"}' in collector.get_metrics_text()


def test_record_increments_labeled_counter():
    collector = get_metrics_collector()
    before = collector.organ_threshold_below.labels(organ="liver", scope="disease")._value.get()
    collector.record_organ_threshold_below("liver", "disease")
    after = collector.organ_threshold_below.labels(organ="liver", scope="disease")._value.get()
    assert after == before + 1


def test_unknown_labels_do_not_explode():
    collector = get_metrics_collector()
    collector.record_organ_threshold_below(None, None)  # type: ignore[arg-type]
    text = collector.get_metrics_text()
    assert 'organ="unknown",scope="unknown"' in text


def test_specialist_path_calls_it():
    """防回退：低于阈值的分支必须真的上报（否则指标恒 0，等于没接）。"""
    from app.agents.specialist import base as specialist_base

    src = inspect.getsource(specialist_base.SpecialistAgent.__dict__["_apply_organ_threshold"]) \
        if "_apply_organ_threshold" in specialist_base.SpecialistAgent.__dict__ else inspect.getsource(specialist_base)
    assert "record_organ_threshold_below" in src
    assert "organ_threshold_metric_failed" in src
