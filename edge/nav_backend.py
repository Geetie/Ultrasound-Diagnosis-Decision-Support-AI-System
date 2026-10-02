"""T-GA-EDG-02 导航推理后端（TensorRT 适配 + 可测的前后处理）。

分层理由：TensorRT/CUDA 的绑定代码在开发机上无法验证，因此把它压缩成
一个极薄的 :class:`EngineRunner`（懒加载 + 明确标注需真机验证），而把
**真正容易出错的几何与数值逻辑**（letterbox 预处理、逆变换、NMS、
归一化坐标转换）放在纯 numpy 的 :func:`preprocess_frame` /
:func:`postprocess_detections` 里，由单测覆盖。

坐标契约：输出一律为归一化坐标（0.0-1.0），与前端 NavigationOverlay 和
`edge/safety.py` 的坐标范围校验一致。
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import numpy as np
import structlog

from edge.protocols import Frame, NavLabel

logger = structlog.get_logger(__name__)

DEFAULT_INPUT_SIZE: tuple[int, int] = (512, 512)
DEFAULT_SCORE_THRESHOLD = 0.5
DEFAULT_IOU_THRESHOLD = 0.45


class EngineRunner(Protocol):
    """TensorRT 引擎封装（真机实现见 TensorRTRunner，测试用假实现）。"""

    name: str

    @property
    def input_shape(self) -> tuple[int, int, int, int]:
        """(N, C, H, W)"""

    def load(self) -> None:
        ...

    def run(self, input_tensor: np.ndarray) -> np.ndarray:
        """输入 (1,3,H,W) float32，输出 (N,6)：x1,y1,x2,y2,score,class_id"""

    def close(self) -> None:
        ...


def preprocess_frame(
    frame: Frame,
    input_size: tuple[int, int] = DEFAULT_INPUT_SIZE,
    *,
    channels: int = 3,
    normalize: bool = True,
) -> tuple[np.ndarray, float, float, float]:
    """letterbox 预处理。

    Returns:
        (tensor, scale, pad_x, pad_y)
        tensor: (1, channels, H, W) float32
        后处理需用 (scale, pad_x, pad_y) 把输入坐标逆变换回原图。
    """
    pixels = np.asarray(frame.pixels)
    if pixels.ndim != 2:
        raise ValueError(f"期望二维灰度帧，实际 shape={pixels.shape}")

    target_h, target_w = input_size[1], input_size[0]
    src_h, src_w = pixels.shape

    scale = min(target_w / src_w, target_h / src_h)
    resized_w = max(1, int(round(src_w * scale)))
    resized_h = max(1, int(round(src_h * scale)))
    pad_x = (target_w - resized_w) / 2.0
    pad_y = (target_h - resized_h) / 2.0

    canvas = np.zeros((target_h, target_w), dtype=np.float32)
    canvas[
        int(round(pad_y)): int(round(pad_y)) + resized_h,
        int(round(pad_x)): int(round(pad_x)) + resized_w,
    ] = _resize_nearest(pixels, resized_h, resized_w)

    if normalize:
        canvas = canvas / 255.0

    tensor = np.repeat(canvas[None, :, :], channels, axis=0)[None, :, :, :]
    return tensor.astype(np.float32), scale, pad_x, pad_y


def _resize_nearest(pixels: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """最近邻缩放（避免引入 cv2/PIL 依赖；推理前插值质量的差异由模型容忍）。"""
    src_h, src_w = pixels.shape
    row_idx = np.clip((np.arange(out_h) * src_h / out_h).astype(int), 0, src_h - 1)
    col_idx = np.clip((np.arange(out_w) * src_w / out_w).astype(int), 0, src_w - 1)
    return pixels[row_idx][:, col_idx].astype(np.float32)


def _iou(box_a: np.ndarray, box_b: np.ndarray) -> float:
    x1 = max(box_a[0], box_b[0])
    y1 = max(box_a[1], box_b[1])
    x2 = min(box_a[2], box_b[2])
    y2 = min(box_a[3], box_b[3])

    inter_w = max(0.0, x2 - x1)
    inter_h = max(0.0, y2 - y1)
    inter = inter_w * inter_h

    area_a = max(0.0, box_a[2] - box_a[0]) * max(0.0, box_a[3] - box_a[1])
    area_b = max(0.0, box_b[2] - box_b[0]) * max(0.0, box_b[3] - box_b[1])
    union = area_a + area_b - inter
    if union <= 0:
        return 0.0
    return inter / union


def nms(
    detections: np.ndarray, iou_threshold: float = DEFAULT_IOU_THRESHOLD
) -> list[int]:
    """按类别分组做非极大值抑制，返回保留的下标。"""
    if len(detections) == 0:
        return []

    keep: list[int] = []
    for class_id in np.unique(detections[:, 5]):
        indices = [
            i for i in range(len(detections)) if detections[i, 5] == class_id
        ]
        indices.sort(key=lambda i: detections[i, 4], reverse=True)

        while indices:
            best = indices.pop(0)
            keep.append(best)
            indices = [
                i
                for i in indices
                if _iou(detections[best][:4], detections[i][:4]) <= iou_threshold
            ]

    keep.sort()
    return keep


def postprocess_detections(
    detections: np.ndarray,
    *,
    frame_shape: tuple[int, int],
    input_size: tuple[int, int] = DEFAULT_INPUT_SIZE,
    scale: float = 1.0,
    pad_x: float = 0.0,
    pad_y: float = 0.0,
    labels: Sequence[str] = (),
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
    iou_threshold: float = DEFAULT_IOU_THRESHOLD,
) -> list[NavLabel]:
    """把引擎原始输出转成归一化坐标的 NavLabel 列表。

    Args:
        detections: (N, 6) = x1,y1,x2,y2,score,class_id，坐标为 letterbox 输入空间。
        frame_shape: 原始帧 (H, W)，用于归一化。
    """
    detections = np.asarray(detections, dtype=np.float32)
    if detections.size == 0:
        return []
    if detections.ndim != 2 or detections.shape[1] != 6:
        raise ValueError(f"detections 形状应为 (N,6)，实际 {detections.shape}")

    filtered = detections[detections[:, 4] >= score_threshold]
    if len(filtered) == 0:
        return []

    keep = nms(filtered, iou_threshold=iou_threshold)

    src_h, src_w = frame_shape
    if src_w <= 0 or src_h <= 0:
        raise ValueError("frame_shape 必须为正数")

    results: list[NavLabel] = []
    for index in keep:
        x1, y1, x2, y2, score, class_id = filtered[index][:6]

        # 逆 letterbox → 原图像素坐标
        ox1 = (x1 - pad_x) / scale
        oy1 = (y1 - pad_y) / scale
        ox2 = (x2 - pad_x) / scale
        oy2 = (y2 - pad_y) / scale

        # 裁到画面内，再归一化
        ox1 = float(np.clip(ox1, 0.0, src_w))
        ox2 = float(np.clip(ox2, 0.0, src_w))
        oy1 = float(np.clip(oy1, 0.0, src_h))
        oy2 = float(np.clip(oy2, 0.0, src_h))

        int_class = int(class_id)
        if 0 <= int_class < len(labels):
            label_name = labels[int_class]
        else:
            label_name = f"class_{int_class}"

        results.append(
            NavLabel(
                label=label_name,
                x=ox1 / src_w,
                y=oy1 / src_h,
                width=max(0.0, (ox2 - ox1)) / src_w,
                height=max(0.0, (oy2 - oy1)) / src_h,
                confidence=float(score),
            )
        )

    return results


@dataclass
class TensorRTNavBackend:
    """TensorRT 导航推理后端（Jetson）。

    ``runner`` 可注入：真机由 :class:`TensorRTRunner` 提供，单测用假实现。
    """

    engine_path: str
    runner: EngineRunner | None = None
    input_size: tuple[int, int] = DEFAULT_INPUT_SIZE
    labels: Sequence[str] = ()
    score_threshold: float = DEFAULT_SCORE_THRESHOLD
    iou_threshold: float = DEFAULT_IOU_THRESHOLD
    name: str = "tensorrt"

    _loaded: bool = False

    def load(self) -> None:
        if self._loaded:
            return
        runner = self._get_runner()
        runner.load()
        self._loaded = True
        logger.info(
            "edge_nav_tensorrt_loaded",
            engine_path=self.engine_path,
            input_shape=getattr(runner, "input_shape", None),
        )

    def infer(self, frame: Frame) -> list[NavLabel]:
        if not self._loaded:
            self.load()

        runner = self._get_runner()
        tensor, scale, pad_x, pad_y = preprocess_frame(
            frame, self.input_size
        )
        raw = runner.run(tensor)

        return postprocess_detections(
            raw,
            frame_shape=frame.pixels.shape,
            input_size=self.input_size,
            scale=scale,
            pad_x=pad_x,
            pad_y=pad_y,
            labels=self.labels,
            score_threshold=self.score_threshold,
            iou_threshold=self.iou_threshold,
        )

    def close(self) -> None:
        if self._loaded and self.runner is not None:
            self.runner.close()
        self._loaded = False

    def _get_runner(self) -> EngineRunner:
        if self.runner is None:
            self.runner = TensorRTRunner(self.engine_path)
        return self.runner


class TensorRTRunner:
    """TensorRT 引擎封装（**需在 Jetson 真机验证**）。

    tensorrt/pycuda 惰性导入；本类只做"反序列化引擎 + 绑定输入输出 +
    同步执行"，所有几何处理都在 nav_backend 的纯 numpy 函数里。
    """

    name = "tensorrt"

    def __init__(self, engine_path: str) -> None:
        self._engine_path = engine_path
        self._engine: Any = None
        self._context: Any = None
        self._inputs: list[Any] = []
        self._outputs: list[Any] = []
        self._output_shape: tuple[int, int, int, int] = (1, 0, 0, 0)

    @property
    def input_shape(self) -> tuple[int, int, int, int]:
        return self._output_shape

    def load(self) -> None:
        try:
            import pycuda.driver as cuda  # noqa: F401
            import tensorrt as trt
        except ImportError as e:
            raise RuntimeError(
                "TensorRT/pycuda 未安装（边缘设备请安装 requirements-edge.txt 与 JetPack）"
            ) from e

        if not Path(self._engine_path).exists():
            raise RuntimeError(f"TensorRT 引擎文件不存在: {self._engine_path}")

        import tensorrt as trt

        trt_logger = trt.Logger(trt.Logger.WARNING)
        with open(self._engine_path, "rb") as handle:
            runtime = trt.Runtime(trt_logger)
            self._engine = runtime.deserialize_cuda_engine(handle.read())

        if self._engine is None:
            raise RuntimeError(f"TensorRT 引擎反序列化失败: {self._engine_path}")

        self._context = self._engine.create_execution_context()
        logger.info("edge_tensorrt_engine_deserialized", path=self._engine_path)

    def run(self, input_tensor: np.ndarray) -> np.ndarray:
        if self._context is None:
            raise RuntimeError("引擎未加载，请先调用 load()")

        import pycuda.driver as cuda

        input_tensor = np.ascontiguousarray(input_tensor, dtype=np.float32)
        output = self._allocate_output()

        d_input = cuda.mem_alloc(input_tensor.nbytes)
        d_output = cuda.mem_alloc(output.nbytes)
        stream = cuda.Stream()

        cuda.memcpy_htod_async(d_input, input_tensor, stream)
        bindings = [int(d_input), int(d_output)]
        self._context.execute_v2(bindings=bindings)
        cuda.memcpy_dtoh_async(output, d_output, stream)
        stream.synchronize()

        d_input.free()
        d_output.free()
        return output.reshape(-1, output.shape[-1])

    def close(self) -> None:
        self._context = None
        self._engine = None

    def _allocate_output(self) -> np.ndarray:
        """输出形状按 (1, N, 6) 约定分配；真实引擎差异需按 binding 读取。"""
        self._output_shape = (1, 8400, 6)
        return np.zeros(self._output_shape, dtype=np.float32)


class NullNavBackend:
    """空后端：未部署模型时占位，保证链路可跑（返回空标注）。"""

    name = "null"

    def load(self) -> None:
        logger.warning("edge_nav_backend_null_in_use")

    def infer(self, frame: Frame) -> list[NavLabel]:
        return []

    def close(self) -> None:
        pass


def create_nav_backend(
    engine_path: str | None = None,
    *,
    input_size: tuple[int, int] = DEFAULT_INPUT_SIZE,
    labels: Sequence[str] = (),
    score_threshold: float = DEFAULT_SCORE_THRESHOLD,
) -> Any:
    """工厂：有引擎文件走 TensorRT，否则返回空后端（不阻断边缘端启动）。"""
    if engine_path and Path(engine_path).exists():
        return TensorRTNavBackend(
            engine_path=engine_path,
            input_size=input_size,
            labels=labels,
            score_threshold=score_threshold,
        )
    logger.warning("edge_nav_engine_missing_using_null_backend", engine_path=engine_path)
    return NullNavBackend()


__all__ = [
    "DEFAULT_INPUT_SIZE",
    "DEFAULT_IOU_THRESHOLD",
    "DEFAULT_SCORE_THRESHOLD",
    "EngineRunner",
    "NullNavBackend",
    "TensorRTNavBackend",
    "TensorRTRunner",
    "create_nav_backend",
    "nms",
    "postprocess_detections",
    "preprocess_frame",
]
