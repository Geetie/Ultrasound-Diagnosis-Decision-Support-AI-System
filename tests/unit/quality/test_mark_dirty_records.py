"""A.70.6 守卫：历史脏记录「打标保留」必须**不删数据、不碰业务字段、可回滚、默认 dry-run**。

用户 2026-10-20 拍板「打标保留」：不删历史判读，只在记录上留标记。
标记写在 `findings.data_quality`（不改 schema），前端读的 `fusion_result`/`recommendations` 原样保留。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p48_mark_dirty_records.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p48_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mark_keeps_business_fields_and_does_not_mutate_input():
    p48 = _load()
    findings = {
        "fusion_result": {"content": "结论正文"},
        "recommendations": "建议复查",
    }
    out = p48.mark_payload(findings, "placeholder_chunk_id", "2026-10-20 14:00:00")

    # 业务字段原样保留
    assert out["fusion_result"] == {"content": "结论正文"}
    assert out["recommendations"] == "建议复查"
    # 打标写入
    assert out["data_quality"]["prompt_echo"] == "placeholder_chunk_id"
    assert out["data_quality"]["marked_by"].startswith("p48_")
    # 不改入参
    assert "data_quality" not in findings


def test_mark_preserves_other_data_quality_keys():
    """别的工具也在 data_quality 里写东西时，不许被我覆盖掉。"""
    p48 = _load()
    findings = {"data_quality": {"other_tool": "x"}}
    out = p48.mark_payload(findings, "placeholder_patient_id", "t")
    assert out["data_quality"]["other_tool"] == "x"
    assert out["data_quality"]["prompt_echo"] == "placeholder_patient_id"


def test_unmark_removes_only_our_marker():
    p48 = _load()
    findings = {
        "fusion_result": {"content": "x"},
        "data_quality": {"prompt_echo": "placeholder_chunk_id", "marked_at": "t", "other_tool": "y"},
    }
    out, changed = p48.unmark_payload(findings)
    assert changed is True
    assert "prompt_echo" not in out["data_quality"]
    assert out["data_quality"]["other_tool"] == "y"  # 别人的标记保留
    assert out["fusion_result"] == {"content": "x"}


def test_unmark_drops_empty_data_quality_key():
    p48 = _load()
    findings = {"data_quality": {"prompt_echo": "x", "marked_at": "t", "marked_by": "y"}}
    out, changed = p48.unmark_payload(findings)
    assert changed is True
    assert "data_quality" not in out


def test_idempotent_check():
    p48 = _load()
    marked = p48.mark_payload({}, "placeholder_chunk_id", "t")
    assert p48.already_marked(marked, "placeholder_chunk_id") is True
    assert p48.already_marked(marked, "placeholder_patient_id") is False
    assert p48.already_marked({}, "placeholder_chunk_id") is False


def test_script_never_deletes_and_defaults_to_dry_run():
    src = _SRC.read_text(encoding="utf-8")
    upper = src.upper()
    assert "DELETE FROM" not in upper and ".DELETE(" not in upper, "打标保留：不许删数据"
    assert 'ap.add_argument("--apply", action="store_true"' in src, "--apply 必须显式开启"
    assert src.count("args.apply") >= 2  # 传进 _run 且用于打印模式
    assert "from app.inference.output_guard import detect_prompt_echo" in src
    assert "from collect_eval_set import _conclusion_of" in src
