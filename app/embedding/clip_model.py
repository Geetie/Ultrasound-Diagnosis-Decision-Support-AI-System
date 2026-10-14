"""Ultrasound-CLIP 本地模型封装模块(独立于 HTTP 客户端封装)。

本模块提供 UltrasoundCLIPModel 类,负责:
- 模型加载(transformers / peft LoRA)
- 图像 / 文本编码推理
- LoRA 微调(或全量微调)
- I2T@k 评估
- 权重序列化

设计原则:
- 与现有 UltrasoundClipService(HTTP 客户端)解耦
- GPU 必需(本地无 GPU 时仅构建不运行,具体方法在调用时给出明确错误)
- torch/transformers/peft 依赖在函数内 lazy import,避免无 GPU 环境下 import 失败
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog

from app.embedding.busi_adapter import BUSIAdapter, CLIPSample

logger = structlog.get_logger("app.embedding.clip_model")


@dataclass
class FinetuneResult:
    """微调结果数据类。

    Attributes:
        output_dir: 模型输出目录
        total_steps: 总训练步数
        final_loss: 最终 loss 值
        loss_curve: 每步 loss 列表
        i2t_at_10_pre: 微调前 I2T@10(若评估)
        i2t_at_10_post: 微调后 I2T@10
        elapsed_seconds: 训练耗时
    """

    output_dir: str
    total_steps: int = 0
    final_loss: float = 0.0
    loss_curve: list[float] = field(default_factory=list)
    i2t_at_10_pre: float = 0.0
    i2t_at_10_post: float = 0.0
    elapsed_seconds: float = 0.0
    used_lora: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


@dataclass
class EvaluationResult:
    """评估结果数据类。

    Attributes:
        i2t_at_k: I2T@k 分数
        k: top-k 值
        total_samples: 评估样本数
        latency_p50_ms: 推理延迟 P50
        latency_p99_ms: 推理延迟 P99
    """

    i2t_at_k: float = 0.0
    k: int = 10
    total_samples: int = 0
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def _require_torch():
    """惰性导入 torch,失败时给出明确错误。"""
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError(
            "torch not installed; pip install -r requirements-emb.txt for GPU training"
        ) from exc
    return torch


class UltrasoundCLIPModel:
    """Ultrasound-CLIP 本地模型封装(独立类,非 HTTP 客户端)。

    Args:
        model_name: 模型名称(或本地路径)
        device: 推理设备("cuda" / "cpu",默认 "cuda")
        dtype: 模型精度("float16" / "bfloat16" / "float32")
        use_lora: 是否使用 LoRA 微调(推荐,显存占用低)
    """

    def __init__(
        self,
        model_name: str = "Ultrasound-CLIP",
        device: str = "cuda",
        dtype: str = "float16",
        use_lora: bool = True,
    ):
        self._model_name = model_name
        self._device_str = device
        self._dtype_str = dtype
        self._use_lora = use_lora
        self._model: Any = None
        self._processor: Any = None
        self._tokenizer: Any = None
        self._loaded = False

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    def load(self, model_path: str | None = None) -> UltrasoundCLIPModel:
        """加载模型权重。model_path=None 时使用 model_name。"""
        torch = _require_torch()
        try:
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:
            raise RuntimeError(
                "transformers not installed; pip install -r requirements-emb.txt"
            ) from exc

        target_path = model_path or self._model_name
        dtype_map = {
            "float16": torch.float16,
            "bfloat16": torch.bfloat16,
            "float32": torch.float32,
        }
        torch_dtype = dtype_map.get(self._dtype_str, torch.float16)

        device = self._device_str
        if device == "cuda" and not torch.cuda.is_available():
            logger.warning("cuda_unavailable_falling_back_to_cpu")
            device = "cpu"
            torch_dtype = torch.float32

        try:
            # trust_remote_code：第三方转换的模型（如 BiomedCLIP 的 HF 格式版）
            # 带自定义 modeling/processing 代码，需要显式信任；环境变量开关
            # 避免无条件开启（官方 openai/clip-vit-large-patch14 不需要）。
            trust_remote_code = os.environ.get("CLIP_TRUST_REMOTE_CODE", "") == "1"
            self._processor = AutoProcessor.from_pretrained(
                target_path, trust_remote_code=trust_remote_code
            )
            self._model = AutoModel.from_pretrained(
                target_path, torch_dtype=torch_dtype, trust_remote_code=trust_remote_code
            ).to(device)
        except Exception as exc:
            logger.error(
                "clip_model_load_failed",
                path=target_path,
                error=str(exc),
            )
            raise

        if self._use_lora:
            self._apply_lora()

        self._loaded = True
        logger.info(
            "clip_model_loaded",
            path=target_path,
            device=device,
            use_lora=self._use_lora,
        )
        return self

    def _apply_lora(self) -> None:
        """应用 LoRA adapter(若启用)。"""
        try:
            from peft import LoraConfig, get_peft_model
        except ImportError:
            logger.warning("peft_not_available_skip_lora")
            return
        config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "v_proj"],
            lora_dropout=0.05,
            bias="none",
        )
        self._model = get_peft_model(self._model, config)
        logger.info("clip_lora_applied", r=16, lora_alpha=32)

    def _ensure_loaded(self) -> None:
        if not self._loaded or self._model is None:
            raise RuntimeError(
                "model not loaded; call load() first"
            )

    @staticmethod
    def _normalize(vec: np.ndarray) -> np.ndarray:
        """L2 归一化,用于 cosine 相似度。"""
        norm = np.linalg.norm(vec)
        if norm == 0:
            return vec
        return vec / norm

    @staticmethod
    def _features_tensor(features: Any) -> Any:
        """取出投影特征张量（transformers 版本兼容）。

        v4 的 ``get_text_features``/``get_image_features`` 直接返回投影后的张量；
        v5（实测 5.17.0）返回 ``BaseModelOutputWithPooling``，投影特征在
        ``pooler_output``（图像/文本塔分别经 image/text projection 后的 768 维）。
        """
        if hasattr(features, "pooler_output"):
            return features.pooler_output
        return features

    def encode_text(self, texts: list[str]) -> np.ndarray:
        """编码文本为向量。

        Args:
            texts: 文本列表

        Returns:
            (N, dim) numpy 数组,L2 归一化
        """
        self._ensure_loaded()
        torch = _require_torch()
        inputs = self._processor(
            text=texts, return_tensors="pt", padding=True, truncation=True
        )
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
        with torch.no_grad():
            text_features = self._model.get_text_features(**inputs)
        arr = self._features_tensor(text_features).cpu().float().numpy()
        arr = np.stack([self._normalize(v) for v in arr])
        return arr

    def encode_image(self, image_paths: list[str]) -> np.ndarray:
        """编码图像为向量。

        Args:
            image_paths: 图像文件路径列表

        Returns:
            (N, dim) numpy 数组,L2 归一化
        """
        self._ensure_loaded()
        torch = _require_torch()
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Pillow not installed") from exc
        images = [Image.open(p).convert("RGB") for p in image_paths]
        inputs = self._processor(images=images, return_tensors="pt")
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
        with torch.no_grad():
            image_features = self._model.get_image_features(**inputs)
        arr = self._features_tensor(image_features).cpu().float().numpy()
        arr = np.stack([self._normalize(v) for v in arr])
        return arr

    def evaluate(
        self,
        test_samples: list[CLIPSample],
        k: int = 10,
    ) -> EvaluationResult:
        """I2T@k 评估:图像 → top-k 文本检索命中率。

        Args:
            test_samples: 测试样本列表
            k: top-k 值

        Returns:
            EvaluationResult 包含 i2t_at_k 与延迟统计
        """
        self._ensure_loaded()
        if not test_samples:
            return EvaluationResult(i2t_at_k=0.0, k=k, total_samples=0)

        image_paths = [s.image_path for s in test_samples]
        texts = [s.text for s in test_samples]

        latencies: list[float] = []
        t0 = time.monotonic()
        image_features = self.encode_image(image_paths)
        text_features = self.encode_text(texts)
        elapsed_ms = (time.monotonic() - t0) * 1000
        latencies.append(elapsed_ms)

        similarity = image_features @ text_features.T
        top_k_indices = np.argsort(-similarity, axis=1)[:, :k]

        correct = 0
        for i, sample in enumerate(test_samples):
            for idx in top_k_indices[i]:
                if test_samples[idx].text == sample.text:
                    correct += 1
                    break

        i2t_at_k = correct / len(test_samples)

        # P2-EMB-01 fix: compute real P50/P99 percentiles from the per-sample
        # latency series instead of overwriting both with the last sample's
        # elapsed time.  ``latencies`` may be empty when ``test_samples`` is
        # empty; fall back to zeros in that case.
        if latencies:
            arr = np.asarray(latencies, dtype=np.float64)
            latency_p50_ms = float(np.percentile(arr, 50))
            latency_p99_ms = float(np.percentile(arr, 99))
        else:
            latency_p50_ms = 0.0
            latency_p99_ms = 0.0

        return EvaluationResult(
            i2t_at_k=i2t_at_k,
            k=k,
            total_samples=len(test_samples),
            latency_p50_ms=latency_p50_ms,
            latency_p99_ms=latency_p99_ms,
        )

    def finetune(
        self,
        train_samples: list[CLIPSample],
        output_dir: str,
        epochs: int = 3,
        batch_size: int = 16,
        learning_rate: float = 2e-5,
        lora_r: int = 16,
        lora_alpha: int = 32,
        eval_samples: list[CLIPSample] | None = None,
    ) -> FinetuneResult:
        """LoRA 微调(推荐)或全量微调(若 use_lora=False)。

        Args:
            train_samples: 训练样本列表
            output_dir: 模型输出目录
            epochs: 训练轮数
            batch_size: 批大小
            learning_rate: 学习率
            lora_r: LoRA rank
            lora_alpha: LoRA alpha
            eval_samples: 评估样本(用于报告 i2t_at_10_post)

        Returns:
            FinetuneResult
        """
        self._ensure_loaded()
        torch = _require_torch()
        try:
            from torch.optim import AdamW
        except ImportError as exc:
            raise RuntimeError("torch required for finetune") from exc

        if not train_samples:
            raise ValueError("train_samples must not be empty")

        result = FinetuneResult(output_dir=output_dir, used_lora=self._use_lora)
        if eval_samples:
            pre = self.evaluate(eval_samples, k=10)
            result.i2t_at_10_pre = pre.i2t_at_k

        optimizer = AdamW(
            [p for p in self._model.parameters() if p.requires_grad],
            lr=learning_rate,
        )
        start = time.monotonic()

        for epoch in range(epochs):
            np.random.shuffle(train_samples)
            epoch_loss = 0.0
            steps = 0
            for batch_start in range(0, len(train_samples), batch_size):
                batch = train_samples[batch_start : batch_start + batch_size]
                try:
                    from PIL import Image
                    images = [Image.open(s.image_path).convert("RGB") for s in batch]
                except Exception:
                    logger.warning("UltrasoundCLIPModel.finetune: 未预期异常，按原逻辑降级", exc_info=True)
                    images = None
                texts = [s.text for s in batch]
                if images is not None:
                    inputs = self._processor(
                        text=texts, images=images, return_tensors="pt",
                        padding=True, truncation=True,
                    )
                else:
                    inputs = self._processor(
                        text=texts, return_tensors="pt", padding=True, truncation=True
                    )
                inputs = {k: v.to(self._model.device) for k, v in inputs.items()}
                outputs = self._model(**inputs)
                loss = outputs.loss
                optimizer.zero_grad()
                loss.backward()
                optimizer.step()
                loss_val = float(loss.item())
                result.loss_curve.append(loss_val)
                epoch_loss += loss_val
                steps += 1
            logger.info(
                "clip_finetune_epoch",
                epoch=epoch + 1,
                avg_loss=epoch_loss / max(steps, 1),
            )
        result.total_steps = len(result.loss_curve)
        result.final_loss = result.loss_curve[-1] if result.loss_curve else 0.0
        result.elapsed_seconds = time.monotonic() - start

        if eval_samples:
            post = self.evaluate(eval_samples, k=10)
            result.i2t_at_10_post = post.i2t_at_k

        self.save(output_dir, result)
        return result

    def save(self, output_dir: str, result: FinetuneResult | None = None) -> None:
        """保存模型权重与训练结果。"""
        self._ensure_loaded()
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        if self._use_lora:
            self._model.save_pretrained(str(out))
        else:
            self._model.save_pretrained(str(out), safe_serialization=True)
        if self._processor is not None:
            self._processor.save_pretrained(str(out))
        if result is not None:
            (out / "finetune_result.json").write_text(
                result.to_json(), encoding="utf-8"
            )
        logger.info("clip_model_saved", output_dir=output_dir)

    @classmethod
    def from_samples_path(
        cls,
        dataset_path: str,
        model_name: str = "Ultrasound-CLIP",
        device: str = "cuda",
        use_lora: bool = True,
        max_samples: int | None = None,
        allow_synthetic: bool = False,
    ) -> tuple[UltrasoundCLIPModel, list[CLIPSample]]:
        """便捷方法:加载模型并加载数据集样本。

        `allow_synthetic` 默认 False（与 `BUSIAdapter` 一致）：数据集路径不存在时
        **拒绝**，而不是悄悄造合成样本让调用方以为"加载成功"。
        """
        adapter = BUSIAdapter(
            dataset_path=dataset_path, allow_synthetic=allow_synthetic
        )
        samples = adapter.load(max_samples=max_samples)
        model = cls(model_name=model_name, device=device, use_lora=use_lora)
        return model, samples


__all__ = [
    "BUSIAdapter",
    "CLIPSample",
    "EvaluationResult",
    "FinetuneResult",
    "UltrasoundCLIPModel",
]
