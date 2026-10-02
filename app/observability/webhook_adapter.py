"""AlertManager Webhook适配器 — 将AlertManager告警转发至飞书/钉钉/企业微信。

轻量FastAPI服务，接收AlertManager webhook请求，
根据路径(/feishu, /dingtalk, /wecom)转发至对应IM平台的webhook URL。
IM平台webhook URL通过环境变量配置。
"""

import os
from typing import Any

import httpx
import structlog
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title="AlertManager Webhook Adapter", version="1.0.0")

_logger = structlog.get_logger("alertmanager_webhook_adapter")

FEISHU_WEBHOOK_URL = os.getenv("FEISHU_WEBHOOK_URL", "")
DINGTALK_WEBHOOK_URL = os.getenv("DINGTALK_WEBHOOK_URL", "")
WECOM_WEBHOOK_URL = os.getenv("WECOM_WEBHOOK_URL", "")


def _format_alerts_text(alerts: list[dict[str, Any]]) -> str:
    if not alerts:
        return "无告警详情"
    lines = []
    for alert in alerts:
        status = alert.get("status", "firing")
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        alertname = labels.get("alertname", "未知告警")
        severity = labels.get("severity", "unknown")
        summary = annotations.get("summary", "")
        lines.append(f"[{status.upper()}] {alertname} (severity={severity})")
        if summary:
            lines.append(f"  {summary}")
    return "\n".join(lines)


def _build_feishu_payload(text: str) -> dict[str, Any]:
    return {
        "msg_type": "interactive",
        "card": {
            "header": {
                "title": {"tag": "plain_text", "content": "🚨 超声诊断系统告警"},
                "template": "red" if "CRITICAL" in text or "SAFETY_MELTDOWN" in text else "orange",
            },
            "elements": [
                {"tag": "markdown", "content": f"```\n{text}\n```"}
            ],
        },
    }


def _build_dingtalk_payload(text: str) -> dict[str, Any]:
    return {
        "msgtype": "markdown",
        "markdown": {
            "title": "超声诊断系统告警",
            "text": f"### 🚨 超声诊断系统告警\n\n{text}",
        },
    }


def _build_wecom_payload(text: str) -> dict[str, Any]:
    return {
        "msgtype": "markdown",
        "markdown": {
            "content": f"### 🚨 超声诊断系统告警\n\n{text}",
        },
    }


async def _forward_to_im(webhook_url: str, payload: dict[str, Any], platform: str) -> None:
    if not webhook_url:
        _logger.warning("webhook_not_configured", platform=platform)
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(webhook_url, json=payload)
            if resp.status_code >= 400:
                _logger.error(
                    "webhook_send_failed",
                    platform=platform,
                    status_code=resp.status_code,
                    response=resp.text[:200],
                )
            else:
                _logger.info("webhook_sent", platform=platform, status_code=resp.status_code)
    except Exception as e:
        _logger.error("webhook_exception", platform=platform, error=str(e))


@app.post("/feishu")
async def feishu_webhook(request: Request) -> Response:
    if not FEISHU_WEBHOOK_URL:
        raise HTTPException(status_code=503, detail="飞书webhook未配置")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    text = _format_alerts_text(body.get("alerts", []))
    payload = _build_feishu_payload(text)
    await _forward_to_im(FEISHU_WEBHOOK_URL, payload, "feishu")
    return Response(status_code=200)


@app.post("/dingtalk")
async def dingtalk_webhook(request: Request) -> Response:
    if not DINGTALK_WEBHOOK_URL:
        raise HTTPException(status_code=503, detail="钉钉webhook未配置")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    text = _format_alerts_text(body.get("alerts", []))
    payload = _build_dingtalk_payload(text)
    await _forward_to_im(DINGTALK_WEBHOOK_URL, payload, "dingtalk")
    return Response(status_code=200)


@app.post("/wecom")
async def wecom_webhook(request: Request) -> Response:
    if not WECOM_WEBHOOK_URL:
        raise HTTPException(status_code=503, detail="企业微信webhook未配置")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    text = _format_alerts_text(body.get("alerts", []))
    payload = _build_wecom_payload(text)
    await _forward_to_im(WECOM_WEBHOOK_URL, payload, "wecom")
    return Response(status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}
