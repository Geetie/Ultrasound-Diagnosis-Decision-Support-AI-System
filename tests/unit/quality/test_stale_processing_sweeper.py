"""A.73 守卫：清扫"卡在处理中"的判读，必须**只动超时未完成的行**、可回滚、默认 dry-run。

真机取证：411 条里 87 条 `findings` 永远是 `{"status": "processing"}`、20 条是 NULL ——
医生看到空白报告且状态永远"处理中"。清扫的意义是把它们变成"明确失败 + 一句实话"。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SRC = (
    Path(__file__).resolve().parents[3] / "scripts" / "ops" / "sweep_stale_processing.py"
)


def _load():
    spec = importlib.util.spec_from_file_location("_sweep_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_processing_placeholder_or_null_is_stale():
    sw = _load()
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    old = now - timedelta(minutes=45)
    assert sw.is_stale({"status": "processing"}, old, now, 30) is True
    assert sw.is_stale(None, old, now, 30) is True
    # 已有结论的行绝不能被清扫
    assert sw.is_stale({"fusion_result": {"content": "结论"}}, old, now, 30) is False
    assert sw.is_stale({"status": "completed"}, old, now, 30) is False
    # 还没超时的不动（可能正在跑）
    fresh = now - timedelta(minutes=5)
    assert sw.is_stale({"status": "processing"}, fresh, now, 30) is False


def test_naive_created_at_is_aligned_not_crashing():
    sw = _load()
    now = datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc)
    naive_old = datetime(2026, 10, 20, 10, 0)  # 无时区
    assert sw.is_stale({"status": "processing"}, naive_old, now, 30) is True


def test_sweeper_is_dry_run_by_default_and_backs_up():
    src = _SRC.read_text(encoding="utf-8")
    assert 'ap.add_argument("--apply", action="store_true"' in src
    assert "备份文件为空，拒绝清扫" in src
    # 清扫后必须写入可读正文（复用统一文案，不自己编）
    assert "from app.services.degraded_texts import stale_processing_text" in src
    # 不许删数据
    assert "DELETE FROM" not in src.upper()


def test_degraded_texts_are_readable_and_hide_tech_details():
    from app.services.degraded_texts import (
        no_conclusion_text,
        pipeline_failed_text,
        stale_processing_text,
    )

    for text in (
        pipeline_failed_text("InterfaceError"),
        stale_processing_text(30),
        no_conclusion_text("空结论"),
    ):
        assert "复核" in text
        assert "chunk_" not in text and "PATIENT_" not in text
        assert len(text) <= 120
