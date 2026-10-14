"""M3 自动测量：边缘端「标注框 + 像素尺度」→ 物理尺寸。

## 为什么换算放在边缘端

边缘端是唯一同时掌握「原始像素 + 标注框 + DICOM 元数据」的地方；后端只拿到
归一化坐标与文本，无法还原真实尺度。因此尺寸换算在边缘端完成，后端只做
契约校验与展示（文本来源的测量见 ``app/knowledge/measurement_extract.py``）。

## PixelSpacing 缺失时为什么必须标记而不是静默回退

DICOM 的 ``PixelSpacing`` (0018,0011) 缺失时，若直接用默认间距算出"1.2cm"
并当作真实尺寸展示，医生会基于一个**看似精确的错误数字**做判断，比不给数字
更危险。因此回退时把 ``pixel_spacing_source`` 置为 ``default``，前端据此
明确标注"估算值"。

## 单位

长度一律 **mm**，体积 **mm³**（``π/6·a·b·c``，两径时按 ``c=b``）。
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# DICOM PixelSpacing 缺失时的默认间距（mm/像素）。真机应由现场按设备标定覆盖。
DEFAULT_PIXEL_SPACING_MM = 0.15

ELLIPSOID_VOLUME_FACTOR = 0.5236  # π/6

PIXEL_SPACING_SOURCE_DICOM = "dicom"
PIXEL_SPACING_SOURCE_DEFAULT = "default"

# DICOM 标签名（按优先级）：PixelSpacing 为首选，ImagerPixelSpacing 为备选。
_DICOM_SPACING_TAGS: tuple[str, ...] = ("PixelSpacing", "ImagerPixelSpacing")


@dataclass(frozen=True)
class PixelSpacing:
    """像素间距（mm）。DICOM 顺序为 ``[行间距, 列间距]`` 即 ``[y, x]``。"""

    row_mm: float
    column_mm: float
    source: str = PIXEL_SPACING_SOURCE_DICOM

    def __post_init__(self) -> None:
        if self.row_mm <= 0 or self.column_mm <= 0:
            raise ValueError("像素间距必须为正数")

    @classmethod
    def default(cls) -> PixelSpacing:
        return cls(
            row_mm=DEFAULT_PIXEL_SPACING_MM,
            column_mm=DEFAULT_PIXEL_SPACING_MM,
            source=PIXEL_SPACING_SOURCE_DEFAULT,
        )


def _unwrap(value: Any) -> Any:
    """取出 DICOM DataElement 的值（pydicom 的可选项包装）。"""
    return getattr(value, "value", value)


def _first_spacing_tag(metadata: Any) -> Any:
    if metadata is None:
        return None
    for tag in _DICOM_SPACING_TAGS:
        if isinstance(metadata, Mapping):
            candidate = metadata.get(tag)
        else:
            candidate = getattr(metadata, tag, None)
        candidate = _unwrap(candidate)
        if candidate:
            return candidate
    return None


def from_dicom_metadata(metadata: Any) -> PixelSpacing:
    """从 DICOM 元数据取像素间距；缺失/非法时回退默认并标记来源。

    Args:
        metadata: pydicom ``Dataset``、普通 ``Mapping``，或 ``None``。
    """
    raw = _first_spacing_tag(metadata)
    if raw is not None:
        try:
            values: Sequence[Any] = raw if isinstance(raw, (list, tuple)) else [raw]
            floats = [float(item) for item in values]
            if len(floats) >= 2 and floats[0] > 0 and floats[1] > 0:
                return PixelSpacing(
                    row_mm=floats[0],
                    column_mm=floats[1],
                    source=PIXEL_SPACING_SOURCE_DICOM,
                )
            logger.warning("edge_pixel_spacing_invalid", raw=str(raw))
        except (TypeError, ValueError) as exc:
            logger.warning("edge_pixel_spacing_parse_failed", raw=str(raw), error=str(exc))

    return PixelSpacing.default()


def measurement_from_bbox(
    bbox: Sequence[float],
    frame_shape: tuple[int, int],
    spacing: PixelSpacing | None = None,
    plane_type: str | None = None,
) -> dict[str, Any] | None:
    """把归一化标注框换算为物理尺寸。

    Args:
        bbox: 归一化 ``(x, y, width, height)``（与前端 NavigationOverlay 同口径）。
        frame_shape: 原始帧 ``(height, width)`` 像素数。
        spacing: 像素间距；``None`` 时用默认间距（结果标记为 ``default``）。
        plane_type: M1 的切面类型，用于标注测量所属切面（便于追溯）。

    Returns:
        与 ``app.models.schemas.Measurement`` 键对齐的 dict；入参非法时
        ``None``（不产出比产出错误的尺寸更安全）。

    说明：``distance_to_capsule/organ_edge/skin`` 需要解剖标志点，仅凭标注框
    无法得到，故不填写（``None``）而不猜测。
    """
    if not bbox or len(bbox) < 4:
        return None
    if not frame_shape or len(frame_shape) < 2:
        return None

    height_px, width_px = int(frame_shape[0]), int(frame_shape[1])
    if height_px <= 0 or width_px <= 0:
        return None

    try:
        _, _, norm_width, norm_height = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    except (TypeError, ValueError):  # silent-ok: 取值不可解析 → 回退默认（正常控制流）
        return None

    if norm_width <= 0 or norm_height <= 0:
        return None

    effective = spacing or PixelSpacing.default()

    box_width_mm = round(norm_width * width_px * effective.column_mm, 3)
    box_height_mm = round(norm_height * height_px * effective.row_mm, 3)
    long_mm, short_mm = max(box_width_mm, box_height_mm), min(box_width_mm, box_height_mm)
    if short_mm <= 0:
        return None

    label = "二维"
    if plane_type:
        label = f"二维{plane_type}"

    return {
        "long_mm": long_mm,
        "short_mm": short_mm,
        "anteroposterior_mm": None,
        "aspect_ratio": round(long_mm / short_mm, 4),
        "volume_mm3": round(ELLIPSOID_VOLUME_FACTOR * long_mm * short_mm * short_mm, 3),
        "distance_to_capsule_mm": None,
        "distance_to_organ_edge_mm": None,
        "distance_to_skin_mm": None,
        "measurement_type": label,
        "unit": "mm",
        "pixel_spacing_source": effective.source,
    }


def measure_labels(
    labels: Sequence[Any],
    frame_shape: tuple[int, int],
    spacing: PixelSpacing | None = None,
    plane_type: str | None = None,
) -> dict[str, dict[str, Any]]:
    """按标注名批量测量，返回 ``{label: measurement}``。

    同一标签名多次出现时保留**最大**的测量（导航流是逐帧上报的，同一病灶
    会反复出现；取最大值避免逐帧抖动导致尺寸忽大忽小）。
    """
    results: dict[str, dict[str, Any]] = {}
    for label in labels or ():
        name = getattr(label, "label", None)
        if not name:
            continue
        bbox = [getattr(label, "x", None), getattr(label, "y", None),
                getattr(label, "width", None), getattr(label, "height", None)]
        measurement = measurement_from_bbox(bbox, frame_shape, spacing, plane_type)
        if measurement is None:
            continue
        existing = results.get(name)
        if existing is None or measurement["long_mm"] > existing["long_mm"]:
            results[name] = measurement
    return results


__all__ = [
    "DEFAULT_PIXEL_SPACING_MM",
    "ELLIPSOID_VOLUME_FACTOR",
    "PIXEL_SPACING_SOURCE_DEFAULT",
    "PIXEL_SPACING_SOURCE_DICOM",
    "PixelSpacing",
    "from_dicom_metadata",
    "measure_labels",
    "measurement_from_bbox",
]
