"""影像描述 HTTP 客户端（A.137 / G2 的可插拔后端）。

契约（最小、可替换）：
    POST {endpoint}
    body: {"image_id": ..., "session_id": ..., "modality": ..., "organ": ...}
    响应: {"description": "低回声结节，边界清…"} 或 {"description": ""}

不做重试与熔断：描述是**增强信息**，失败即降级为"无特征"（由调用方记录来源），
用重试把一次判读拖长反而更糟。超时默认 5s（`image_caption_timeout_seconds`）。
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.rag.caption_http")

_DEFAULT_TIMEOUT = 5.0


class HttpImageCaptioner:
    def __init__(self, endpoint: str, *, timeout: float | None = None) -> None:
        if not endpoint:
            raise ValueError("image caption endpoint 为空")
        self._endpoint = endpoint
        self._timeout = timeout or float(
            getattr(settings, "image_caption_timeout_seconds", _DEFAULT_TIMEOUT)
        )

    async def caption(self, payload: dict[str, Any] | None) -> str:
        body = {
            key: (payload or {}).get(key)
            for key in ("image_id", "session_id", "modality", "organ")
            if (payload or {}).get(key)
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(self._endpoint, json=body)
        if resp.status_code != 200:
            raise RuntimeError(f"caption HTTP {resp.status_code}")
        data = resp.json()
        description = data.get("description") if isinstance(data, dict) else None
        return str(description).strip() if description else ""
