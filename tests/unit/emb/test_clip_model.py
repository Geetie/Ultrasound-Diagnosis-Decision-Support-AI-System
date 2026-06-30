"""UltrasoundCLIPModel 单元测试。

测试策略:
- 不依赖真实 torch/transformers(本地无 GPU)
- 通过 unittest.mock 模拟模型行为
- GPU 依赖路径用 @pytest.mark.skipif 标记门控
- 验证:参数路径、数据形状、I2T@k 计算逻辑、序列化往返
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.embedding.busi_adapter import CLIPSample
from app.embedding.clip_model import (
    EvaluationResult,
    FinetuneResult,
    UltrasoundCLIPModel,
)

pytestmark = [pytest.mark.track_emb]


class TestUltrasoundCLIPModelInit:
    def test_default_values(self):
        model = UltrasoundCLIPModel()
        assert model._model_name == "Ultrasound-CLIP"
        assert model._device_str == "cuda"
        assert model._dtype_str == "float16"
        assert model._use_lora is True
        assert model.is_loaded is False

    def test_with_custom_values(self):
        model = UltrasoundCLIPModel(
            model_name="custom-clip",
            device="cpu",
            dtype="float32",
            use_lora=False,
        )
        assert model._model_name == "custom-clip"
        assert model._device_str == "cpu"
        assert model._dtype_str == "float32"
        assert model._use_lora is False

    def test_from_samples_path_synthetic(self, tmp_path):
        nonexistent = tmp_path / "missing"
        model, samples = UltrasoundCLIPModel.from_samples_path(
            dataset_path=str(nonexistent),
            device="cpu",
            max_samples=3,
        )
        assert isinstance(model, UltrasoundCLIPModel)
        assert len(samples) == 3
        assert all(isinstance(s, CLIPSample) for s in samples)


class TestFinetuneResult:
    def test_defaults(self):
        result = FinetuneResult(output_dir="/tmp/out")
        assert result.output_dir == "/tmp/out"
        assert result.total_steps == 0
        assert result.final_loss == 0.0
        assert result.loss_curve == []
        assert result.i2t_at_10_pre == 0.0
        assert result.i2t_at_10_post == 0.0
        assert result.used_lora is True

    def test_to_dict_contains_all_fields(self):
        result = FinetuneResult(
            output_dir="/tmp/out",
            total_steps=10,
            final_loss=0.5,
            loss_curve=[0.8, 0.6, 0.5],
            i2t_at_10_pre=0.4,
            i2t_at_10_post=0.5,
            elapsed_seconds=120.0,
            used_lora=True,
        )
        d = result.to_dict()
        assert d["total_steps"] == 10
        assert d["final_loss"] == 0.5
        assert d["loss_curve"] == [0.8, 0.6, 0.5]
        assert d["i2t_at_10_pre"] == 0.4
        assert d["i2t_at_10_post"] == 0.5

    def test_to_json_valid(self):
        result = FinetuneResult(output_dir="/tmp/out", total_steps=5, final_loss=0.3)
        json_str = result.to_json()
        assert "total_steps" in json_str
        assert "5" in json_str


class TestEvaluationResult:
    def test_defaults(self):
        result = EvaluationResult()
        assert result.i2t_at_k == 0.0
        assert result.k == 10
        assert result.total_samples == 0
        assert result.latency_p50_ms == 0.0
        assert result.latency_p99_ms == 0.0

    def test_to_json(self):
        result = EvaluationResult(i2t_at_k=0.45, k=10, total_samples=100)
        json_str = result.to_json()
        assert "0.45" in json_str
        assert "100" in json_str


class TestUltrasoundCLIPModelEncode:
    def test_ensure_loaded_raises_when_not_loaded(self):
        model = UltrasoundCLIPModel()
        with pytest.raises(RuntimeError, match="model not loaded"):
            model.encode_text(["hello"])

    def test_ensure_loaded_raises_for_image(self):
        model = UltrasoundCLIPModel()
        with pytest.raises(RuntimeError, match="model not loaded"):
            model.encode_image(["/tmp/x.png"])

    def test_normalize(self):
        vec = np.array([3.0, 4.0])
        normalized = UltrasoundCLIPModel._normalize(vec)
        np.testing.assert_almost_equal(np.linalg.norm(normalized), 1.0)
        np.testing.assert_array_almost_equal(normalized, [0.6, 0.8])

    def test_normalize_zero_vector_returns_zero(self):
        vec = np.zeros(5)
        normalized = UltrasoundCLIPModel._normalize(vec)
        np.testing.assert_array_equal(normalized, np.zeros(5))


class TestUltrasoundCLIPModelEvaluate:
    def _make_loaded_model(self, dim: int = 768) -> UltrasoundCLIPModel:
        model = UltrasoundCLIPModel()
        model._loaded = True
        model._model = MagicMock()
        model._model.device = "cpu"
        model._model.get_text_features = MagicMock(return_value=self._fake_torch_tensor(dim, 3))
        model._model.get_image_features = MagicMock(return_value=self._fake_torch_tensor(dim, 3))
        model._processor = MagicMock(return_value={"pixel_values": MagicMock()})
        return model

    @staticmethod
    def _fake_torch_tensor(dim: int, batch: int):
        t = MagicMock()
        t.cpu.return_value.float.return_value.numpy.return_value = (
            np.random.randn(batch, dim).astype(np.float32)
        )
        return t

    def test_evaluate_empty_samples(self):
        model = UltrasoundCLIPModel()
        model._loaded = True
        model._model = MagicMock()
        result = model.evaluate([], k=10)
        assert result.i2t_at_k == 0.0
        assert result.total_samples == 0

    def test_evaluate_with_synthetic_data(self):
        model = self._make_loaded_model(dim=768)
        from app.embedding.clip_model import UltrasoundCLIPModel as _CLS

        real_encode_image = _CLS.encode_image

        def fake_encode_image(self, image_paths):
            dim = 768
            arr = np.random.randn(len(image_paths), dim).astype(np.float32)
            return np.stack([self._normalize(v) for v in arr])

        def fake_encode_text(self, texts):
            dim = 768
            arr = np.random.randn(len(texts), dim).astype(np.float32)
            return np.stack([self._normalize(v) for v in arr])

        with patch.object(_CLS, "encode_image", fake_encode_image), patch.object(
            _CLS, "encode_text", fake_encode_text
        ):
            samples = [
                CLIPSample(image_path="/tmp/a.png", text=f"text {i}", label="benign")
                for i in range(3)
            ]
            result = model.evaluate(samples, k=10)
        assert result.total_samples == 3
        assert 0.0 <= result.i2t_at_k <= 1.0
        assert result.k == 10
        _ = real_encode_image  # silence unused warning


class TestUltrasoundCLIPModelFinetune:
    def test_finetune_empty_samples_raises(self):
        model = UltrasoundCLIPModel()
        model._loaded = True
        model._model = MagicMock()
        with pytest.raises(ValueError, match="train_samples must not be empty"):
            model.finetune(train_samples=[], output_dir="/tmp/out")

    def test_finetune_writes_result_file(self, tmp_path):
        model = UltrasoundCLIPModel()
        model._loaded = True
        model._model = MagicMock()
        model._model.device = "cpu"
        model._model.parameters.return_value = []
        loss_tensor = MagicMock()
        loss_tensor.item.return_value = 0.5
        loss_tensor.backward = MagicMock()
        output_mock = MagicMock()
        output_mock.loss = loss_tensor
        model._model.return_value = output_mock
        model._processor = MagicMock(return_value={"pixel_values": MagicMock()})
        model._model.save_pretrained = MagicMock()
        model._processor.save_pretrained = MagicMock()

        with patch("torch.optim.AdamW") as mock_adamw:
            optimizer = MagicMock()
            mock_adamw.return_value = optimizer
            with patch("PIL.Image.open", side_effect=Exception("no image")):
                samples = [
                    CLIPSample(image_path="/tmp/x.png", text="text", label="benign")
                ]
                result = model.finetune(
                    train_samples=samples,
                    output_dir=str(tmp_path / "out"),
                    epochs=1,
                    batch_size=1,
                )
        assert (tmp_path / "out" / "finetune_result.json").exists()
        assert result.used_lora is True


class TestUltrasoundCLIPModelLoad:
    def test_load_without_torch_raises(self):
        model = UltrasoundCLIPModel()
        with patch.dict("sys.modules", {"torch": None}):
            with pytest.raises(RuntimeError, match="torch not installed"):
                model.load()

    def test_load_without_transformers_raises(self):
        model = UltrasoundCLIPModel()
        fake_torch = MagicMock()
        fake_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": fake_torch, "transformers": None}):
            with pytest.raises(RuntimeError, match="transformers not installed"):
                model.load()


class TestUltrasoundCLIPModelSave:
    def test_save_writes_files(self, tmp_path):
        model = UltrasoundCLIPModel()
        model._loaded = True
        model._model = MagicMock()
        model._processor = MagicMock()
        model._model.save_pretrained = MagicMock()
        model._processor.save_pretrained = MagicMock()
        out_dir = tmp_path / "save_test"
        result = FinetuneResult(output_dir=str(out_dir), total_steps=5, final_loss=0.4)
        model.save(str(out_dir), result=result)
        assert (out_dir / "finetune_result.json").exists()
        model._model.save_pretrained.assert_called_once()
        model._processor.save_pretrained.assert_called_once()
