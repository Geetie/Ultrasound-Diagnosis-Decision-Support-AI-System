"""甲状腺结节分割推理模块（UNet，TN3K 官方划分训练/评测）。

来源：scripts/tn3k_seg_unet.py —— 官方 trainval(2879) 训练、官方 test(614) 全量评测
DSC 0.7961 / IoU 0.7004（报告 reports/tn3k_seg_unet.json）。本模块把该权重接进服务。

设计要点
--------
- **预处理与训练严格一致**：直接 resize 到 256×256 + /255（训练脚本同一段逻辑：
  长边缩放到 256、短边被 ``max(256, …)`` 顶到 256，等效方形缩放；超声长宽比本不
  作为诊断依据，未做补边/裁剪）。推理记录缩放参数，输出掩码用**逆变换**贴回原图
  尺寸，避免医生拿到错位掩码。
- **懒加载单例**：首次调用才建图 + 载权重；权重/torch/monai 缺失时抛
  ``SegmentationUnavailableError``，由 API 层转 503 并带明确原因（不静默返回空掩码）。
- **线程安全**：infer 走 ``torch.no_grad()``；模型只读不训，天然可并发；
  但为避免多线程同时首次加载，用一把锁做一次性初始化。
- **设备选择**：``settings.nodule_segmentation_device``（auto/cuda/cpu）。
  注意服务器 shell 的 ``CUDA_VISIBLE_DEVICES`` 若为无效值，torch 看不到 GPU，
  auto 会退 CPU——功能正常但慢，部署时显式设 ``CUDA_VISIBLE_DEVICES=0``。
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import structlog

logger = structlog.get_logger(__name__)

INPUT_SIZE = 256
DEFAULT_THRESHOLD = 0.5


class SegmentationUnavailableError(RuntimeError):
    """分割能力不可用（缺依赖/缺权重/设备不可用）。API 层据此返回 503。"""


@dataclass(frozen=True)
class SegmentationResult:
    """一次分割推理的结构化结果。"""

    mask_png: bytes
    width: int
    height: int
    threshold: float
    input_size: int
    lesion_area_ratio: float
    bbox: tuple[int, int, int, int] | None  # x0, y0, x1, y1（原图坐标）
    mean_prob: float
    max_prob: float
    latency_ms: float
    device: str
    model_dice_official: float | None


def _model_path(organ: str = "thyroid") -> Path:
    """器官权重路径解析。

    - thyroid：读 settings.nodule_segmentation_model_path（保持既有部署行为不变）
    - 其他器官：注册表 weight_path，可被 ``<ORGAN>_SEGMENTATION_MODEL_PATH`` 环境变量
      覆盖（如 ``CARDIAC_SEGMENTATION_MODEL_PATH``）——部署时不必改代码。
    """
    from app.config.settings import settings

    if organ == "thyroid":
        return Path(
            getattr(settings, "nodule_segmentation_model_path", "")
            or "~/uai/models/nodule_seg/unet_tn3k.pt"
        ).expanduser()

    import os

    env_key = f"{organ.upper()}_SEGMENTATION_MODEL_PATH"
    env_val = os.environ.get(env_key, "").strip()
    if env_val:
        return Path(env_val).expanduser()

    from app.inference.organ_segmentation import get_spec

    spec = get_spec(organ)
    return Path(spec.weight_path or "").expanduser()


class NoduleSegmenter:
    """懒加载单例包装（``NoduleSegmenter.get(organ)``，按器官各一个实例）。

    命名保留 NoduleSegmenter 是历史兼容——它现在按注册表服务任意器官
    （thyroid 结节 / cardiac 左心室）。调用方应通过 ``organ=`` 显式指定，
    默认 thyroid 以保证旧调用行为不变。
    """

    _instances: ClassVar[dict[str, NoduleSegmenter]] = {}
    _lock = threading.Lock()

    def __init__(self, organ: str = "thyroid") -> None:
        from app.inference.organ_segmentation import get_spec

        self._organ = get_spec(organ).organ  # 归一化 + 校验（未注册即 KeyError）
        self._net: Any = None
        self._device: str = "cpu"
        self._meta: dict[str, Any] = {}

    @property
    def organ(self) -> str:
        return self._organ

    @classmethod
    def get(cls, organ: str = "thyroid") -> NoduleSegmenter:
        key = (organ or "thyroid").strip().lower()
        with cls._lock:
            inst = cls._instances.get(key)
            if inst is None:
                inst = cls(key)
                cls._instances[key] = inst
            return inst

    @classmethod
    def reset_instances(cls) -> None:
        """清空实例缓存（测试用：换权重/换设备后重新加载）。"""
        with cls._lock:
            cls._instances.clear()

    # ---------------------------------------------------------------- 加载
    def _resolve_device(self) -> str:
        from app.config.settings import settings

        pref = str(getattr(settings, "nodule_segmentation_device", "auto")).lower()
        if pref == "cpu":
            return "cpu"
        try:
            import torch
        except Exception as exc:  # pragma: no cover
            raise SegmentationUnavailableError(f"torch 不可用：{exc}") from exc
        if pref == "cuda":
            if not torch.cuda.is_available():
                raise SegmentationUnavailableError(
                    "配置要求 cuda，但 torch 看不到 GPU（检查 CUDA_VISIBLE_DEVICES）"
                )
            return "cuda"
        return "cuda" if torch.cuda.is_available() else "cpu"

    def _ensure_loaded(self) -> None:
        if self._net is not None:
            return
        from app.config.settings import settings
        from app.inference.organ_segmentation import UNET_ARCH_KEY, resolve

        # 1) 注册表闸门：未训练/无数据的器官在此明确失败（不静默换器官权重）
        spec = resolve(self._organ)

        if not bool(getattr(settings, "nodule_segmentation_enabled", True)):
            raise SegmentationUnavailableError("结节分割功能已关闭（nodule_segmentation_enabled=false）")

        device = self._resolve_device()
        path = _model_path(self._organ)
        if not path.exists():
            raise SegmentationUnavailableError(f"分割权重不存在：{path}")

        try:
            import torch
            from monai.networks.nets import UNet
        except Exception as exc:
            raise SegmentationUnavailableError(f"依赖不可用（torch/monai）：{exc}") from exc

        try:
            ckpt = torch.load(str(path), map_location="cpu", weights_only=False)
        except Exception as exc:
            raise SegmentationUnavailableError(f"权重加载失败：{exc}") from exc

        # 2) 架构闸门：权重与注册表 arch 不匹配 → 明确失败（宁可不服务，
        #    也不能拿不匹配的权重 strict-load 崩掉或静默错载）
        ckpt_arch = str(ckpt.get("arch_key") or "")
        if ckpt_arch and ckpt_arch != spec.arch_key:
            raise SegmentationUnavailableError(
                f"权重架构与注册表不符：{path} arch_key={ckpt_arch} "
                f"期望 {spec.arch_key}（{spec.organ}）"
            )
        if ckpt_arch == "" and spec.arch_key != UNET_ARCH_KEY:
            raise SegmentationUnavailableError(
                f"权重未登记 arch_key，无法确认与 {spec.organ} 架构兼容"
            )

        net = UNet(
            spatial_dims=2, in_channels=3, out_channels=1,
            channels=(32, 64, 128, 256, 320), strides=(2, 2, 2, 2),
            num_res_units=2, dropout=0.1,
        )
        net.load_state_dict(ckpt["state_dict"], strict=True)
        net.to(device).eval()
        self._net = net
        self._device = device
        self._meta = {
            "organ": self._organ,
            "arch_key": ckpt_arch or UNET_ARCH_KEY,
            "input_size": int(ckpt.get("input_size", spec.input_size)),
            "test_dice": ckpt.get("test_dice", spec.metrics.get("dice")),
            "test_iou": ckpt.get("test_iou", spec.metrics.get("iou")),
            "train_split": ckpt.get("train_split", spec.metrics.get("eval")),
            "generated_at": ckpt.get("generated_at"),
            "path": str(path),
        }
        logger.info(
            "nodule_segmentation_loaded",
            organ=self._organ,
            device=device,
            path=str(path),
            official_dice=self._meta["test_dice"],
        )

    # ------------------------------------------------------------ 预处理
    @staticmethod
    def _preprocess(pil_image) -> tuple[Any, dict[str, int]]:
        """方形缩放到 256×256（逐字复刻训练脚本），返回 (tensor, 变换参数)。"""
        import numpy as np
        import torch
        from PIL import Image

        img = pil_image.convert("RGB")
        w, h = img.size
        s = INPUT_SIZE / max(w, h)
        nw, nh = max(INPUT_SIZE, int(w * s + 0.5)), max(INPUT_SIZE, int(h * s + 0.5))
        img_r = img.resize((nw, nh), Image.BILINEAR)
        left, top = (nw - INPUT_SIZE) // 2, (nh - INPUT_SIZE) // 2
        img_c = img_r.crop((left, top, left + INPUT_SIZE, top + INPUT_SIZE))
        x = torch.from_numpy(np.asarray(img_c).copy()).permute(2, 0, 1).float() / 255.0
        params = {"orig_w": w, "orig_h": h, "scaled_w": nw, "scaled_h": nh,
                  "left": left, "top": top}
        return x, params

    @staticmethod
    def _postprocess(prob: Any, params: dict[str, int], out_w: int, out_h: int):
        """把 256×256 概率图逆变换回原图尺寸（最近邻，保持二值边界）。"""
        import numpy as np
        from PIL import Image

        arr = prob.detach().float().cpu().numpy()[0]
        # 概率图贴回缩放后画布，再裁掉偏移，最后缩放回原图
        canvas = np.zeros((params["scaled_h"], params["scaled_w"]), dtype=np.float32)
        canvas[params["top"]:params["top"] + INPUT_SIZE,
               params["left"]:params["left"] + INPUT_SIZE] = arr
        img = Image.fromarray(canvas, mode="F")
        return img.resize((out_w, out_h), Image.NEAREST)

    # -------------------------------------------------------------- 推理
    def infer(
        self,
        image_path: str | Path | None = None,
        *,
        image_bytes: bytes | None = None,
        threshold: float | None = None,
        out_size: tuple[int, int] | None = None,
    ) -> SegmentationResult:
        """分割一张超声图。

        Args:
            image_path / image_bytes: 二选一。
            threshold: 掩码二值化阈值，默认 ``settings.nodule_segmentation_threshold``。
            out_size: 输出掩码尺寸，默认与输入图一致（推荐，便于前端叠加）。

        Returns:
            ``SegmentationResult``；``bbox`` 为 None 表示未检出结节区域。
        """
        import io

        import numpy as np
        import torch
        from PIL import Image

        from app.config.settings import settings

        if (image_path is None) == (image_bytes is None):
            raise ValueError("image_path 与 image_bytes 必须且只能提供一个")
        t0 = time.perf_counter()
        self._ensure_loaded()
        thr = float(threshold if threshold is not None
                    else getattr(settings, "nodule_segmentation_threshold", DEFAULT_THRESHOLD))

        # 参数校验已保证二者互斥（else 分支仅类型收窄）
        pil = (Image.open(io.BytesIO(image_bytes)) if image_bytes is not None
               else Image.open(str(image_path)))
        ow, oh = pil.size
        out_w, out_h = out_size or (ow, oh)

        x, params = self._preprocess(pil)
        with torch.no_grad():
            logits = self._net(x.unsqueeze(0).to(self._device))
            prob = torch.sigmoid(logits)

        prob_full = self._postprocess(prob, params, out_w, out_h)
        prob_np = np.asarray(prob_full, dtype=np.float32)
        mask = (prob_np > thr).astype(np.uint8)

        ys, xs = np.nonzero(mask)
        bbox = None
        if xs.size:
            bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)

        import io as _io

        buf = _io.BytesIO()
        Image.fromarray((mask * 255).astype(np.uint8), mode="L").save(buf, format="PNG")

        return SegmentationResult(
            mask_png=buf.getvalue(),
            width=out_w,
            height=out_h,
            threshold=thr,
            input_size=INPUT_SIZE,
            lesion_area_ratio=float(mask.mean()),
            bbox=bbox,
            mean_prob=float(prob_np.mean()),
            max_prob=float(prob_np.max()),
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            device=self._device,
            model_dice_official=self._meta.get("test_dice"),
        )

    def health(self) -> dict[str, Any]:
        """健康检查（不抛异常，供 /health 与降级矩阵使用）。"""
        try:
            self._ensure_loaded()
        except SegmentationUnavailableError as exc:
            return {"available": False, "organ": self._organ, "reason": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {
                "available": False,
                "organ": self._organ,
                "reason": f"{type(exc).__name__}: {exc}",
            }
        return {
            "available": True,
            "organ": self._organ,
            "device": self._device,
            "model": self._meta,
        }

    def warmup(self) -> dict[str, Any]:
        """启动预热：加载权重 + 真实前向一次（消首次判读 5.7s 冷启动）。

        为什么用合成图真跑一遍而不是只 ``health()``（仅加载权重）：
        首次判读的冷启动大头除了权重加载，还有 CUDA kernel 的按需编译/
        显存池分配——只在第一次前向时发生。用一张 64x64 灰度合成图跑完整
        infer 链路（预处理→前向→后处理），首诊判读即可落到热身水平
        （实测 5713ms → 50ms）。失败不抛（调用方 best-effort）。
        """
        import io as _io
        import time as _time

        t0 = _time.perf_counter()
        try:
            # 合成 64x64 灰度图（非纯黑，带梯度，让前向走真实数值路径）
            import numpy as np
            from PIL import Image

            arr = (np.linspace(20, 200, 64 * 64, dtype=np.float32)
                   .reshape(64, 64).astype(np.uint8))
            buf = _io.BytesIO()
            Image.fromarray(arr, mode="L").save(buf, format="PNG")
            self.infer(image_bytes=buf.getvalue())
            return {
                "available": True,
                "organ": self._organ,
                "warmup_ms": round((_time.perf_counter() - t0) * 1000, 1),
            }
        except SegmentationUnavailableError as exc:
            # 权重缺失/功能关闭：如实返回，不视为错误（liver 等未训练器官）
            return {"available": False, "organ": self._organ, "reason": str(exc)}
        except Exception as exc:  # pragma: no cover
            return {
                "available": False,
                "organ": self._organ,
                "reason": f"{type(exc).__name__}: {exc}",
            }


__all__ = [
    "NoduleSegmenter",
    "SegmentationResult",
    "SegmentationUnavailableError",
]
