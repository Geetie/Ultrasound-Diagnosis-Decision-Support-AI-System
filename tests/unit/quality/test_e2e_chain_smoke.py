"""A.76 守卫：端到端链路判定必须"认得出四种坏结果"。

发真请求的意义全在**判得准**：正文空 / 可读降级 / 模板回显 / 缺分级，四者都算链路未通过，
但不能互相混淆（否则又会得到"看起来 OK"的假结论）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p57_e2e_chain_smoke.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p57_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_clean_graded_result_passes():
    p57 = _load()
    out = p57.chain_of(
        {
            "status": "completed",
            "confidence": 0.85,
            "organ": "thyroid",
            "findings": {"fusion_result": {"content": "分级: TR4 理由: 实性低回声，边界清。"}},
        }
    )
    assert out["link_ok"] is True
    assert out["graded"] is True
    assert out["degraded"] is False


def test_readable_degraded_result_is_still_fail():
    p57 = _load()
    out = p57.chain_of(
        {
            "status": "failed",
            "organ": "thyroid",
            "findings": {
                "fusion_result": {"content": "本次判读未完成（X），需人工复核", "degraded": True}
            },
        }
    )
    assert out["has_body"] is True
    assert out["degraded"] is True
    assert out["link_ok"] is False


def test_prompt_echo_is_fail_even_if_graded():
    p57 = _load()
    out = p57.chain_of(
        {
            "organ": "thyroid",
            "findings": {"fusion_result": {"content": "分级: TR5 证据: chunk_a1b2, chunk_c3d4"}},
        }
    )
    assert out["prompt_echo"] is True
    assert out["link_ok"] is False


def test_missing_grade_is_fail_for_thyroid_and_cardiac():
    p57 = _load()
    t = p57.chain_of({"organ": "thyroid", "findings": {"fusion_result": {"content": "结节建议复查"}}})
    c = p57.chain_of({"organ": "cardiac", "findings": {"fusion_result": {"content": "心功能尚可"}}})
    assert t["graded"] is False and t["link_ok"] is False
    assert c["graded"] is False and c["link_ok"] is False


def test_liver_only_needs_body():
    p57 = _load()
    out = p57.chain_of({"organ": "liver", "findings": {"fusion_result": {"content": "建议增强 CT"}}})
    assert out["graded"] is True and out["link_ok"] is True


def test_missing_fields_do_not_raise():
    p57 = _load()
    assert p57.chain_of({})["link_ok"] is False
    assert p57.chain_of({"findings": None})["link_ok"] is False
