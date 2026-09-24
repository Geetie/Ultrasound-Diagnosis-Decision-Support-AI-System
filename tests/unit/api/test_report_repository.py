"""T-GB-API-02 报告仓储测试（真实仓储逻辑，非替身）。

此前只有服务层用替身测试，仓储本身（报告编号生成、状态机流转、
PACS 结果累计）没有直接覆盖。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.models.database import DiagnosisReport
from app.repositories.report_repository import ReportRepository


class _FakeSession:
    """最小 AsyncSession 替身：支持 add / flush / execute(select)。"""

    def __init__(self, rows: list[DiagnosisReport] | None = None) -> None:
        self.rows: list[DiagnosisReport] = list(rows or [])
        self.flushed = 0

    def add(self, obj: Any) -> None:
        if getattr(obj, "id", None) is None:
            obj.id = len(self.rows) + 1
        self.rows.append(obj)

    async def flush(self) -> None:
        self.flushed += 1

    async def execute(self, stmt):
        result = MagicMock()
        # 仓储只用 scalar_one_or_none 与 scalars().all()
        result.scalar_one_or_none.return_value = self.rows[-1] if self.rows else None
        scalars = MagicMock()
        scalars.all.return_value = list(self.rows)
        result.scalars.return_value = scalars
        return result


def _draft(**overrides) -> DiagnosisReport:
    report = DiagnosisReport(
        report_id="rep_20260924_abcd1234",
        request_id="req_1",
        session_id="sess_1",
        patient_id="P001",
        organ="thyroid",
        status="draft",
        content={"k": "v"},
        rendered="报告正文",
        content_digest="digest",
        template_version="report-template-v1",
        pacs_status="pending",
        pacs_attempts=0,
    )
    for key, value in overrides.items():
        setattr(report, key, value)
    return report


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_report_persists_draft(self):
        session = _FakeSession()
        repo = ReportRepository(session)

        report = await repo.create_report(
            request_id="req_1",
            content={"organ": "thyroid"},
            rendered="正文",
            content_digest="digest",
            template_version="report-template-v1",
            session_id="sess_1",
            patient_id="P001",
            organ="thyroid",
        )

        assert report.status == "draft"
        assert report.pacs_status == "pending"
        assert report.pacs_attempts == 0
        assert session.flushed == 1
        assert session.rows == [report]

    @pytest.mark.asyncio
    async def test_generated_report_id_is_unique_and_prefixed(self):
        session = _FakeSession()
        repo = ReportRepository(session)

        first = await repo.create_report(
            request_id="r1",
            content={},
            rendered="a",
            content_digest="d",
            template_version="t",
        )
        second = await repo.create_report(
            request_id="r2",
            content={},
            rendered="b",
            content_digest="d",
            template_version="t",
        )

        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        for report in (first, second):
            assert report.report_id.startswith(f"rep_{today}_")
            assert len(report.report_id) == len("rep_20260924_") + 8
        assert first.report_id != second.report_id

    @pytest.mark.asyncio
    async def test_explicit_report_id_respected(self):
        session = _FakeSession()
        repo = ReportRepository(session)
        report = await repo.create_report(
            request_id="r1",
            content={},
            rendered="a",
            content_digest="d",
            template_version="t",
            report_id="rep_custom",
        )
        assert report.report_id == "rep_custom"


class TestReads:
    @pytest.mark.asyncio
    async def test_get_report_returns_none_when_empty(self):
        repo = ReportRepository(_FakeSession())
        assert await repo.get_report("rep_missing") is None

    @pytest.mark.asyncio
    async def test_get_report_and_latest_and_list(self):
        draft = _draft()
        session = _FakeSession([draft])
        repo = ReportRepository(session)

        assert await repo.get_report("rep_20260924_abcd1234") is draft
        assert await repo.get_latest_by_request("req_1") is draft
        assert await repo.list_by_request("req_1") == [draft]

    @pytest.mark.asyncio
    async def test_get_latest_by_request_empty(self):
        repo = ReportRepository(_FakeSession())
        assert await repo.get_latest_by_request("req_missing") is None


class TestSigningFlow:
    @pytest.mark.asyncio
    async def test_mark_signed_sets_state(self):
        draft = _draft()
        session = _FakeSession([draft])
        repo = ReportRepository(session)

        updated = await repo.mark_signed(
            draft.report_id,
            signature="c2ln",
            signing_key_name="report_signing_v1",
            signed_by="doc_1",
        )

        assert updated is draft
        assert draft.status == "signed"
        assert draft.signature == "c2ln"
        assert draft.signing_key_name == "report_signing_v1"
        assert draft.signed_by == "doc_1"
        assert draft.signed_at is not None

    @pytest.mark.asyncio
    async def test_mark_signed_missing_report_returns_none(self):
        repo = ReportRepository(_FakeSession())
        assert (
            await repo.mark_signed(
                "rep_missing", signature="x", signing_key_name="k"
            )
            is None
        )


class TestPacsResult:
    @pytest.mark.asyncio
    async def test_success_sets_pushed_and_attempts(self):
        draft = _draft(status="signed", signature="sig")
        session = _FakeSession([draft])
        repo = ReportRepository(session)

        await repo.mark_pacs_result(
            draft.report_id, adapter="local_fake", success=True, attempts=1
        )

        assert draft.status == "pushed"
        assert draft.pacs_status == "success"
        assert draft.pacs_attempts == 1
        assert draft.pushed_at is not None

    @pytest.mark.asyncio
    async def test_failure_keeps_signed_and_accumulates_attempts(self):
        draft = _draft(status="signed", signature="sig")
        session = _FakeSession([draft])
        repo = ReportRepository(session)

        await repo.mark_pacs_result(
            draft.report_id, adapter="http", success=False, message="HTTP 500", attempts=3
        )
        await repo.mark_pacs_result(
            draft.report_id, adapter="http", success=False, message="HTTP 500", attempts=2
        )

        # 推送失败不改变签名状态，只累计尝试与消息（可重推）
        assert draft.status == "signed"
        assert draft.pacs_status == "failed"
        assert draft.pacs_attempts == 5
        assert draft.pacs_message == "HTTP 500"

    @pytest.mark.asyncio
    async def test_skipped_marks_status_without_pushed_at(self):
        draft = _draft(status="draft")
        repo = ReportRepository(_FakeSession([draft]))

        await repo.mark_pacs_result(
            draft.report_id,
            adapter="local_fake",
            success=False,
            message="report is not signed",
            attempts=0,
            skipped=True,
        )

        assert draft.pacs_status == "skipped"
        assert draft.pushed_at is None
        # attempts=0 也至少记 1，便于区分"从未尝试"
        assert draft.pacs_attempts == 1

    @pytest.mark.asyncio
    async def test_long_message_is_truncated(self):
        draft = _draft(status="signed")
        repo = ReportRepository(_FakeSession([draft]))

        await repo.mark_pacs_result(
            draft.report_id, adapter="http", success=False, message="x" * 5000
        )

        assert draft.pacs_message is not None
        assert len(draft.pacs_message) == 2000

    @pytest.mark.asyncio
    async def test_mark_pacs_result_missing_report_returns_none(self):
        repo = ReportRepository(_FakeSession())
        assert (
            await repo.mark_pacs_result("rep_missing", adapter="a", success=True) is None
        )


class TestFailedState:
    @pytest.mark.asyncio
    async def test_mark_failed(self):
        draft = _draft()
        repo = ReportRepository(_FakeSession([draft]))

        updated = await repo.mark_failed(draft.report_id, "signing failed")

        assert updated is draft
        assert draft.status == "failed"
        assert draft.pacs_message == "signing failed"

    @pytest.mark.asyncio
    async def test_mark_failed_missing_report(self):
        repo = ReportRepository(_FakeSession())
        assert await repo.mark_failed("rep_missing", "x") is None
