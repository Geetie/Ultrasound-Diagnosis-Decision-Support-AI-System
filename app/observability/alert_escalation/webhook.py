import structlog

from .models import AlertEvent

_logger = structlog.get_logger("app.observability.alert_escalation.webhook")


async def send_webhook(webhook_url: str, level: str, alert: AlertEvent) -> None:
    if not webhook_url:
        return
    try:
        import httpx

        payload = {
            "alert_id": alert.alert_id,
            "level": level,
            "metric": alert.metric,
            "value": alert.value,
            "threshold": alert.threshold,
            "message": alert.message,
            "created_at": alert.created_at,
            "escalation_level": alert.escalation_level,
        }
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(webhook_url, json=payload)
            _logger.info(
                "webhook_sent",
                alert_id=alert.alert_id,
                status_code=resp.status_code,
            )
    except Exception as e:
        _logger.error("webhook_send_failed", error=str(e))
