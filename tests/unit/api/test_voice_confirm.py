"""A.49 摸排修复：语音触发判读（POST /api/v1/voice/confirm）三处断裂。

修复前（真机 100% 复现 500）：
1. create_request 传了不存在的 ``patient_id`` 形参 → TypeError（签名漂移）；
2. 语音路径**完全绕开患者同意门**（主端点 diagnosis.py 有 check_consent_status）；
3. create_request 只 flush 不 commit、也不入队 → worker 读不到行，
   请求永久 processing（diagnosis.py L229-240 已有同款教训注释）。

本测试直接以假依赖调端点函数，钉住：签名、同意门、commit+入队三件事。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.api.v1.errors import PatientConsentWithdrawnException
from app.api.v1.voice import voice_confirm
from app.models.schemas import VoiceConfirmRequest

pytestmark = pytest.mark.track_api


def _request(**kw) -> VoiceConfirmRequest:
    base = dict(
        session_id="sess_v",
        transcript="开始甲状腺判读",
        command_type="diagnosis_trigger",
        confirmed=True,
        timestamp=1_700_000_000_000,
        organ="thyroid",
    )
    base.update(kw)
    return VoiceConfirmRequest(**base)


def _deps(consent_status: str = "granted"):
    session_repo = Mock()
    session_repo.get_session = AsyncMock(return_value=Mock(patient_id="pat_v"))
    diag_repo = Mock()
    diag_repo.create_request = AsyncMock(return_value="req_voice_1")
    diag_repo.db = Mock()
    diag_repo.db.commit = AsyncMock()
    consent_repo = Mock()
    consent_repo.get_latest_consent = AsyncMock(return_value=Mock(status=consent_status))
    audit_service = Mock()
    audit_service.log_action = AsyncMock()
    return session_repo, diag_repo, consent_repo, audit_service


@pytest.mark.asyncio
async def test_diagnosis_trigger_creates_enqueues_and_returns_request_id(
    monkeypatch: pytest.MonkeyPatch,
):
    enqueued: list[str] = []

    async def _fake_enqueue(request_id: str) -> bool:
        enqueued.append(request_id)
        return True

    monkeypatch.setattr("app.api.v1.voice.enqueue_diagnosis", _fake_enqueue)
    session_repo, diag_repo, consent_repo, audit = _deps()

    resp = await voice_confirm(
        request=_request(),
        session_repo=session_repo,
        diag_repo=diag_repo,
        consent_repo=consent_repo,
        audit_service=audit,
        current_user={"user_id": "doc1"},
    )

    # ① 签名对齐：不再传 patient_id；organ/session_id 正确
    kwargs = diag_repo.create_request.await_args.kwargs
    assert kwargs["session_id"] == "sess_v"
    assert kwargs["organ"] == "thyroid"
    assert "patient_id" not in kwargs, "create_request 没有 patient_id 形参（签名漂移回归）"
    # ② commit + 入队，且入队 id 与库里创建的一致
    diag_repo.db.commit.assert_awaited_once()
    assert enqueued == ["req_voice_1"]
    # ③ 响应 executed + request_id 透出
    assert resp.executed is True
    assert resp.request_id == "req_voice_1"
    assert resp.action_taken == "diagnosis_initiated"


@pytest.mark.asyncio
async def test_diagnosis_trigger_blocked_when_consent_withdrawn(
    monkeypatch: pytest.MonkeyPatch,
):
    """撤回患者语音触发判读：必须拒绝且不落库、不入队。"""
    monkeypatch.setattr(
        "app.api.v1.voice.enqueue_diagnosis", AsyncMock(side_effect=AssertionError("不应入队"))
    )
    session_repo, diag_repo, consent_repo, audit = _deps(consent_status="withdrawn")

    with pytest.raises(PatientConsentWithdrawnException):
        await voice_confirm(
            request=_request(),
            session_repo=session_repo,
            diag_repo=diag_repo,
            consent_repo=consent_repo,
            audit_service=audit,
            current_user={"user_id": "doc1"},
        )

    diag_repo.create_request.assert_not_awaited()
    diag_repo.db.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_rejected_confirmation_is_audit_only(monkeypatch: pytest.MonkeyPatch):
    """confirmed=false：不落库不入队，返回 rejected_by_user。"""
    monkeypatch.setattr(
        "app.api.v1.voice.enqueue_diagnosis", AsyncMock(side_effect=AssertionError("不应入队"))
    )
    session_repo, diag_repo, consent_repo, audit = _deps()

    resp = await voice_confirm(
        request=_request(confirmed=False),
        session_repo=session_repo,
        diag_repo=diag_repo,
        consent_repo=consent_repo,
        audit_service=audit,
        current_user={"user_id": "doc1"},
    )

    assert resp.executed is False
    assert resp.action_taken == "rejected_by_user"
    diag_repo.create_request.assert_not_awaited()
