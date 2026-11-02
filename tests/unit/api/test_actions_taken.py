"""A.130 / G35：医生实际执行的处置必须落库（建议采纳对照）。

缺口：系统给结构化建议（含 rule_id），但没有任何字段记录"实际执行了哪几条" →
采纳率/偏离率无法计算（反馈表只有改判）。
"""

from __future__ import annotations

import importlib.util
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.v1.errors import BadRequestException
from app.api.v1.diagnosis import set_actions_taken
from app.models.schemas import ActionsTakenRequest

_REPO_ROOT = Path(__file__).resolve().parents[3]
pytestmark = pytest.mark.track_api


def _repo(result=None):
    repo = MagicMock()
    repo.get_result = AsyncMock(return_value=result)
    repo.mark_actions_taken = AsyncMock(return_value=result)
    return repo


def _result(**kw):
    base = dict(
        request_id="r1",
        session_id="s1",
        actions_taken_at=datetime.now(timezone.utc),
        actions_taken=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_schema_rejects_empty_and_dedupes_at_endpoint():
    with pytest.raises(Exception):
        ActionsTakenRequest(rule_ids=[])


@pytest.mark.asyncio
async def test_endpoint_writes_and_audits():
    repo = _repo(_result())
    audit = MagicMock()
    audit.log_action = AsyncMock()

    out = await set_actions_taken(
        request_id="r1",
        payload=ActionsTakenRequest(rule_ids=["thyroid:fna", "thyroid:fna", " thyroid:followup "], note="已执行"),
        diagnosis_repo=repo,
        audit_service=audit,
        current_user={"user_id": "doc1"},
    )

    kw = repo.mark_actions_taken.await_args.kwargs
    assert kw["rule_ids"] == ["thyroid:fna", "thyroid:followup"]
    assert kw["taken_by"] == "doc1"
    assert out.rule_ids == ["thyroid:fna", "thyroid:followup"]
    detail = audit.log_action.await_args.kwargs["detail"]
    assert detail["rule_ids"] == ["thyroid:fna", "thyroid:followup"]


@pytest.mark.asyncio
async def test_endpoint_404_when_missing():
    from app.api.v1.errors import NotFoundException

    repo = _repo(None)
    with pytest.raises(NotFoundException):
        await set_actions_taken(
            request_id="nope",
            payload=ActionsTakenRequest(rule_ids=["a:b"]),
            diagnosis_repo=repo,
            audit_service=MagicMock(),
            current_user=None,
        )


@pytest.mark.asyncio
async def test_endpoint_rejects_blank_only_ids():
    repo = _repo(_result())
    with pytest.raises(BadRequestException):
        await set_actions_taken(
            request_id="r1",
            payload=ActionsTakenRequest(rule_ids=["   "]),
            diagnosis_repo=repo,
            audit_service=MagicMock(),
            current_user=None,
        )


def test_repository_only_writes_three_fields():
    """repo 方法只写 actions_taken/at/by，不动判读产物。"""
    src = (_REPO_ROOT / "app/repositories/diagnosis_repository.py").read_text(encoding="utf-8")
    block = src[src.index("async def mark_actions_taken"):]
    block = block[: block.index("async def get_results_by_session")]
    # 只检查**赋值语句**（文档字符串里会出现"不动 doctor_confirmed"这类说明，不算写入）
    import re

    assignments = re.findall(r"\bresult\.([a-z_]+)\s*=", block)
    assert assignments, "没解析到任何写入"
    for forbidden in ("status", "confidence", "findings", "doctor_confirmed_at", "doctor_confirmed_by"):
        assert forbidden not in assignments, f"采纳回填不应改写 {forbidden}"


def test_migration_and_model_agree():
    """迁移 0020 必须存在且字段名与 ORM 一致（否则真机建表后读写会错位）。"""
    spec = importlib.util.spec_from_file_location(
        "m0020", _REPO_ROOT / "alembic/versions/0020_actions_taken.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    assert mod.revision == "0020" and mod.down_revision == "0019"
    src = (_REPO_ROOT / "alembic/versions/0020_actions_taken.py").read_text(encoding="utf-8")
    for col in ("actions_taken", "actions_taken_at", "actions_taken_by"):
        assert col in src
    model = (_REPO_ROOT / "app/models/database.py").read_text(encoding="utf-8")
    for col in ("actions_taken = Column", "actions_taken_at = Column", "actions_taken_by = Column"):
        assert col in model
