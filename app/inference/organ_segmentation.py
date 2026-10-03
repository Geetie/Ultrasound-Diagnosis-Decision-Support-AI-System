"""器官分割注册表——多器官架构（2026-10-03）。

为什么需要
----------
甲状腺结节分割已跑通（UNet / TN3K 官方划分 DSC 0.7961），但它是**甲状腺专用**：
权重与预处理都按"甲状腺超声"固化。要扩到别的器官，不能每个器官写一套推理代码，
故抽成注册表：**器官 → (权重路径, 架构, 预处理约定, 指标)**。

现状（务必区分"已验证"与"未验证"）
--------------------------------
- thyroid：**已训练并全量验证**（DSC 0.7961 / IoU 0.7004，TN3K 2879/614 官方划分），
  训练数据与指标见 reports/tn3k_seg_unet.json
- cardiac：**架构就绪、权重待训练**。公开数据集 CAMUS（500 患者 2CH/4CH，
  LV 心内膜/心外膜/左心房标注）。⚠️ 未训练前调用会显式 unavailable——
  绝不能拿甲状腺权重冒充心脏模型（形态与解剖完全不同）。
- liver：**无公开超声肝分割数据**。公开的肝分割基准是 CT（LiTS）；
  超声方向只有乳腺（BUSI/BUS-UCLM）等其他器官，肝超声标注数据需院内自建或
  私有合作（结论见 16 号文档 A.21 ⑤-5）。因此 liver 在注册表中标记为
  ``data_unavailable``，调用方得到的是明确的"无数据"原因而非"模型没训好"。

设计约束
--------
1. **不预加载**：注册表只做元数据 + 惰性取引擎，未注册器官不占内存。
2. **失败语义统一**：未训练/无数据 → ``SegmentationUnavailableError``（消息区分二者），
   API 层 503 + 原因；管线内 best-effort 旁路只 warning。
3. **不静默降级到别的器官权重**：这是临床安全红线——器官错配会产出看似合理的
   错误轮廓，比明确失败危险得多。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class OrganSegmentationSpec:
    """一个器官的分割能力登记。"""

    organ: str
    weight_path: str | None
    status: str  # trained | untrained | data_unavailable
    arch: str
    input_size: int = 256
    preprocess: str = "square_resize_256"
    metrics: dict[str, Any] = field(default_factory=dict)
    dataset: str | None = None
    note: str = ""

    @property
    def available(self) -> bool:
        return self.status == "trained"

    def unavailable_reason(self) -> str | None:
        if self.available:
            return None
        if self.status == "untrained":
            return f"{self.organ} 分割权重尚未训练（架构已就绪，缺训练数据）"
        if self.status == "data_unavailable":
            return f"{self.organ} 无公开超声分割数据集（{self.note or '需院内自建/私有合作'}）"
        return f"{self.organ} 分割不可用（status={self.status}）"

    def as_dict(self) -> dict[str, Any]:
        return {
            "organ": self.organ,
            "status": self.status,
            "available": self.available,
            "weight_path": self.weight_path,
            "arch": self.arch,
            "input_size": self.input_size,
            "preprocess": self.preprocess,
            "dataset": self.dataset,
            "metrics": self.metrics,
            "note": self.note,
            "unavailable_reason": self.unavailable_reason(),
        }


_UNET_TN3K = (
    "monai.UNet(spatial_dims=2, channels=(32,64,128,256,320), strides=(2,2,2,2),"
    " num_res_units=2, dropout=0.1)"
)

REGISTRY: dict[str, OrganSegmentationSpec] = {
    "thyroid": OrganSegmentationSpec(
        organ="thyroid",
        weight_path="~/uai/models/nodule_seg/unet_tn3k.pt",
        status="trained",
        arch=_UNET_TN3K,
        dataset="TN3K 官方划分（trainval 2879 / test 614）",
        metrics={"dice": 0.7961, "iou": 0.7004, "eval": "官方 test 614 张全量"},
        note="甲状腺结节（浅低回声病灶）分割，已全量验证",
    ),
    "cardiac": OrganSegmentationSpec(
        organ="cardiac",
        weight_path="~/uai/models/cardiac_seg/unet_camus.pt",
        status="untrained",
        arch=_UNET_TN3K + "（结构一致，权重另训）",
        dataset="CAMUS（500 患者 2CH/4CH，LV 心内膜/心外膜 + 左心房）",
        metrics={},
        note="架构就绪；公开数据可得（hf-mirror 有 1000 张子集），待训练",
    ),
    "liver": OrganSegmentationSpec(
        organ="liver",
        weight_path=None,
        status="data_unavailable",
        arch=_UNET_TN3K + "（结构一致，权重另训）",
        dataset=None,
        metrics={},
        note="无公开超声肝分割数据（公开肝基准 LiTS 是 CT；BUSI/BUS-UCLM 是乳腺）",
    ),
}


def get_spec(organ: str) -> OrganSegmentationSpec:
    """取器官登记；未注册抛错（消息说明可选器官）。"""
    key = (organ or "").strip().lower()
    spec = REGISTRY.get(key)
    if spec is None:
        raise KeyError(
            f"未注册的器官：{key!r}；已注册 {sorted(REGISTRY)}"
        )
    return spec


def list_specs() -> dict[str, dict[str, Any]]:
    return {k: v.as_dict() for k, v in REGISTRY.items()}


def resolve(organ: str) -> OrganSegmentationSpec:
    """取**可用**的器官登记；不可用则抛带原因的异常（不静默换器官）。"""
    from app.inference.nodule_segmentation import SegmentationUnavailableError

    spec = get_spec(organ)
    if not spec.available:
        reason = spec.unavailable_reason()
        logger.warning(
            "organ_segmentation_unavailable", organ=organ, status=spec.status, reason=reason
        )
        raise SegmentationUnavailableError(reason)
    return spec


__all__ = [
    "REGISTRY",
    "OrganSegmentationSpec",
    "get_spec",
    "list_specs",
    "resolve",
]
