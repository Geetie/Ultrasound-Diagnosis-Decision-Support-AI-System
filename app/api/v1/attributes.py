"""诊断属性提示 API（报告一致性校验旁路）。

- ``POST /api/v1/attributes/hints``：提交报告文本 → 各维度 zero-shot 提示 + 冲突标记
- ``GET  /api/v1/attributes/health``：权重/设备可用性（永不 503，能力位在 body）

设计原则：这是**旁路校验**，不参与诊断结论；能力不可用时显式 503 + 原因。
"""
from __future__ import annotations

import asyncio

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/attributes", tags=["attributes"])

MAX_TEXT_CHARS = 20000


class AttributeHintsRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=MAX_TEXT_CHARS,
                      description="报告文本（结论段/描述段）")
    text_hint: str | None = Field(
        None, max_length=2000,
        description="可选提示：告诉模型器官上下文（不参与打分，仅占位便于将来扩展）",
    )


@router.post("/hints")
async def attribute_hints(req: AttributeHintsRequest) -> dict:
    """对报告文本给出属性维度提示与一致性比对结果。"""
    from app.inference.attribute_hints import (
        AttributeHintEngine,
        AttributeHintsUnavailableError,
    )

    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="text 不能为空")

    try:
        # 同步 CPU 推理（text tower ~400MB）必须放线程池，勿阻塞事件循环
        result = await asyncio.to_thread(AttributeHintEngine.get().hints_for_text, text)
    except AttributeHintsUnavailableError as exc:
        logger.warning("attribute_hints_unavailable", error=str(exc))
        raise HTTPException(status_code=503, detail=f"属性提示不可用：{exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # pragma: no cover
        logger.error("attribute_hints_failed", error=str(exc), error_type=type(exc).__name__)
        raise HTTPException(status_code=500, detail="属性提示执行失败") from exc

    return result.as_dict()


@router.get("/health")
async def attribute_hints_health() -> dict:
    """属性提示能力可用性（永远 200，故障体现在 available/reason）。"""
    from app.inference.attribute_hints import AttributeHintEngine

    return AttributeHintEngine.get().health()
