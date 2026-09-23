"""BUSI 数据集适配器模块。

BUSI(Breast Ultrasound Images)数据集是公开的乳腺超声影像数据集,包含
benign(良性)、malignant(恶性)、normal(正常)三类。本适配器提供统一的
CLIPSample 数据加载接口,供微调脚本和评估脚本使用。

支持的数据源格式:
1. 标准 BUSI 目录结构: <dataset_path>/<class>/<id>.png
2. JSONL 格式: 每行 {"image_path": "...", "text": "...", "label": "..."}
3. HuggingFace datasets(parquet): 通过 --use-hf 启用

文本标注策略:
- 根据 label 生成模板文本,避免人工标注依赖
- 模板: "A breast ultrasound image showing {label} tissue"
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

import structlog

logger = structlog.get_logger("app.embedding.busi_adapter")

_TEXT_TEMPLATES = {
    "benign": "A breast ultrasound image showing benign tissue",
    "malignant": "A breast ultrasound image showing malignant mass",
    "normal": "A breast ultrasound image of normal breast tissue",
}

_VALID_LABELS = set(_TEXT_TEMPLATES.keys())


@dataclass
class CLIPSample:
    """CLIP 训练样本数据类。

    Attributes:
        image_path: 图像文件绝对路径
        text: 与图像配对的文本描述
        label: 类别标签(benign/malignant/normal)
    """

    image_path: str
    text: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"image_path": self.image_path, "text": self.text, "label": self.label}


class BUSIAdapter:
    """BUSI 数据集适配器。

    Args:
        dataset_path: 数据集根目录或 JSONL/parquet 文件路径
        use_hf: 是否使用 HuggingFace datasets 加载(parquet 后端)
    """

    def __init__(self, dataset_path: str, use_hf: bool = False):
        self._dataset_path = Path(dataset_path)
        self._use_hf = use_hf

    def load(
        self,
        split: str = "train",
        max_samples: int | None = None,
    ) -> list[CLIPSample]:
        """加载数据集样本列表。

        Args:
            split: 数据子集(train/val/test),仅用于 JSONL 文件名约定
            max_samples: 最大样本数(冒烟测试用)

        Returns:
            CLIPSample 列表
        """
        if self._use_hf:
            return list(self._iter_hf(split, max_samples))
        if not self._dataset_path.exists():
            logger.warning(
                "busi_dataset_path_not_found",
                path=str(self._dataset_path),
            )
            return self._generate_synthetic_samples(max_samples)
        if self._dataset_path.is_file() and self._dataset_path.suffix == ".jsonl":
            return list(self._iter_jsonl(split, max_samples))
        return list(self._iter_directory(max_samples))

    def _iter_directory(self, max_samples: int | None) -> Iterator[CLIPSample]:
        """遍历 BUSI 标准目录结构。"""
        count = 0
        for label in sorted(_VALID_LABELS):
            class_dir = self._dataset_path / label
            if not class_dir.is_dir():
                continue
            for img_path in sorted(class_dir.glob("*.png")):
                if max_samples is not None and count >= max_samples:
                    return
                yield CLIPSample(
                    image_path=str(img_path.absolute()),
                    text=_TEXT_TEMPLATES[label],
                    label=label,
                )
                count += 1
            for img_path in sorted(class_dir.glob("*.jpg")):
                if max_samples is not None and count >= max_samples:
                    return
                yield CLIPSample(
                    image_path=str(img_path.absolute()),
                    text=_TEXT_TEMPLATES[label],
                    label=label,
                )
                count += 1

    def _iter_jsonl(
        self, split: str, max_samples: int | None
    ) -> Iterator[CLIPSample]:
        """遍历 JSONL 格式数据集。"""
        jsonl_path = self._dataset_path
        if jsonl_path.is_dir():
            candidate = jsonl_path / f"{split}.jsonl"
            if candidate.exists():
                jsonl_path = candidate
            else:
                jsonl_path = next(jsonl_path.glob("*.jsonl"), jsonl_path)
        count = 0
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                if max_samples is not None and count >= max_samples:
                    return
                row = json.loads(line.strip())
                yield CLIPSample(
                    image_path=row["image_path"],
                    text=row["text"],
                    label=row.get("label", "benign"),
                )
                count += 1

    def _iter_hf(self, split: str, max_samples: int | None) -> Iterator[CLIPSample]:
        """通过 HuggingFace datasets 加载 parquet 数据集。"""
        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise RuntimeError(
                "datasets library required for HF backend: pip install datasets"
            ) from exc
        ds = load_dataset(str(self._dataset_path), split=split)
        count = 0
        for row in ds:
            if max_samples is not None and count >= max_samples:
                return
            yield CLIPSample(
                image_path=row.get("image_path", ""),
                text=row.get("text", ""),
                label=row.get("label", "benign"),
            )
            count += 1

    def _generate_synthetic_samples(self, max_samples: int | None) -> list[CLIPSample]:
        """生成合成样本占位(用于本地无数据集时的冒烟测试)。

        返回的 image_path 指向不存在的占位文件,仅用于流程验证,
        实际训练时必须替换为真实数据。
        """
        count = max_samples or 6
        samples: list[CLIPSample] = []
        labels = ["benign", "malignant", "normal"]
        for i in range(count):
            label = labels[i % len(labels)]
            samples.append(
                CLIPSample(
                    image_path=f"<synthetic>/{label}_{i}.png",
                    text=_TEXT_TEMPLATES[label],
                    label=label,
                )
            )
        logger.info(
            "busi_synthetic_samples_generated",
            count=len(samples),
            path=str(self._dataset_path),
        )
        return samples

    def write_jsonl(
        self,
        samples: list[CLIPSample],
        output_path: str,
    ) -> None:
        """将样本列表写入 JSONL 文件(便于跨工具共享)。"""
        with open(output_path, "w", encoding="utf-8") as f:
            for sample in samples:
                f.write(json.dumps(sample.to_dict(), ensure_ascii=False) + "\n")
        logger.info(
            "busi_jsonl_written",
            path=output_path,
            count=len(samples),
        )


def is_busi_path(path: str) -> bool:
    """快速判断给定路径是否可能是 BUSI 数据集(目录含 benign/malignant/normal 子目录)。"""
    if not os.path.isdir(path):
        return False
    entries = set(os.listdir(path))
    return bool({"benign", "malignant", "normal"} & entries)


__all__ = ["BUSIAdapter", "CLIPSample", "is_busi_path"]
