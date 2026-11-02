"""影像块"视觉特征文本"的产出通道（A.137 / G2）。

## 缺口

影像腿进 prompt 的正文是空壳：`[影像参考] img_x；模态=B-mode；部位=thyroid`（G2）。
没有成分/回声/边界/钙化这类**视觉特征**，影像证据在融合与报告里等于噪声。
补齐它有两条来源：①影像模型/VLM 生成的描述；②DICOM 元数据里真有的结构化事实。
前者需要模型服务（本机没有），后者不构成"诊断所见"（不能当特征用）。

## 设计（可插拔、默认关闭、绝不编造）

- `describe(payload, *, captioner=None)`：先看 payload 里**已存在**的
  `visual_features`（索引期由 VLM 写入的字符串/字符串列表）；没有就调 `captioner`
  （若配置了 `settings.image_caption_endpoint`）；都没有 → 返回 `("", "none")`。
- **不编造**：任何"结节边界清/低回声"之类的临床描述，只能来自模型输出或医生，
  本模块不生成、不推断、不用模板凑。
- 失败降级：captioner 异常 → 记录 warning 并返回 `("", "caption_failed")`，
  调用方据此保留原有元数据文本（"没特征"要能被看出来，不能装成"特征为空即正常"）。
- 默认关闭：`image_caption_endpoint` 为空 → 不发任何请求（与现状一致）。

于是"接入 VLM"变成一个配置项 + 一次索引重建，而不是一次跨模块改造。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.rag.image_caption")

MAX_FEATURE_CHARS = 400


def _normalise_features(raw: Any) -> str:
    """把 visual_features 收敛成一行（字符串 / 字符串列表；其它形状忽略）。"""
    if isinstance(raw, str):
        text = raw.strip()
    elif isinstance(raw, Sequence) and not isinstance(raw, (bytes, bytearray)):
        parts = [str(x).strip() for x in raw if str(x).strip()]
        text = "；".join(parts)
    else:
        return ""
    return text[:MAX_FEATURE_CHARS]


async def describe(
    payload: dict[str, Any] | None,
    *,
    captioner: Any | None = None,
) -> tuple[str, str]:
    """返回 ``(特征文本, 来源)``；来源 ∈ ``visual_features`` / ``caption_model`` / ``none`` / ``caption_failed``。

    ``captioner`` 需提供 ``async def caption(payload) -> str``；不传则用
    `settings.image_caption_endpoint`（空 = 不启用）。
    """
    p = payload or {}

    # 1) 索引期已写入的特征（最可信：来自当时的模型输出，且有原始依据）
    existing = _normalise_features(p.get("visual_features"))
    if existing:
        return existing, "visual_features"

    # 2) 现场调用 captioner（仅当显式配置）
    endpoint = str(getattr(settings, "image_caption_endpoint", "") or "").strip()
    if captioner is None and not endpoint:
        return "", "none"
    try:
        if captioner is None:  # 延迟导入，避免默认路径加载 httpx/客户端
            from app.rag.caption_http import HttpImageCaptioner

            captioner = HttpImageCaptioner(endpoint)
        text = _normalise_features(await captioner.caption(p))
        return (text, "caption_model") if text else ("", "none")
    except Exception as exc:  # silent-ok: 影像描述失败只降级为"无特征"，并留 warning 供排查
        logger.warning(
            "image_caption_failed",
            error=str(exc)[:160],
            error_type=type(exc).__name__,
        )
        return "", "caption_failed"
