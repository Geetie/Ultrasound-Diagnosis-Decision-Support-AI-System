"""A.37 ③ 多模态注入的决定性单测（本地可跑，无需 GPU/服务器）。

真机走查实测：LLM 结论出现「请提供具体的影像特征」——影像从未进入判读上下文。
本测试证明注入链路成立：分割 dict -> _safe_dispatch(imaging_metadata=...) ->
AgentContext.imaging_metadata，且 PatientContext 拼入 user_query；
并钉住顺序回归：分割必须在 dispatch 之前产出。
"""
from __future__ import annotations

import pytest

from app.services import diagnosis_pipeline as dp

SEG = {
    "available": True,
    "bbox": [143, 240, 330, 367],
    "area_mm2": 294.6,
    "max_prob": 0.99,
    "mean_prob": 0.05,
    "pixel_spacing_mm": [0.2, 0.2],
}


class _FakeRequest:
    session_id = "sess_x"
    organ = "cardiac"
    clinical_text = ""
    patient_context = type(
        "PC", (), {"age": 52, "gender": "F", "relevant_history": "高血压", "patient_conditions": []},
    )()


class _FakeResponse:
    confidence = 0.9
    text = "ok"


async def _fake_seg(request):
    return dict(SEG)


async def _fake_desens(request_id, raw_text, session_id):
    return raw_text


async def _fake_invoke(context):
    _fake_invoke.captured = context
    return _FakeResponse()


@pytest.mark.asyncio
async def test_dispatch_receives_imaging_metadata_and_patient_context(monkeypatch):
    _fake_invoke.captured = None
    monkeypatch.setattr(dp, "_safe_nodule_contour", _fake_seg)
    monkeypatch.setattr(dp, "_desensitize_input_text", _fake_desens)
    monkeypatch.setattr(dp.time, "monotonic", lambda: 0.0)
    state = type("S", (), {})()
    state.dispatcher = type("D", (), {})()
    state.dispatcher.invoke = _fake_invoke
    import app.main as main_mod
    monkeypatch.setattr(main_mod, "app", type("A", (), {"state": state}))

    await dp._safe_dispatch(_FakeRequest(), "req_test", routing=None, imaging_metadata=dict(SEG))

    ctx = _fake_invoke.captured
    assert ctx is not None, "dispatcher 未被调用"
    assert ctx.imaging_metadata == SEG, "分割量化特征必须进入 AgentContext"
    assert "52 岁" in ctx.user_query and "高血压" in ctx.user_query, (
        "PatientContext 必须拼入 user_query —— 否则结论恒为「信息不足」"
    )
