"""A.118 / G43：改运行时安全阈值必须进**关键审计链**。

缺口实测：`CriticalOperationAudit.log_critical_operation()` 全仓零生产调用，而
`threshold_adjustment` 在 `obs_audit_critical_operations` 名单里 —— 这条"改对患者放行/拦截
行为"的操作一条关键链记录都没有。

三条不变量：
1. 下发成功后写关键链（operation=threshold_adjustment，operator=登录用户，detail 带动作/器官/阈值）；
2. 下发失败（Redis 不可用）→ **不写**审计（不记录"改过"的假事实）；
3. 审计写失败 → 500 + 明确文案（阈值已生效却无法留痕，属合规事故，不能让调用方误以为成功）。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.api.v1 import alert_admin
from app.api.v1.alert_admin import SGThresholdUpdateRequest, publish_sg_threshold_update

pytestmark = pytest.mark.track_api


class _User:
    pass


def _current_user():
    return {"user_id": "admin_1", "role": "SYSTEM_ADMIN"}


class _RedisOK:
    def __init__(self):
        self.added: list[tuple] = []

    async def xadd(self, stream, payload):
        self.added.append((stream, payload))


class _RedisBoom:
    async def xadd(self, stream, payload):
        raise RuntimeError("redis down")


def _fake_db():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=lambda: None))
    return db


@pytest.mark.asyncio
async def test_success_writes_critical_chain(monkeypatch):
    db = _fake_db()
    redis = _RedisOK()
    monkeypatch.setattr(alert_admin.RedisPool, "get_client", AsyncMock(return_value=redis))

    out = await publish_sg_threshold_update(
        SGThresholdUpdateRequest(action="update", organ="thyroid", threshold=0.7),
        db=db,
        current_user=_current_user(),
    )

    assert out["status"] == "published"
    assert out["audit_chain"] == "critical"
    assert out["audited_by"] == "admin_1"
    added = db.add.call_args[0][0]
    assert added.action == "threshold_adjustment"
    assert added.actor == "admin_1"
    assert added.is_critical_operation is True
    assert added.detail["organ"] == "thyroid"
    assert added.detail["threshold"] == 0.7
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_publish_failure_writes_no_audit(monkeypatch):
    db = _fake_db()
    monkeypatch.setattr(alert_admin.RedisPool, "get_client", AsyncMock(return_value=_RedisBoom()))

    with pytest.raises(HTTPException) as exc:
        await publish_sg_threshold_update(
            SGThresholdUpdateRequest(action="refresh"),
            db=db,
            current_user=_current_user(),
        )
    assert exc.value.status_code == 503
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_audit_failure_surfaces_500(monkeypatch):
    db = _fake_db()
    db.commit = AsyncMock(side_effect=RuntimeError("db gone"))
    redis = _RedisOK()
    monkeypatch.setattr(alert_admin.RedisPool, "get_client", AsyncMock(return_value=redis))

    with pytest.raises(HTTPException) as exc:
        await publish_sg_threshold_update(
            SGThresholdUpdateRequest(action="delete", organ="liver"),
            db=db,
            current_user=_current_user(),
        )
    assert exc.value.status_code == 500
    assert "审计写入失败" in str(exc.value.detail)


def test_operation_is_in_critical_registry():
    from app.observability.audit_chain import is_critical_operation
    from app.config.settings import settings

    assert "threshold_adjustment" in (settings.obs_audit_critical_operations or [])
    assert is_critical_operation("threshold_adjustment") is True
