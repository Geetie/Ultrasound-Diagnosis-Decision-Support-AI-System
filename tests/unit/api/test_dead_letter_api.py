"""D-04 API 层：死信队列的读/重放端点（权限沿用 view_qc_report + 审计留痕）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.api.v1 import obs_dashboard
from app.security.auth import get_current_active_user

pytestmark = pytest.mark.track_api


def _app():
    """只挂目标路由的裸 app。

    `obs_dashboard.router` 带路由级 `dependencies=[Depends(PermissionChecker(...))]`，
    而 `PermissionChecker` 是**实例**（不是可覆盖的函数键）。测试关注端点逻辑而非
    鉴权（鉴权有独立用例覆盖），故直接复用真实的子路由对象、把 its 路由级依赖清空。
    """
    app = FastAPI()
    router = APIRouter()
    for route in obs_dashboard.router.routes:
        router.add_api_route(
            route.path,
            route.endpoint,
            methods=list(route.methods),
            dependencies=[],
        )
    app.include_router(router, prefix="/obs")
    app.dependency_overrides[get_current_active_user] = lambda: {"username": "qc-admin"}
    return app


def test_list_dead_letters_returns_depth_and_items():
    with patch(
        "app.security.dead_letter.list_dead_letters",
        AsyncMock(return_value=[{"entry_id": "1-1", "event_id": "e1"}]),
    ), patch("app.security.dead_letter.dead_letter_depth", AsyncMock(return_value=1)):
        r = TestClient(_app()).get("/obs/dead-letters?limit=5")
    assert r.status_code == 200
    body = r.json()
    assert body["depth"] == 1
    assert body["items"][0]["event_id"] == "e1"


def test_replay_failure_is_400_not_silent():
    with patch(
        "app.security.dead_letter.replay_dead_letter",
        AsyncMock(return_value={"ok": False, "reason": "not_found", "new_entry_id": None}),
    ):
        r = TestClient(_app()).post(
            "/obs/dead-letters/replay", json={"entry_id": "9-9", "reason": "巡检补投"}
        )
    assert r.status_code == 400
    assert r.json()["detail"] == "not_found"


def test_replay_success_writes_audit():
    audit = AsyncMock()
    audit.log_action = AsyncMock()
    app = _app()
    app.dependency_overrides[obs_dashboard.get_audit_service] = lambda: audit
    with patch(
        "app.security.dead_letter.replay_dead_letter",
        AsyncMock(return_value={"ok": True, "reason": "replayed", "new_entry_id": "2-0"}),
    ):
        r = TestClient(app).post(
            "/obs/dead-letters/replay", json={"entry_id": "1-1", "reason": "巡检补投"}
        )
    assert r.status_code == 200
    assert audit.log_action.await_count == 1
    kwargs = audit.log_action.await_args.kwargs
    assert kwargs["action"] == "consent_withdrawal_dead_letter_replayed"
    assert kwargs["target_id"] == "1-1"


# ---------------------------------------------------------------------------
# G8：知识缓存主动失效端点
# ---------------------------------------------------------------------------


def test_kb_cache_invalidate_writes_stamp_and_audits():
    from unittest.mock import AsyncMock, patch

    audit = AsyncMock()
    audit.log_action = AsyncMock()
    app = _app()
    app.dependency_overrides[obs_dashboard.get_audit_service] = lambda: audit

    client_mock = AsyncMock()
    client_mock.set = AsyncMock()
    pool_mock = AsyncMock()
    pool_mock.get_client = AsyncMock(return_value=client_mock)

    with patch("app.config.redis.RedisPool.get_client", pool_mock.get_client):
        r = TestClient(app).post(
            "/obs/kb-cache/invalidate", json={"reason": "reindex kb_v1.4.0"}
        )

    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert "reindex kb_v1.4.0" in body["stamp"]
    assert client_mock.set.await_count == 1
    assert audit.log_action.await_count == 1
    assert audit.log_action.await_args.kwargs["action"] == "kb_cache_invalidated"


def test_kb_cache_invalidate_returns_503_when_redis_down():
    from unittest.mock import AsyncMock, patch

    pool_mock = AsyncMock()
    pool_mock.get_client = AsyncMock(side_effect=RuntimeError("redis down"))
    app = _app()
    app.dependency_overrides[obs_dashboard.get_audit_service] = lambda: AsyncMock()

    with patch("app.config.redis.RedisPool.get_client", pool_mock.get_client):
        r = TestClient(app).post("/obs/kb-cache/invalidate", json={"reason": "x"})

    assert r.status_code == 503
    assert "缓存服务" in r.json()["detail"]
