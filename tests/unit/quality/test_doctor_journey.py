"""A.85 守卫：医生视角走查必须"看得懂结果"，而不是只报 HTTP 码。

走查的价值在**翻译**：把 `GET /result` 的 JSON 翻成"医生屏幕上看到什么"。
翻错（比如把空结论显示成正常、把"未做分割"显示成"未检出"）比不翻更危险。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SRC = Path(__file__).resolve().parents[3] / "scripts" / "audit" / "p60_doctor_journey.py"


def _load():
    spec = importlib.util.spec_from_file_location("_p60_under_test", _SRC)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_doctor_view_summarises_what_doctor_sees():
    p60 = _load()
    view = p60._doctor_view(
        {
            "status": "completed",
            "diagnosis": {"fusion_result": {"content": "甲状腺 · TR2 — 分级: TR2\n证据: [1], [2]"}},
            "quality_control": {"confidence_score": 0.97},
            "requires_manual_review": False,
            "evidence": {"knowledge_ids": ["k1", "k2", "k3"]},
            "nodule_segmentation": {"available": False, "reason": "image_not_indexed"},
            "similar_cases": {"status": "ok", "count": 2},
        }
    )
    assert view["状态"] == "completed"
    assert view["一句话结论"].startswith("甲状腺 · TR2")
    assert view["证据条数"] == 3
    assert "未做" in view["分割"]          # 未做 ≠ 未检出
    assert view["相似病例"] == "2 条"


def test_doctor_view_flags_empty_conclusion_explicitly():
    p60 = _load()
    view = p60._doctor_view({"status": "completed", "diagnosis": {"fusion_result": {"content": ""}}})
    assert view["一句话结论"] == "（空）"


def test_doctor_view_reports_unavailable_similar_cases():
    p60 = _load()
    view = p60._doctor_view({"similar_cases": {"status": "unavailable", "count": 0}})
    assert view["相似病例"].startswith("无")


def test_journey_uses_real_schemas():
    """走查必须按真实契约调用（本轮踩过：reports→report、feedback 缺 content 结构）。"""
    src = _SRC.read_text(encoding="utf-8")
    assert '"/api/v1/report"' in src          # 单数前缀
    assert '"feedback_level"' in src and '"content"' in src
    assert "missing_from_session" not in src  # 提交侧校验由后端做，脚本不重复实现
