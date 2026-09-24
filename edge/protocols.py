"""边缘端对外契约（Protocol + 数据类）。

硬件相关实现（DICOM 解码、TensorRT 推理）通过协议注入，便于替换与单测：
开发机上没有 Jetson/PyTorch-TensorRT 时，用假实现验证调度与安全逻辑。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class Frame:
    """一帧已解码的超声图像。"""

    frame_id: str
    session_id: str
    index: int
    # 归一化灰度像素（H, W），dtype=uint8
    pixels: np.ndarray
    # 单调时钟到达时间（毫秒），用于延迟埋点
    received_at_ms: float
    acquisition_datetime: Optional[str] = None
    # M3 自动测量：DICOM 像素间距 (行间距, 列间距)，单位 mm；缺失时为 None，
    # 由 edge.measurement 回退默认间距并标记为估算值。
    pixel_spacing_mm: Optional[Tuple[float, float]] = None


@dataclass(frozen=True)
class PlaneAssessment:
    """切面标准性评估（M1）。帧级属性。

    与后端 ``app.models.schemas.PlaneAssessment`` 一一对应；边缘端不导入
    ``app.*``（独立可部署单元），因此这里用 dataclass 镜像同名契约。
    真机切面分类头由 ``edge/plane_assessment.py`` 的适配器注入。
    """

    plane_type: str  # transverse | longitudinal | non_standard
    is_standard: bool
    plane_score: float = 0.0
    organ_code: Optional[str] = None
    issues: List[str] = field(default_factory=list)
    required_structures_visible: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class ImageQuality:
    """图像质量评估（M6）。帧级属性，各分项 0.0-1.0，越高越好。

    真机质量评分头由 ``edge/image_quality.py`` 的适配器注入。
    """

    overall_score: float
    blur_score: Optional[float] = None
    artifact_score: Optional[float] = None
    gain_score: Optional[float] = None
    issues: List[str] = field(default_factory=list)
    # none | minor | major
    diagnostic_impact: Optional[str] = None


@dataclass(frozen=True)
class NavLabel:
    """导航推理输出的单个标注。

    坐标一律使用归一化坐标（0.0-1.0），与前端 NavigationOverlay 契约一致，
    避免边缘端与前端对像素尺寸的理解不一致。

    ``risk_flag``/``suggested_action`` 为 M2 的 B+ 级高危标记：只表达
    "需关注"+建议启动标准评估，不含分级词汇或病种编码（安全容忍度高于
    A 级、低于 B 级）。默认 ``None`` 以兼容既有构造点。
    """

    label: str
    x: float
    y: float
    width: float
    height: float
    confidence: float
    risk_flag: Optional[str] = None  # "high" | None
    suggested_action: Optional[str] = None  # "start_standard_evaluation" | None

    def as_box(self) -> tuple:
        return (self.x, self.y, self.x + self.width, self.y + self.height)


@dataclass
class NavResult:
    """单帧导航推理结果。"""

    frame_id: str
    session_id: str
    labels: List[NavLabel] = field(default_factory=list)
    infer_latency_ms: float = 0.0
    backend: str = "unknown"
    # M1/M6 帧级属性（可选，未接切面/质量头时为 None）
    plane_assessment: Optional[PlaneAssessment] = None
    image_quality: Optional[ImageQuality] = None


@runtime_checkable
class NavInferenceBackend(Protocol):
    """导航推理后端（TensorRT / 测试假实现）。"""

    name: str

    def load(self) -> None:
        """加载模型（幂等）。"""

    def infer(self, frame: Frame) -> List[NavLabel]:
        """同步推理，返回归一化坐标标注。"""

    def close(self) -> None:
        """释放显存/句柄。"""


@runtime_checkable
class FrameSink(Protocol):
    """帧/结果上行通道（WebSocket 客户端实现）。"""

    async def push_nav_result(self, result: NavResult) -> None:
        ...

    async def push_frame(self, frame: Frame, payload: Dict[str, Any]) -> None:
        ...
