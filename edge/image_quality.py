"""M6 图像质量评估适配器（边缘端）。

与 :mod:`edge.plane_assessment` 同样的处理方式：真机质量评分头（≤100MB、
≤3ms 的轻量头）无法在开发机验证，因此**只定义契约并交付本地确定性假实现**，
真机实现由现场注入。

配置开关（见 ``edge/config.py``）：

    EDGE_IMAGE_QUALITY_ADAPTER=null        不产出质量评估（默认，安全）
    EDGE_IMAGE_QUALITY_ADAPTER=local_fake  本地假实现（仅开发/测试）

.. warning::
   本地假实现的输出**没有临床意义**，仅用于打通链路与单测。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np
import structlog

from edge.protocols import Frame, ImageQuality

logger = structlog.get_logger(__name__)

# 影响程度取值（与后端 app.models.schemas.ImageQualityImpact 一致）
IMPACT_NONE = "none"
IMPACT_MINOR = "minor"
IMPACT_MAJOR = "major"

# 前端展示阈值（M6 规格：≥0.7 无提示 / 0.5-0.7 黄 / <0.5 红）
OVERALL_HINT_THRESHOLD = 0.7
OVERALL_WARN_THRESHOLD = 0.5

DARK_MEAN_THRESHOLD = 40.0
BRIGHT_MEAN_THRESHOLD = 220.0
SEVERE_BLUR_GRADIENT = 4.0
SATURATION_CLIP_RATIO = 0.05


@runtime_checkable
class ImageQualityAdapter(Protocol):
    """图像质量评估适配器契约。"""

    name: str

    def load(self) -> None:
        """加载资源（幂等；假实现为空操作）。"""

    def assess(self, frame: Frame) -> Optional[ImageQuality]:
        """评估单帧质量；返回 ``None`` 表示本帧未产出评估。"""

    def close(self) -> None:
        ...


class NullImageQualityAdapter:
    """不产出质量评估（默认）。"""

    name = "null"

    def load(self) -> None:
        return None

    def assess(self, frame: Frame) -> Optional[ImageQuality]:
        return None

    def close(self) -> None:
        return None


class LocalFakeImageQualityAdapter:
    """本地确定性假实现（仅开发/测试）。

    分项均由帧像素统计量推导（无随机数），各值截断到 [0, 1]，越高越好：

    - ``blur_score``：相邻像素梯度均值归一化（越低越模糊）；
    - ``artifact_score``：饱和裁剪像素占比的补（越少裁剪越高）；
    - ``gain_score``：均值接近 128 的程度（增益合适度）；
    - ``overall_score``：上述三项与对比度的加权平均。

    .. warning:: 以上规则**没有临床依据**，真机必须替换为质量评分头输出。
    """

    name = "local_fake"

    def load(self) -> None:
        logger.warning("edge_image_quality_local_fake_in_use")

    def assess(self, frame: Frame) -> Optional[ImageQuality]:
        pixels = np.asarray(frame.pixels, dtype=np.float32)
        if pixels.ndim != 2 or pixels.size == 0:
            return None

        mean = float(pixels.mean())
        std = float(pixels.std())

        grad_x = float(np.abs(np.diff(pixels, axis=1)).mean()) if pixels.shape[1] > 1 else 0.0
        grad_y = float(np.abs(np.diff(pixels, axis=0)).mean()) if pixels.shape[0] > 1 else 0.0
        gradient = (grad_x + grad_y) / 2.0

        blurred = gradient / 16.0
        blur_score = _clamp(blurred)

        clipped = float(np.count_nonzero((pixels <= 0) | (pixels >= 255))) / float(pixels.size)
        artifact_score = _clamp(1.0 - clipped / max(SATURATION_CLIP_RATIO, 1e-6) * 0.5)

        gain_fitness = 1.0 - min(1.0, abs(mean - 128.0) / 128.0)
        gain_score = _clamp(gain_fitness)

        contrast = _clamp(std / 64.0)

        overall = _clamp(
            0.35 * blur_score + 0.25 * gain_score + 0.20 * artifact_score + 0.20 * contrast
        )

        issues: List[str] = []
        if mean < DARK_MEAN_THRESHOLD:
            issues.append("image_too_dark")
        elif mean > BRIGHT_MEAN_THRESHOLD:
            issues.append("image_too_bright")
        if gradient < SEVERE_BLUR_GRADIENT:
            issues.append("severe_blur")
        if clipped >= SATURATION_CLIP_RATIO:
            issues.append("saturation_clipping")
        if contrast < 0.2:
            issues.append("low_contrast")

        return ImageQuality(
            overall_score=round(overall, 4),
            blur_score=round(blur_score, 4),
            artifact_score=round(artifact_score, 4),
            gain_score=round(gain_score, 4),
            issues=issues,
            diagnostic_impact=_impact_of(overall),
        )

    def close(self) -> None:
        return None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def _impact_of(overall_score: float) -> str:
    if overall_score >= OVERALL_HINT_THRESHOLD:
        return IMPACT_NONE
    if overall_score >= OVERALL_WARN_THRESHOLD:
        return IMPACT_MINOR
    return IMPACT_MAJOR


# 进程级注入点：现场用真机适配器替换
_INJECTED_ADAPTER: Optional[ImageQualityAdapter] = None


def set_image_quality_adapter(adapter: Optional[ImageQualityAdapter]) -> None:
    """注入图像质量适配器（现场真机实现；传 ``None`` 清除注入）。"""
    global _INJECTED_ADAPTER
    _INJECTED_ADAPTER = adapter


def create_image_quality_adapter(name: str = "null") -> ImageQualityAdapter:
    """按配置名创建适配器。

    Raises:
        ValueError: 配置了未支持的名字且未注入真机适配器（显式失败，
            不静默降级为假实现）。
    """
    normalized = (name or "null").strip().lower()
    if _INJECTED_ADAPTER is not None and _INJECTED_ADAPTER.name == normalized:
        return _INJECTED_ADAPTER
    if normalized in ("", "null", "none", "disabled"):
        return NullImageQualityAdapter()
    if normalized in ("local_fake", "fake"):
        return LocalFakeImageQualityAdapter()
    raise ValueError(
        "未支持的图像质量适配器配置: "
        f"{name!r}。请配置 null 或 local_fake，或先经 "
        "set_image_quality_adapter() 注入真机实现。"
    )


def get_image_quality_adapter() -> Optional[ImageQualityAdapter]:
    """当前注入的适配器（无则 None）。"""
    return _INJECTED_ADAPTER


def image_quality_to_dict(quality: ImageQuality) -> Dict[str, Any]:
    """序列化为上行载荷（键名与后端 ``app.models.schemas.ImageQuality`` 对齐）。"""
    return {
        "overall_score": quality.overall_score,
        "blur_score": quality.blur_score,
        "artifact_score": quality.artifact_score,
        "gain_score": quality.gain_score,
        "issues": list(quality.issues),
        "diagnostic_impact": quality.diagnostic_impact,
    }


__all__ = [
    "IMPACT_MAJOR",
    "IMPACT_MINOR",
    "IMPACT_NONE",
    "OVERALL_HINT_THRESHOLD",
    "OVERALL_WARN_THRESHOLD",
    "ImageQualityAdapter",
    "LocalFakeImageQualityAdapter",
    "NullImageQualityAdapter",
    "create_image_quality_adapter",
    "get_image_quality_adapter",
    "image_quality_to_dict",
    "set_image_quality_adapter",
]
