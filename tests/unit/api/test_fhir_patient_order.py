"""A.52 端到端摸排守卫：FHIR 患者读取的**判定顺序**（先存在性 → 再同意）。

背景（真机走查实录）：`GET /fhir/Patient/{id}` 原先先查同意，导致**不存在的患者**
也抛 403「未签署知情同意」——医生被误导去质控补签，而前端 A.33 设计的
「404 → 就地建档」分支永远走不到。本测试钉住两条语义**可区分**：

    不存在的患者        → 404 NotFoundException（E1002，前端据此引导建档）
    存在但未同意/已撤回 → 403 PatientNotConsented / ConsentWithdrawn（引导质控）

纯单测（fake DB），不依赖真机；真机复验见 16 号 A.52。
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.errors import NotFoundException, PatientNotConsentedException
from app.api.v1.fhir import _load_patient_with_consent, get_patient
from app.models.schemas import ConsentStatus


def _db(patient_row, consent_status: str | None):
    """构造最小 fake AsyncSession：execute() 返回患者行，同意仓储读 patch 掉。"""
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=patient_row)
    db = MagicMock()
    db.execute = AsyncMock(return_value=result)
    return db


def _patch_consent(monkeypatch, status: str | None):
    """把 ConsentRepository.get_latest_consent 换成固定返回值。"""
    record = None if status is None else MagicMock(status=status)
    monkeypatch.setattr(
        "app.repositories.consent_repository.ConsentRepository.get_latest_consent",
        AsyncMock(return_value=record),
        raising=True,
    )


@pytest.mark.asyncio
async def test_get_patient_missing_returns_404_not_403(monkeypatch):
    """不存在 → 404（前端可据此引导建档），不得被同意门误判成 403。"""
    _patch_consent(monkeypatch, None)  # 无同意记录（不存在的患者必然如此）
    with pytest.raises(NotFoundException) as exc:
        await get_patient(id="no-such-patient", db=_db(None, None), current_user={"username": "t"})
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_patient_exists_without_consent_returns_403(monkeypatch):
    """存在但无同意 → 403（引导质控），与 404 严格区分。"""
    _patch_consent(monkeypatch, None)
    with pytest.raises(PatientNotConsentedException) as exc:
        await get_patient(
            id="p-consent-missing",
            db=_db(MagicMock(patient_id="p-consent-missing"), None),
            current_user={"username": "t"},
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_get_patient_exists_with_granted_consent_ok(monkeypatch):
    """存在且同意有效 → 正常返回（不抛）。"""
    _patch_consent(monkeypatch, ConsentStatus.GRANTED.value)
    patient = MagicMock(patient_id="p-ok", name="P OK")
    out = await get_patient(id="p-ok", db=_db(patient, ConsentStatus.GRANTED.value),
                            current_user={"username": "t"})
    assert out is not None


@pytest.mark.asyncio
async def test_write_path_same_order(monkeypatch):
    """写路径（PUT/DELETE 走的 _load_patient_with_consent）同口径：不存在 → 404。"""
    _patch_consent(monkeypatch, None)
    with pytest.raises(NotFoundException):
        await _load_patient_with_consent(_db(None, None), "no-such-patient")
