"""A.49 摸排修复：撤回消费链的 cancelled 终态必须可读（结果读路径）。

管线（diagnosis_pipeline.py CancelledError 处理）asyncio.shield 写回
status=cancelled，供医生/审计观测"请求因患者撤回同意被取消"。修复前读路径
先过同意门 —— 撤回患者的每次结果查询都抛 E1010，终态不可观测
（真机探针实测：API 无 status 字段，DB status=cancelled）。

修复：cancelled 在同意门之前提前返回，且**不携带任何临床内容**（findings 占位）。
本测试直接以假 repo 调端点函数，钉住行为与顺序两个不变量。
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import Response

from app.api.v1.diagnosis import get_diagnosis_result
from app.api.v1.errors import PatientConsentWithdrawnException

pytestmark = pytest.mark.track_api


def _fake_cancelled_row() -> Mock:
    row = Mock()
    row.request_id = "req_cancelled_1"
    row.status = "cancelled"
    row.session_id = "sess_x"
    row.organ = "thyroid"
    row.findings = None
    row.created_at = datetime.now(timezone.utc)
    row.confidence = None
    row.knowledge_ids = None
    row.result_pushed = False
    row.patient_context = None
    return row


def _stub_redis_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResult:
        def __init__(self) -> None:
            self.error = None
            self.value = None

        def decode_value(self) -> str:  # pragma: no cover - 不应被调用
            raise AssertionError("cancelled 读路径不应命中缓存")

    class _FakePool:
        @staticmethod
        async def get_with_error(_key: str) -> _FakeResult:
            return _FakeResult()

    monkeypatch.setattr("app.api.v1.diagnosis.RedisPool", _FakePool)


@pytest.mark.asyncio
async def test_cancelled_result_readable_despite_withdrawn_consent(
    monkeypatch: pytest.MonkeyPatch,
):
    """撤回患者查 cancelled 结果：必须 200 带 status=cancelled，不得抛 E1010。"""
    _stub_redis_pool(monkeypatch)
    repo = Mock()
    repo.get_result = AsyncMock(return_value=_fake_cancelled_row())

    # 同意门一旦被触达就会抛 —— cancelled 分支必须先于它返回
    session_repo = Mock()
    session_repo.get_session = AsyncMock(side_effect=AssertionError("cancelled 不应触达会话查询"))
    consent_repo = Mock()
    consent_repo.get_latest_consent = AsyncMock(
        side_effect=AssertionError("cancelled 不应触达同意门")
    )

    result = await get_diagnosis_result(
        request_id="req_cancelled_1",
        response=Response(),
        db=Mock(),
        diagnosis_repo=repo,
        consent_repo=consent_repo,
        session_repo=session_repo,
        current_user={"user_id": "u1"},
    )

    assert result.status == "cancelled"
    assert result.consent_status_at_output.value == "withdrawn"
    assert result.review_reason == "consent_withdrawn_cancelled"
    assert result.requires_manual_review is True
    assert result.diagnosis.get("pending") is True
    # 不携带临床内容：findings 是占位文本，不是任何真实结论
    assert result.diagnosis.get("findings") == (
        "Diagnosis cancelled: patient consent withdrawn"
    )


@pytest.mark.asyncio
async def test_non_cancelled_still_gated_by_consent(monkeypatch: pytest.MonkeyPatch):
    """非 cancelled 结果不受影响：撤回患者查 processing/completed 仍抛 E1010。"""
    _stub_redis_pool(monkeypatch)
    row = _fake_cancelled_row()
    row.status = "completed"
    repo = Mock()
    repo.get_result = AsyncMock(return_value=row)

    session_repo = Mock()
    session_repo.get_session = AsyncMock(
        return_value=Mock(patient_id="pat_withdrawn")
    )
    consent_repo = Mock()
    consent_repo.get_latest_consent = AsyncMock(
        return_value=Mock(status="withdrawn")
    )

    with pytest.raises(PatientConsentWithdrawnException):
        await get_diagnosis_result(
            request_id="req_cancelled_1",
            response=Response(),
            db=Mock(),
            diagnosis_repo=repo,
            consent_repo=consent_repo,
            session_repo=session_repo,
            current_user={"user_id": "u1"},
        )


class TestCancelledBranchOrdering:
    def test_cancelled_branch_precedes_consent_gate(self):
        """源码顺序守卫：cancelled 提前返回必须在 check_consent_status 之前。"""
        import inspect
        from pathlib import Path

        src = Path(inspect.getfile(get_diagnosis_result)).read_text(encoding="utf-8")
        gate = src.find("consent_status = check_consent_status(consent_record, patient_id)")
        branch = src.find('if result.status == "cancelled":')
        assert branch != -1, "cancelled 提前返回分支缺失（回归）"
        assert gate != -1
        assert branch < gate, "cancelled 分支必须位于同意门之前（否则终态不可观测）"
