"""内部告警回调接收端点。

## 存在的理由

`config/alertmanager.yml:59` 与 `docker-compose.dev.yml:247` 都把告警回调指向
`/api/v1/internal/alerts/webhook`，但**代码里从未存在这个路由** —— 也就是说
Alertmanager 的 `webhook` receiver 与 vmbackup 的失败通知**一直在收 404**，
告警实际只依赖 IM（飞书/钉钉/企微）那条链路。

本模块补齐该端点，并兼容两个真实发送方的载荷：

- Alertmanager v4：`{version, status, groupKey, alerts:[{status,labels,annotations,...}]}`
  （配置里 `send_resolved: true`，所以 `status` 可能是 `resolved`）；
- vmbackup：`{"type":"vmbackup_failure","message":"..."}`（见 compose 里的 wget 调用）。

## 认证

内网端点，默认不校验；**一旦配置 `alert_webhook_token` 则强制校验**
（`X-Alert-Token` 或 `Authorization: Bearer`），校验失败返回 401 —— 即"配置了就 fail-closed"，
避免出现"以为有鉴权实际没有"的情况。
"""
from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.config.settings import settings

__all__ = ["router"]

logger = structlog.get_logger("app.api.v1.alerts")

router = APIRouter(tags=["internal-alerts"])


class AlertmanagerAlert(BaseModel):
    """Alertmanager v4 的单条告警（只取我们实际会用到的字段）。"""

    model_config = {"extra": "allow"}

    status: str = "firing"
    labels: dict[str, str] = Field(default_factory=dict)
    annotations: dict[str, str] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None
    fingerprint: str | None = None


class InternalAlertPayload(BaseModel):
    """兼容 Alertmanager v4 与 vmbackup 两种载荷（未知字段保留，便于排查）。"""

    model_config = {"extra": "allow"}

    # Alertmanager
    version: str | None = None
    status: str | None = None
    groupKey: str | None = None
    receiver: str | None = None
    alerts: list[AlertmanagerAlert] = Field(default_factory=list)
    # vmbackup（compose 里的一行 JSON）
    type: str | None = None
    message: str | None = None

    def source(self) -> str:
        if self.alerts or self.version:
            return "alertmanager"
        if self.type:
            return "vmbackup"
        return "unknown"


def _verify_token(token: str | None, authorization: str | None) -> None:
    """配置了 token 就必须匹配（fail-closed）；未配置则放行（内网部署）。"""
    expected = (settings.alert_webhook_token or "").strip()
    if not expected:
        return
    provided = (token or "").strip()
    if not provided and authorization and authorization.lower().startswith("bearer "):
        provided = authorization[7:].strip()
    if provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid alert webhook token",
        )


@router.post("/webhook")
async def receive_alert_webhook(
    payload: InternalAlertPayload,
    x_alert_token: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
    """接收告警回调。

    **永远返回 200**（除非鉴权失败）：Alertmanager 对非 2xx 会重试并进入退避，
    在告警风暴时重试风暴比丢一条通知更危险；载荷本身只做落日志与计数。
    """
    _verify_token(x_alert_token, authorization)

    firing = [alert for alert in payload.alerts if alert.status != "resolved"]
    resolved = [alert for alert in payload.alerts if alert.status == "resolved"]
    alert_names = sorted(
        {alert.labels.get("alertname", "") for alert in payload.alerts if alert.labels}
    )

    if payload.source() == "alertmanager":
        logger.warning(
            "alertmanager_webhook_received",
            status=payload.status,
            receiver=payload.receiver,
            group_key=payload.groupKey,
            firing_count=len(firing),
            resolved_count=len(resolved),
            alert_names=alert_names,
        )
    else:
        logger.error(
            "internal_alert_webhook_received",
            source=payload.source(),
            alert_type=payload.type,
            message=payload.message,
        )

    return {
        "status": "ok",
        "source": payload.source(),
        "firing": len(firing),
        "resolved": len(resolved),
        "alert_names": alert_names,
    }
