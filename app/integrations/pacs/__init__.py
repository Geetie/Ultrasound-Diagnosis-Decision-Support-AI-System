"""T-GB-API-02 — PACS / HIS 报告推送适配器。

契约：``PacsPushAdapter``（Protocol）。实现：

- :class:`LocalFakePacsAdapter` — 本地假实现，记录推送内容并通过，
  用于在无 PACS 环境下验证"草稿 → 签名 → 推送 → 状态回写"全链路；
- :class:`HttpPacsAdapter` — 现场对接用 HTTP 实现（POST JSON，带重试与
  指数退避、幂等键）。

由 :func:`create_pacs_adapter` 按 ``settings.pacs_adapter`` 选择；
现场亦可用 :func:`set_pacs_adapter` 直接注入自定义实现。
推送失败**不阻塞**草稿与签名（报告状态单独记录 pacs_status/pacs_message）。
"""
from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

import structlog

from app.config.settings import settings

logger = structlog.get_logger("app.integrations.pacs")


@dataclass
class PushResult:
    """一次推送尝试的结果。"""

    success: bool
    adapter: str = "local_fake"
    message: str = ""
    attempts: int = 1
    skipped: bool = False
    status_code: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "adapter": self.adapter,
            "message": self.message,
            "attempts": self.attempts,
            "skipped": self.skipped,
            "status_code": self.status_code,
        }


@runtime_checkable
class PacsPushAdapter(Protocol):
    """报告推送契约。"""

    @property
    def name(self) -> str:
        """适配器名（写入 ``diagnosis_reports.pacs_adapter``）。"""
        ...

    async def push_report(
        self,
        *,
        report_id: str,
        payload: bytes,
        idempotency_key: str,
        patient_id: str | None = None,
        organ: str | None = None,
        content_type: str = "text/plain; charset=utf-8",
    ) -> PushResult:
        """推送一份已签名的报告。"""
        ...

    async def health(self) -> bool:
        """适配器可用性。"""
        ...


class LocalFakePacsAdapter:
    """本地假实现：记录推送内容并返回成功。"""

    def __init__(self, fail_times: int = 0) -> None:
        self.pushes: list[dict[str, Any]] = []
        # 前 N 次推送失败（用于验证重试/降级路径）
        self._fail_times = fail_times
        self._attempts_seen = 0

    @property
    def name(self) -> str:
        return "local_fake"

    async def push_report(
        self,
        *,
        report_id: str,
        payload: bytes,
        idempotency_key: str,
        patient_id: str | None = None,
        organ: str | None = None,
        content_type: str = "text/plain; charset=utf-8",
    ) -> PushResult:
        self._attempts_seen += 1
        if self._attempts_seen <= self._fail_times:
            logger.warning("pacs_local_fake_push_failed", report_id=report_id)
            return PushResult(
                success=False,
                adapter=self.name,
                message="simulated failure",
                attempts=1,
            )
        self.pushes.append(
            {
                "report_id": report_id,
                "payload": payload,
                "idempotency_key": idempotency_key,
                "patient_id": patient_id,
                "organ": organ,
                "content_type": content_type,
            }
        )
        logger.info("pacs_local_fake_pushed", report_id=report_id)
        return PushResult(success=True, adapter=self.name, attempts=1)

    async def health(self) -> bool:
        return True


class HttpPacsAdapter:
    """现场 HTTP 实现：POST 报告 JSON，带重试与指数退避。

    Args:
        endpoint: PACS/HIS 报告接口 URL。
        timeout_seconds: 单次请求超时。
        max_attempts: 最大尝试次数（含首次）。
        backoff_seconds: 退避基数，第 n 次重试等待 ``backoff * 2^(n-1)``。
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_seconds: float = 10.0,
        max_attempts: int = 3,
        backoff_seconds: float = 0.5,
    ) -> None:
        if not endpoint:
            raise ValueError("HttpPacsAdapter requires a non-empty endpoint")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds

    @property
    def name(self) -> str:
        return "http"

    async def push_report(
        self,
        *,
        report_id: str,
        payload: bytes,
        idempotency_key: str,
        patient_id: str | None = None,
        organ: str | None = None,
        content_type: str = "text/plain; charset=utf-8",
    ) -> PushResult:
        import httpx

        attempt = 0
        last_message = ""
        last_status: int | None = None
        while attempt < self._max_attempts:
            attempt += 1
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    response = await client.post(
                        self._endpoint,
                        content=payload,
                        headers={
                            "Content-Type": content_type,
                            "Idempotency-Key": idempotency_key,
                            "X-Report-Id": report_id,
                        },
                        params={"patient_id": patient_id, "organ": organ},
                    )
                last_status = response.status_code
                if 200 <= response.status_code < 300:
                    logger.info(
                        "pacs_push_succeeded",
                        report_id=report_id,
                        attempts=attempt,
                    )
                    return PushResult(
                        success=True,
                        adapter=self.name,
                        attempts=attempt,
                        status_code=response.status_code,
                    )
                last_message = f"HTTP {response.status_code}"
                # 4xx 属客户端问题，重试无意义
                if 400 <= response.status_code < 500:
                    break
            except Exception as exc:
                last_message = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "pacs_push_attempt_failed",
                    report_id=report_id,
                    attempt=attempt,
                    error=last_message,
                )
            if attempt < self._max_attempts:
                await asyncio.sleep(self._backoff * (2 ** (attempt - 1)))

        logger.error(
            "pacs_push_failed",
            report_id=report_id,
            attempts=attempt,
            message=last_message,
        )
        return PushResult(
            success=False,
            adapter=self.name,
            message=last_message,
            attempts=attempt,
            status_code=last_status,
        )

    async def health(self) -> bool:
        return True


_adapter: PacsPushAdapter | None = None


def set_pacs_adapter(adapter: PacsPushAdapter | None) -> None:
    """注入（或清除）进程级 PACS 适配器。"""
    global _adapter
    _adapter = adapter


def create_pacs_adapter(settings_obj: Any = None) -> PacsPushAdapter:
    """按 ``settings.pacs_adapter`` 构造适配器。

    Raises:
        ValueError: 配置为 ``http`` 但 ``pacs_endpoint`` 为空，或名字未知。
            显式失败而非静默降级，避免生产环境"看起来在推送"。
    """
    name = str(getattr(settings_obj or settings, "pacs_adapter", "local_fake"))
    if name == "local_fake":
        return LocalFakePacsAdapter()
    if name == "http":
        endpoint = str(getattr(settings_obj or settings, "pacs_endpoint", "") or "")
        return HttpPacsAdapter(
            endpoint,
            timeout_seconds=float(
                getattr(settings_obj or settings, "pacs_timeout_seconds", 10.0)
            ),
            max_attempts=int(
                getattr(settings_obj or settings, "pacs_max_attempts", 3)
            ),
            backoff_seconds=float(
                getattr(settings_obj or settings, "pacs_retry_backoff_seconds", 0.5)
            ),
        )
    raise ValueError(
        f"unknown pacs_adapter: {name!r} "
        "(expected 'local_fake' or 'http'; use set_pacs_adapter() to inject)"
    )


def get_pacs_adapter() -> PacsPushAdapter:
    """取得进程级适配器（已注入的优先，否则按配置构造）。"""
    global _adapter
    if _adapter is None:
        _adapter = create_pacs_adapter()
    return _adapter


def encode_payload(text: str) -> bytes:
    """报告正文 → 推送载荷（UTF-8 字节）。"""
    return text.encode("utf-8")


def decode_signature(signature: str | None) -> bytes | None:
    """base64 签名 → 原始字节（校验用）。"""
    if not signature:
        return None
    try:
        return base64.b64decode(signature)
    except Exception:
        return None


__all__ = [
    "HttpPacsAdapter",
    "LocalFakePacsAdapter",
    "PacsPushAdapter",
    "PushResult",
    "create_pacs_adapter",
    "decode_signature",
    "encode_payload",
    "get_pacs_adapter",
    "set_pacs_adapter",
]
