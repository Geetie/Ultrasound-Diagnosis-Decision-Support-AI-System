"""A.75 守卫：parse 环取证必须能"分清三种死法"。

`p55` 显示 9 条死在 parse，而重试机制**代码里本来就有**——
所以要能区分：①plain 被 max_tokens 截断 ②结构化输出不是 JSON ③结构化能取到但生产没接上。
分不清这三种，就会又去"改提示词"而实际上该修的是接线。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p56_parse_link_probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p56_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plain_extraction_matches_production_rules():
    p56 = _load()
    assert p56.extract_plain("分级: TR4 理由: …", "thyroid") == "TR4"
    assert p56.extract_plain("TI-RADS 5 类", "thyroid") is None  # 生产首抽只认 TRn，不认 5 类
    assert p56.extract_plain("LVEF: 45% 分级: mildly_reduced", "cardiac") == "lvef_present"
    assert p56.extract_plain("分级: moderately_reduced", "cardiac") == "moderately_reduced"
    assert p56.extract_plain("看不出", "liver") is None


def test_structured_json_parsing_tolerates_fences():
    p56 = _load()
    assert p56.parse_structured_json('```json\n{"grading": "4b"}\n```') == {"grading": "4b"}
    assert p56.parse_structured_json('前言 {"grading":"tr3"} 后语') == {"grading": "tr3"}
    assert p56.parse_structured_json("不是 JSON") is None
    assert p56.parse_structured_json("") is None


def test_grading_of_accepts_several_key_names():
    p56 = _load()
    assert p56.grading_of({"grading": "4b"}) == "4b"
    assert p56.grading_of({"severity": "mildly_reduced"}) == "mildly_reduced"
    assert p56.grading_of({"other": 1}) is None
    assert p56.grading_of(None) is None


def test_probe_uses_production_schema_and_readonly():
    src = _SRC.read_text(encoding="utf-8")
    assert "from app.inference.grading_schema import grading_json_schema" in src
    assert '"response_format"' in src  # 生产同款（AGENTS #5：不用 guided_json）
    upper = src.upper()
    assert "UPDATE " not in upper and "DELETE FROM" not in upper
