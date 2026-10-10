"""A.50：告警联系人 PUT 语义定案（此前两类环境恒 400，探针 p7 实测）。

旧实现：任何 PUT 都先跑可达性验证——
①webhook_url 未配置 → 验证无从执行直接 False → 联系人表**永远无法配置**；
②清空（[]）也要过验证 → 连「读改写原值」的 no-op 都失败。

新语义（manager.update_contacts）：
①空表 = 管理员显式清空，免验证直接生效（告警升级退化为日志）；
②未配 webhook = 跳过验证放行（fail-closed 无安全收益）；
③已配 webhook 且非空表 = 保持可达性验证，失败回滚 + 告警事件。
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from app.observability.alert_escalation.manager import AlertEscalationManager
from app.observability.alert_escalation.models import AlertContact

pytestmark = pytest.mark.track_obs

_CONTACT = [{"name": "值班医师", "role": "department", "phone": "13800000000", "email": "duty@h.org"}]


@pytest.mark.asyncio
async def test_empty_list_clears_without_verification():
    """清空是显式管理动作：无联系人可验证，直接生效。"""
    manager = AlertEscalationManager(webhook_url="http://127.0.0.1:9/hook")
    manager._contacts = [AlertContact(name="旧联系人", role="department")]

    assert await manager.update_contacts("[]") is True
    assert manager.get_contacts() == []


@pytest.mark.asyncio
async def test_no_webhook_skips_verification_and_applies():
    """未配 webhook_url：跳过可达性验证并放行（旧实现恒 400，表无法配置）。"""
    manager = AlertEscalationManager(webhook_url="")

    assert await manager.update_contacts(json.dumps(_CONTACT)) is True
    contacts = manager.get_contacts()
    assert len(contacts) == 1
    assert contacts[0]["name"] == "值班医师"


@pytest.mark.asyncio
async def test_webhook_configured_unreachable_rolls_back(monkeypatch):
    """已配 webhook 且不可达：保持回滚语义 + 发出告警事件。"""
    manager = AlertEscalationManager(webhook_url="http://127.0.0.1:9/hook")
    monkeypatch.setattr(
        "app.observability.alert_escalation.manager.verify_contact_reachability",
        AsyncMock(return_value=False),
    )

    assert await manager.update_contacts(json.dumps(_CONTACT)) is False
    assert manager.get_contacts() == []
    metrics = [a["metric"] for a in manager.get_active_alerts()]
    assert "alert_contacts_reachability" in metrics


@pytest.mark.asyncio
async def test_webhook_configured_reachable_applies(monkeypatch):
    manager = AlertEscalationManager(webhook_url="http://127.0.0.1:9/hook")
    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "app.observability.alert_escalation.manager.verify_contact_reachability",
        verify,
    )

    assert await manager.update_contacts(json.dumps(_CONTACT)) is True
    verify.assert_awaited_once()
    assert len(manager.get_contacts()) == 1


@pytest.mark.asyncio
async def test_malformed_json_rejected():
    manager = AlertEscalationManager(webhook_url="")
    assert await manager.update_contacts("not-json") is False
    assert manager.get_contacts() == []
