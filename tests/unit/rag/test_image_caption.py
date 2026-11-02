"""A.137 / G2：影像块视觉特征的可插拔来源（默认关闭、绝不编造）。

缺口：影像腿正文是空壳 `[影像参考] img_x；模态=…；部位=…`，没有成分/回声/边界/钙化（G2）。
本组守卫三件事：
1) payload 里有 `visual_features` → 渲染进正文并标注来源；没有 → 保持元数据模板（不编造）；
2) `describe()` 四个来源分支：visual_features / caption_model / none / caption_failed；
3) 端点默认空 → 不发任何请求（行为与现状一致）。
"""

from __future__ import annotations

import pytest

from app.config.settings import settings
from app.rag import image_caption as ic
from app.rag.retriever import _image_chunk_text

pytestmark = pytest.mark.track_rag


def test_chunk_text_renders_features_when_present():
    text = _image_chunk_text(
        "img_1",
        {
            "modality": "B-mode",
            "organ": "thyroid",
            "visual_features": ["低回声", "边界清"],
            "visual_features_source": "caption_model",
        },
    )
    assert "视觉特征=低回声；边界清" in text
    assert "特征来源=caption_model" in text


def test_chunk_text_stays_metadata_only_without_features():
    text = _image_chunk_text("img_2", {"modality": "B-mode", "organ": "liver"})
    assert "视觉特征" not in text
    assert text == "[影像参考] img_2；模态=B-mode；部位=liver"


def test_chunk_text_ignores_blank_feature_entries():
    text = _image_chunk_text("img_3", {"visual_features": ["  ", ""]})
    assert "视觉特征" not in text


@pytest.mark.asyncio
async def test_describe_prefers_existing_features():
    text, source = await ic.describe({"visual_features": "低回声结节"})
    assert (text, source) == ("低回声结节", "visual_features")


@pytest.mark.asyncio
async def test_describe_without_endpoint_returns_none(monkeypatch):
    monkeypatch.setattr(settings, "image_caption_endpoint", "")
    text, source = await ic.describe({"image_id": "i1"})
    assert (text, source) == ("", "none")


@pytest.mark.asyncio
async def test_describe_calls_captioner_when_given():
    class _Fake:
        async def caption(self, payload):
            return "低回声、边界不清"

    text, source = await ic.describe({"image_id": "i1"}, captioner=_Fake())
    assert (text, source) == ("低回声、边界不清", "caption_model")


@pytest.mark.asyncio
async def test_describe_failure_degrades_and_reports():
    class _Boom:
        async def caption(self, payload):
            raise RuntimeError("model down")

    text, source = await ic.describe({"image_id": "i1"}, captioner=_Boom())
    assert (text, source) == ("", "caption_failed")


@pytest.mark.asyncio
async def test_describe_empty_captioner_output_is_none():
    class _Empty:
        async def caption(self, payload):
            return "   "

    assert await ic.describe({}, captioner=_Empty()) == ("", "none")
