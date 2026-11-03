"""A.155：思考模型 <think> 兜底剥除（II-Medical-8B 真机实测不读 enable_thinking）。"""

from __future__ import annotations

import pytest

from app.inference.vllm import VLLMEngine
from app.inference.protocols import InferenceRequest

pytestmark = pytest.mark.track_api


def _engine():
    return VLLMEngine(base_url="http://127.0.0.1:1", default_model="m")


def _req():
    return InferenceRequest(prompt="p")


def test_closed_think_stripped():
    data = {"choices": [{"message": {"content": "<think>推理过程</think>分级: TR3"}, "finish_reason": "stop"}],
            "model": "m", "usage": {}}
    out = _engine()._parse_response(data, _req())
    assert out.text == "分级: TR3"


def test_unclosed_think_stripped_to_end():
    data = {"choices": [{"message": {"content": "<think>很长很长的推理没有闭合"}, "finish_reason": "length"}],
            "model": "m", "usage": {}}
    out = _engine()._parse_response(data, _req())
    assert out.text == ""


def test_normal_output_untouched():
    data = {"choices": [{"message": {"content": "分级: TR3"}, "finish_reason": "stop"}],
            "model": "m", "usage": {}}
    out = _engine()._parse_response(data, _req())
    assert out.text == "分级: TR3"


def test_think_with_newlines_stripped():
    data = {"choices": [{"message": {"content": "<think>第一行\n第二行\n</think>\n\n结论: TR4"}, "finish_reason": "stop"}],
            "model": "m", "usage": {}}
    out = _engine()._parse_response(data, _req())
    assert out.text == "结论: TR4"
