"""M1 切面标准性评估适配器（边缘端）。

真机实现（与导航检测头共享 backbone 的切面分类/评分头，TensorRT）在开发机上
无法验证，因此沿用本项目既有约定（EDG / TensorRT / PACS）：**只定义契约并交付
一个本地确定性假实现**，编排逻辑完整可测，真机实现由现场经
:func:`set_plane_assessment_adapter` 注入。

配置开关（见 ``edge/config.py``）：

    EDGE_PLANE_ASSESSMENT_ADAPTER=null        不产出切面评估（默认，安全）
    EDGE_PLANE_ASSESSMENT_ADAPTER=local_fake  本地假实现（仅开发/测试）

配置为其它名字（如 ``tensorrt``）而现场尚未注入真机适配器时**显式报错**，
不静默降级为假实现——否则现场会拿到"看似正常"的假切面评估，属临床风险。

.. warning::
   本地假实现的输出**没有临床意义**，仅用于打通链路与单测。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np
import structlog

from edge.protocols import Frame, PlaneAssessment

logger = structlog.get_logger(__name__)

# 与后端 app.models.schemas.PlaneType 取值保持一致
PLANE_TRANSVERSE = "transverse"
PLANE_LONGITUDINAL = "longitudinal"
PLANE_NON_STANDARD = "non_standard"

# 切面判定为"标准"的最低分数（本地假实现使用，真机由模型头决定）
DEFAULT_STANDARD_PLANE_THRESHOLD = 0.60

# 灰阶均值边界（0-255）：超出范围视为无法定切面
DARK_MEAN_THRESHOLD = 40.0
BRIGHT_MEAN_THRESHOLD = 220.0
LOW_CONTRAST_STD = 12.0

# 本地假实现使用的"该器官标准切面应可见结构"目录（**非临床标准**，
# 仅用于让后端的关键结构缺失监控链路可测）。
_FAKE_STRUCTURE_CATALOG: Dict[str, List[str]] = {
    "thyroid": ["left_lobe", "right_lobe", "isthmus"],
    "liver": ["right_lobe", "left_lobe", "portal_vein"],
    "cardiac": ["left_ventricle", "left_atrium", "aortic_valve"],
}


@runtime_checkable
class PlaneAssessmentAdapter(Protocol):
    """切面评估适配器契约。"""

    name: str

    def load(self) -> None:
        """加载资源（幂等；假实现为空操作）。"""

    def assess(
        self, frame: Frame, organ_code: Optional[str] = None
    ) -> Optional[PlaneAssessment]:
        """评估单帧切面；返回 ``None`` 表示本帧未产出评估。"""

    def close(self) -> None:
        ...


class NullPlaneAssessmentAdapter:
    """不产出切面评估（默认）。"""

    name = "null"

    def load(self) -> None:
        return None

    def assess(
        self, frame: Frame, organ_code: Optional[str] = None
    ) -> Optional[PlaneAssessment]:
        return None

    def close(self) -> None:
        return None


class LocalFakePlaneAssessmentAdapter:
    """本地确定性假实现（仅开发/测试）。

    输出完全由帧像素统计量决定（无随机数），保证同一帧永远得到同一结果，
    便于单测与链路联调：

    - 亮度过暗/过亮 → 无法定切面，``plane_type=non_standard``；
    - 否则按帧长宽比定切面（高≥宽 → 纵切面，反之为横切面）；
    - ``plane_score`` = 0.5×对比度 + 0.5×亮度合适度，截断到 [0, 1]。

    .. warning:: 以上规则**没有临床依据**，真机必须替换为模型头输出。
    """

    name = "local_fake"

    def __init__(self, standard_threshold: float = DEFAULT_STANDARD_PLANE_THRESHOLD):
        if not 0.0 <= standard_threshold <= 1.0:
            raise ValueError("standard_threshold 必须在 [0, 1] 内")
        self._standard_threshold = standard_threshold

    def load(self) -> None:
        logger.warning("edge_plane_assessment_local_fake_in_use")

    def assess(
        self, frame: Frame, organ_code: Optional[str] = None
    ) -> Optional[PlaneAssessment]:
        pixels = np.asarray(frame.pixels, dtype=np.float32)
        if pixels.ndim != 2 or pixels.size == 0:
            return None

        height, width = int(pixels.shape[0]), int(pixels.shape[1])
        mean = float(pixels.mean())
        std = float(pixels.std())

        contrast = min(1.0, std / 64.0)
        brightness_fitness = 1.0 - min(1.0, abs(mean - 128.0) / 128.0)
        score = round(max(0.0, min(1.0, 0.5 * contrast + 0.5 * brightness_fitness)), 4)

        issues: List[str] = []
        if mean < DARK_MEAN_THRESHOLD:
            issues.append("image_too_dark")
        elif mean > BRIGHT_MEAN_THRESHOLD:
            issues.append("image_too_bright")
        if std < LOW_CONTRAST_STD:
            issues.append("low_contrast")

        if "image_too_dark" in issues or "image_too_bright" in issues:
            # 画面本身不可判读，无法给出切面方向
            plane_type = PLANE_NON_STANDARD
        else:
            plane_type = PLANE_LONGITUDINAL if height >= width else PLANE_TRANSVERSE

        is_standard = (
            plane_type != PLANE_NON_STANDARD
            and score >= self._standard_threshold
            and "low_contrast" not in issues
        )
        if not is_standard and plane_type != PLANE_NON_STANDARD:
            issues.append("plane_below_standard")

        return PlaneAssessment(
            plane_type=plane_type,
            is_standard=is_standard,
            plane_score=score,
            organ_code=organ_code,
            issues=issues,
            required_structures_visible=self._fake_visible_structures(organ_code, is_standard),
        )

    @staticmethod
    def _fake_visible_structures(
        organ_code: Optional[str], is_standard: bool
    ) -> List[str]:
        """假实现：非标准切面时"最后一个结构不可见"，用于驱动关键结构缺失监控。"""
        catalog = _FAKE_STRUCTURE_CATALOG.get(organ_code or "", [])
        if not catalog:
            return []
        if is_standard:
            return list(catalog)
        return list(catalog[:-1])

    def close(self) -> None:
        return None


# 进程级注入点：现场用真机适配器替换（与 set_reload_adapter 等既有模式一致）
_INJECTED_ADAPTER: Optional[PlaneAssessmentAdapter] = None


def set_plane_assessment_adapter(adapter: Optional[PlaneAssessmentAdapter]) -> None:
    """注入切面评估适配器（现场真机实现；传 ``None`` 清除注入）。"""
    global _INJECTED_ADAPTER
    _INJECTED_ADAPTER = adapter


def create_plane_assessment_adapter(name: str = "null") -> PlaneAssessmentAdapter:
    """按配置名创建适配器。

    Raises:
        ValueError: 配置了未支持的名字且未注入真机适配器（显式失败，
            不静默降级为假实现）。
    """
    normalized = (name or "null").strip().lower()
    if _INJECTED_ADAPTER is not None and _INJECTED_ADAPTER.name == normalized:
        return _INJECTED_ADAPTER
    if normalized in ("", "null", "none", "disabled"):
        return NullPlaneAssessmentAdapter()
    if normalized in ("local_fake", "fake"):
        return LocalFakePlaneAssessmentAdapter()
    raise ValueError(
        "未支持的切面评估适配器配置: "
        f"{name!r}。请配置 null 或 local_fake，或先经 "
        "set_plane_assessment_adapter() 注入真机实现（如 tensorrt）。"
    )


def get_plane_assessment_adapter() -> Optional[PlaneAssessmentAdapter]:
    """当前注入的适配器（无则 None）。"""
    return _INJECTED_ADAPTER


def plane_assessment_to_dict(assessment: PlaneAssessment) -> Dict[str, Any]:
    """序列化为上行载荷（键名与后端 ``app.models.schemas.PlaneAssessment`` 对齐）。"""
    return {
        "plane_type": assessment.plane_type,
        "is_standard": assessment.is_standard,
        "plane_score": assessment.plane_score,
        "organ_code": assessment.organ_code,
        "issues": list(assessment.issues),
        "required_structures_visible": list(assessment.required_structures_visible),
    }


__all__ = [
    "DEFAULT_STANDARD_PLANE_THRESHOLD",
    "PLANE_LONGITUDINAL",
    "PLANE_NON_STANDARD",
    "PLANE_TRANSVERSE",
    "LocalFakePlaneAssessmentAdapter",
    "NullPlaneAssessmentAdapter",
    "PlaneAssessmentAdapter",
    "create_plane_assessment_adapter",
    "get_plane_assessment_adapter",
    "plane_assessment_to_dict",
    "set_plane_assessment_adapter",
]
