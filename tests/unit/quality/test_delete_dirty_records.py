"""A.70.8 守卫：删除历史脏记录必须「只删已打标 + 必须先备份 + 默认 dry-run」。

用户 2026-10-20 决定把 608 条脏记录**直接删掉**（原因与坑记文档）。
删数据不可逆，所以守卫要钉住三条硬约束，防止"判定规则漂移后删错一批"。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p50_delete_dirty_records.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p50_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_marked_reason_only_from_marker():
    """选择条件必须是**打标**，不能靠重新判定（规则漂移会删错）。"""
    p50 = _load()
    assert p50.marked_reason({"data_quality": {"prompt_echo": "placeholder_chunk_id"}}) == (
        "placeholder_chunk_id"
    )
    assert p50.marked_reason({"data_quality": {"other_tool": "x"}}) is None
    assert p50.marked_reason({"fusion_result": {"content": "看起来是回显 chunk_a1b2"}}) is None
    assert p50.marked_reason({}) is None
    assert p50.marked_reason(None) is None


def test_backup_rows_keep_full_findings():
    """备份必须含**完整 findings**，否则删了就无法恢复。"""
    p50 = _load()
    findings = {"fusion_result": {"content": "x"}, "data_quality": {"prompt_echo": "r"}}
    rows = [(("req-1"), "thyroid", datetime(2026, 10, 1, 12, 0, 0), findings)]
    out = p50.build_backup_rows(rows)
    assert out[0]["request_id"] == "req-1"
    assert out[0]["findings"] == findings
    assert out[0]["created_at"].startswith("2026-10-01T12:00")


def test_apply_requires_backup_and_never_rejudges():
    src = _SRC.read_text(encoding="utf-8")
    assert 'ap.add_argument("--apply", action="store_true"' in src
    # 备份必须真的写文件并在为空时拒绝
    assert "备份文件为空，拒绝删除" in src
    # 不重新判定：不许 import 输出守卫 / 不许出现占位符字面量
    assert "output_guard" not in src
    assert "chunk_a1b2" not in src and "PATIENT_001" not in src
    # 不自动级联删反馈
    assert "delete(FeedbackRecord)" not in src
