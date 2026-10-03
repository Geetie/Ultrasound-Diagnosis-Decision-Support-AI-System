"""结节分割推理模块单测（不依赖 GPU 与真实权重）。

覆盖：预处理/逆变换几何正确性、缺权重降级、关开关降级、参数校验、
输出结构（掩膜 PNG/面积/bbox）、异常输入。
"""
from __future__ import annotations

import io

import pytest
from PIL import Image

pytestmark = [pytest.mark.track_inf]

from app.inference.nodule_segmentation import (
    INPUT_SIZE,
    NoduleSegmenter,
    SegmentationUnavailableError,
)


@pytest.fixture
def fake_sinseg(monkeypatch):
    """注入一个"中央亮斑"假分割器：中间 1/3 区域概率 1，其余 0。"""
    import torch

    seg = NoduleSegmenter.get()
    seg._net = lambda x: torch.full((1, 1, INPUT_SIZE, INPUT_SIZE), -10.0)
    # 让中间区域亮起来
    class _Net(torch.nn.Module):
        def forward(self, x):
            logits = torch.full((1, 1, INPUT_SIZE, INPUT_SIZE), -10.0)
            m = INPUT_SIZE // 3
            logits[:, :, INPUT_SIZE // 2 - m // 2: INPUT_SIZE // 2 + m // 2,
                   INPUT_SIZE // 2 - m // 2: INPUT_SIZE // 2 + m // 2] = 10.0
            return logits

    seg._net = _Net()
    seg._device = "cpu"
    seg._meta = {"input_size": INPUT_SIZE, "test_dice": 0.7961, "test_iou": 0.7004}
    yield seg
    # 复位单例，避免污染其他用例
    NoduleSegmenter._instance = None


def _img(w=300, h=200, color=128):
    return Image.new("RGB", (w, h), (color, color, color))


class TestPreprocess:
    def test_resizes_to_square(self):
        """实际行为：长边缩放到 INPUT_SIZE，短边被 max(256, …) 顶到 256
        → 等效于直接 resize 到 256×256（**不做补边/裁剪**）。与训练脚本一致：
        训练侧同一段代码，推理必须逐字复刻，否则掩膜会错位。"""
        x, params = NoduleSegmenter._preprocess(_img(300, 200))
        assert tuple(x.shape) == (3, INPUT_SIZE, INPUT_SIZE)
        assert params["orig_w"] == 300 and params["orig_h"] == 200
        assert params["scaled_w"] == INPUT_SIZE
        assert params["scaled_h"] == INPUT_SIZE
        assert params["left"] == 0 and params["top"] == 0

    def test_square_image(self):
        _, params = NoduleSegmenter._preprocess(_img(256, 256))
        assert params["scaled_w"] == INPUT_SIZE and params["scaled_h"] == INPUT_SIZE
        assert params["left"] == 0 and params["top"] == 0

    def test_extreme_aspect_ratio(self):
        _, params = NoduleSegmenter._preprocess(_img(1000, 100))
        assert params["scaled_w"] == INPUT_SIZE
        assert params["scaled_h"] == INPUT_SIZE


class TestInference:
    def test_returns_mask_and_metrics(self, fake_sinseg):
        buf = io.BytesIO()
        _img(300, 200).save(buf, format="PNG")
        r = fake_sinseg.infer(image_bytes=buf.getvalue())
        assert r.width == 300 and r.height == 200
        assert r.lesion_area_ratio > 0
        assert r.bbox is not None
        x0, y0, x1, y1 = r.bbox
        assert 0 <= x0 < x1 <= 300 and 0 <= y0 < y1 <= 200
        # 掩膜 PNG 可解码且尺寸一致
        mask = Image.open(io.BytesIO(r.mask_png))
        assert mask.size == (300, 200)
        assert r.device == "cpu"
        assert r.model_dice_official == 0.7961

    def test_output_size_override(self, fake_sinseg):
        buf = io.BytesIO()
        _img(300, 200).save(buf, format="PNG")
        r = fake_sinseg.infer(image_bytes=buf.getvalue(), out_size=(128, 96))
        assert (r.width, r.height) == (128, 96)
        assert Image.open(io.BytesIO(r.mask_png)).size == (128, 96)

    def test_threshold_all_positive_gives_full_mask(self, fake_sinseg):
        buf = io.BytesIO()
        _img(200, 200).save(buf, format="PNG")
        r = fake_sinseg.infer(image_bytes=buf.getvalue(), threshold=0.0)
        assert r.lesion_area_ratio == pytest.approx(1.0, abs=1e-3)

    def test_no_lesion_returns_none_bbox(self, fake_sinseg):
        import torch

        class _Empty(torch.nn.Module):
            def forward(self, x):
                return torch.full((1, 1, INPUT_SIZE, INPUT_SIZE), -10.0)

        fake_sinseg._net = _Empty()
        buf = io.BytesIO()
        _img(200, 200).save(buf, format="PNG")
        r = fake_sinseg.infer(image_bytes=buf.getvalue())
        assert r.bbox is None
        assert r.lesion_area_ratio == 0.0

    def test_requires_exactly_one_source(self, fake_sinseg):
        with pytest.raises(ValueError):
            fake_sinseg.infer()
        with pytest.raises(ValueError):
            fake_sinseg.infer(image_path="x.png", image_bytes=b"y")

    def test_grayscale_image_supported(self, fake_sinseg):
        buf = io.BytesIO()
        Image.new("L", (150, 150), 200).save(buf, format="PNG")
        r = fake_sinseg.infer(image_bytes=buf.getvalue())
        assert r.width == 150


class TestDegradation:
    def test_missing_weights_raises_unavailable(self, monkeypatch):
        monkeypatch.setattr(
            "app.inference.nodule_segmentation._model_path",
            lambda: __import__("pathlib").Path("/nonexistent/unet_tn3k.pt"),
        )
        seg = NoduleSegmenter.get()
        seg._net = None
        with pytest.raises(SegmentationUnavailableError, match="权重不存在"):
            seg._ensure_loaded()
        NoduleSegmenter._instance = None

    def test_disabled_switch_raises_unavailable(self, monkeypatch):
        from app.config.settings import settings

        monkeypatch.setattr(settings, "nodule_segmentation_enabled", False, raising=False)
        seg = NoduleSegmenter.get()
        seg._net = None
        with pytest.raises(SegmentationUnavailableError, match="已关闭"):
            seg._ensure_loaded()
        NoduleSegmenter._instance = None

    def test_health_reports_unavailable_without_raising(self, monkeypatch):
        from pathlib import Path

        monkeypatch.setattr(
            "app.inference.nodule_segmentation._model_path",
            lambda: Path("/nonexistent/unet_tn3k.pt"),
        )
        seg = NoduleSegmenter.get()
        seg._net = None
        h = seg.health()
        assert h["available"] is False and "权重不存在" in h["reason"]
        NoduleSegmenter._instance = None
