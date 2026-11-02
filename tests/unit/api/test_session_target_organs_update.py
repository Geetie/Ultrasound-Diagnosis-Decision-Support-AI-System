"""A.120 / G27：建会话后必须能追加/替换目标部位。

缺口：`SessionUpdateRequest` 只有 `status` / `organ_statuses`，会话建好后没有任何入口
改 `target_organs` —— 医生要加一个部位只能重建会话（丢掉已上传影像与已填临床信息）。

口径：
1. `None` = 不改；非空列表 = **整组替换**；`[]` 被 schema 拒绝（想清空请重建会话）；
2. 替换时同步 `organ_statuses`：新部位补 PENDING、已有部位状态保留（否则状态表错位、
   界面显示空白）；
3. 会话审计里必须留下这次改了哪些部位。
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.api.v1.session import update_session
from app.models.schemas import Organ, SessionStatus, SessionUpdateRequest


class _SessionRepo:
    def __init__(self, session=None):
        self.calls: list[dict] = []
        self._session = session

    async def update_session(self, **kwargs):
        self.calls.append(kwargs)
        return self._session


def _fake_session():
    # 字段集与 `_build_session_response` 一致（端点会在返回时重建响应）
    return SimpleNamespace(
        session_id="s1",
        patient_id="p1",
        device_id="dev1",
        status="active",
        target_organs=["thyroid"],
        organ_statuses={"thyroid": "completed"},
        knowledge_base_version="kb_v1.0.0",
        consent_status_at_creation="granted",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


def test_schema_rejects_empty_list():
    with pytest.raises(ValidationError):
        SessionUpdateRequest(target_organs=[])


def test_schema_none_keeps_organs():
    req = SessionUpdateRequest()
    assert req.target_organs is None


@pytest.mark.asyncio
async def test_endpoint_passes_organs_and_audits():
    repo = _SessionRepo(_fake_session())
    audit = MagicMock()
    audit.log_action = AsyncMock()

    await update_session(
        session_id="s1",
        request=SessionUpdateRequest(target_organs=[Organ.THYROID, Organ.LIVER]),
        session_repo=repo,
        audit_service=audit,
        current_user={"user_id": "u1"},
    )

    assert repo.calls[0]["target_organs"] == ["thyroid", "liver"]
    detail = audit.log_action.await_args.kwargs["detail"]
    assert detail["target_organs"] == ["thyroid", "liver"]


@pytest.mark.asyncio
async def test_endpoint_without_organs_passes_none():
    repo = _SessionRepo(_fake_session())
    audit = MagicMock()
    audit.log_action = AsyncMock()

    await update_session(
        session_id="s1",
        request=SessionUpdateRequest(status=SessionStatus.COMPLETED),
        session_repo=repo,
        audit_service=audit,
        current_user={"user_id": "u1"},
    )
    assert repo.calls[0]["target_organs"] is None


@pytest.mark.asyncio
async def test_repository_replacement_fills_missing_statuses():
    """替换部位时：新部位补 PENDING、旧部位状态保留。"""
    from app.repositories.session_repository import SessionRepository

    repo = SessionRepository.__new__(SessionRepository)
    session = _fake_session()
    repo.get_session = AsyncMock(return_value=session)
    repo.db = AsyncMock()
    repo.db.flush = AsyncMock()

    await repo.update_session(session_id="s1", target_organs=["thyroid", "liver"])

    assert session.target_organs == ["thyroid", "liver"]
    assert session.organ_statuses == {"thyroid": "completed", "liver": "pending"}


@pytest.mark.asyncio
async def test_repository_no_organs_keeps_statuses():
    from app.repositories.session_repository import SessionRepository

    repo = SessionRepository.__new__(SessionRepository)
    session = _fake_session()
    repo.get_session = AsyncMock(return_value=session)
    repo.db = AsyncMock()
    repo.db.flush = AsyncMock()

    await repo.update_session(session_id="s1", status=SessionStatus.COMPLETED)
    assert session.target_organs == ["thyroid"]
    assert session.organ_statuses == {"thyroid": "completed"}


def test_frontend_service_declares_target_organs():
    """前端 service 类型也要带 target_organs，否则 TS 层无法传（能力只到后端）。"""
    from pathlib import Path

    src = Path(__file__).resolve().parents[3] / "frontend/src/services/sessionService.ts"
    text = src.read_text(encoding="utf-8")
    assert "target_organs?: string[]" in text
