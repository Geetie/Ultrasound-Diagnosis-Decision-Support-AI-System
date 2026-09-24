"""M3 自动测量：把报告文本中的尺寸表述结构化为 :class:`Measurement`。

## 来源分工（两条路都要走，因为真机与文本各有各的信息）

- **文本来源**（本模块）：医生报告 / LLM 输出里的 ``1.2x0.8cm``、``1.2×0.8×0.9cm``
  以及 ``距包膜 0.3cm`` 之类的距离表述。写入
  ``pixel_spacing_source="text"``，表明尺寸来自文本而非像素换算。
- **影像来源**（``edge/measurement.py``）：边缘端用标注框 + DICOM
  ``PixelSpacing`` 换算，``pixel_spacing_source`` 为 ``dicom``；DICOM 缺失
  PixelSpacing 时回退默认间距并标 ``default``（此时是估算值，前端必须提示）。

## 派生量

- ``aspect_ratio`` = 长径 / 短径（>1 表示"高于宽"，是甲状腺结节的恶性指向之一，
  由 M5 风险模型以 :data:`FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE` 消费）；
- ``volume_mm3`` = 椭球体积 ``π/6 · a · b · c``（只有两径时按 ``c=b`` 处理，
  与临床常用的"长×宽×高×0.523"一致）。

长度单位一律换算为 **mm**（契约固定），避免 cm/mm 混用导致 10 倍量级错误。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

import structlog

from app.knowledge.risk_rules import (
    ASPECT_RATIO_TALLER_THAN_WIDE_THRESHOLD,
    FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE,
)
from app.models.schemas import Measurement

logger = structlog.get_logger("app.knowledge.measurement_extract")

PIXEL_SPACING_SOURCE_TEXT = "text"

ELLIPSOID_VOLUME_FACTOR = 0.5236  # π/6

# "1.2x0.8" / "1.2×0.8×0.9" / "12*8mm"（单位可缺省，缺省按 cm）。
#
# 为什么直接对原文解析而不用 UDTParser 的 measurement 特征：后者会把任意
# 输入统一格式化为 "N.NxN.N cm"（"12*8mm" → "12.0x8.0 cm"，数值未换算），
# 直接取用会造成 10 倍量级错误；且它只捕获两径，三维尺寸会被截断。
_DIMENSION_RE = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:[x×*]|乘)\s*(\d+(?:\.\d+)?)"
    r"(?:\s*(?:[x×*]|乘)\s*(\d+(?:\.\d+)?))?\s*(mm|cm)?",
    re.IGNORECASE,
)

# "距包膜 0.3cm" / "距皮肤0.5 cm" / "距脏器边缘约 3mm"
_DISTANCE_PATTERNS: Tuple[Tuple[str, re.Pattern], ...] = (
    (
        "distance_to_capsule_mm",
        re.compile(r"距\s*(?:甲状腺)?包膜\s*(?:约)?\s*(\d+(?:\.\d+)?)\s*(mm|cm)?", re.IGNORECASE),
    ),
    (
        "distance_to_organ_edge_mm",
        re.compile(r"距\s*(?:脏器|腺体|器官)?\s*边缘\s*(?:约)?\s*(\d+(?:\.\d+)?)\s*(mm|cm)?", re.IGNORECASE),
    ),
    (
        "distance_to_skin_mm",
        re.compile(r"距\s*皮肤\s*(?:约)?\s*(\d+(?:\.\d+)?)\s*(mm|cm)?", re.IGNORECASE),
    ),
)

# 缺省单位：超声报告的尺寸默认以 cm 表述（"1.2x0.8" 通常指 1.2cm × 0.8cm）
_DEFAULT_UNIT = "cm"

_UNIT_TO_MM = {"mm": 1.0, "cm": 10.0}


def _to_mm(value: float, unit: Optional[str]) -> float:
    return round(value * _UNIT_TO_MM.get((unit or _DEFAULT_UNIT).lower(), 10.0), 3)


def parse_dimensions(text: Optional[str]) -> Optional[Tuple[float, float, Optional[float]]]:
    """抽取第一组尺寸（mm）。

    Returns:
        ``(long, short, anteroposterior)``；两径表述时第三项为 ``None``。
        长度按降序排列（长径 ≥ 短径），与"长/短/前后径"的临床口径一致。
    """
    if not text or not text.strip():
        return None

    match = _DIMENSION_RE.search(text)
    if not match:
        return None

    unit = match.group(4)
    values = [_to_mm(float(match.group(1)), unit), _to_mm(float(match.group(2)), unit)]
    if match.group(3):
        values.append(_to_mm(float(match.group(3)), unit))
    values.sort(reverse=True)
    anteroposterior = values[2] if len(values) > 2 else None
    return values[0], values[1], anteroposterior


def parse_distances(text: Optional[str]) -> dict:
    """抽取"到包膜/脏器边缘/皮肤"的距离（mm）。未提及的键不出现在结果中。"""
    if not text:
        return {}
    found: dict = {}
    for field_name, pattern in _DISTANCE_PATTERNS:
        match = pattern.search(text)
        if match:
            found[field_name] = _to_mm(float(match.group(1)), match.group(2))
    return found


def structured_measurement_from_text(
    text: Optional[str],
    organ: Optional[str] = None,
) -> Optional[Measurement]:
    """把报告文本结构化为 :class:`Measurement`。

    Returns:
        ``None`` 表示文本中没有任何可结构化测量的尺寸/距离（此时不应伪造
        一个空 ``Measurement``，否则前端会渲染出全空的测量表）。
    """
    del organ  # 预留：后续可按器官差异化解析（如心脏的 EF/室壁厚度）
    dimensions = parse_dimensions(text)
    distances = parse_distances(text)

    if dimensions is None and not distances:
        return None

    payload: dict = dict(distances)
    if dimensions is not None:
        long_mm, short_mm, anteroposterior_mm = dimensions
        payload["long_mm"] = long_mm
        payload["short_mm"] = short_mm
        if anteroposterior_mm is not None:
            payload["anteroposterior_mm"] = anteroposterior_mm
        if short_mm > 0:
            payload["aspect_ratio"] = round(long_mm / short_mm, 4)
        third = anteroposterior_mm if anteroposterior_mm is not None else short_mm
        payload["volume_mm3"] = round(
            ELLIPSOID_VOLUME_FACTOR * long_mm * short_mm * third, 3
        )
        payload["measurement_type"] = "三维" if anteroposterior_mm is not None else "二维"

    return Measurement(pixel_spacing_source=PIXEL_SPACING_SOURCE_TEXT, **payload)


def measurement_features(measurement: Optional[Measurement]) -> List[str]:
    """把测量结果转成参与 M5 风险计算的特征（目前只有"纵横比>1"）。"""
    if measurement is None or measurement.aspect_ratio is None:
        return []
    if measurement.aspect_ratio > ASPECT_RATIO_TALLER_THAN_WIDE_THRESHOLD:
        return [FEATURE_ASPECT_RATIO_TALLER_THAN_WIDE]
    return []


__all__ = [
    "ELLIPSOID_VOLUME_FACTOR",
    "PIXEL_SPACING_SOURCE_TEXT",
    "measurement_features",
    "parse_dimensions",
    "parse_distances",
    "structured_measurement_from_text",
]
