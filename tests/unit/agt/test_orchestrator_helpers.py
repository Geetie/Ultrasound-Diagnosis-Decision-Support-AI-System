"""multi_organ_orchestrator 辅助函数直测（不 patch 内部 _record_latency/_push）。"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = [pytest.mark.track_agt]

import app.services.multi_organ_orchestrator as mod
from app.models.schemas import Organ, OrganStatus, ResponseTier
from app.services.multi_organ_orchestrator import (
    MultiOrganOutcome,
    OrganRunResult,
    _commit_if_possible,
    _default_response_tier,
    _mark_organ_timeout,
    _persist_progress,
    _push_organ_switching,
    _read_back_status,
    _record_latency,
    _resolve_organs,
    _resolve_request_id,
)


def _repo(update_error=None, row=None, rows=None):
    repo = MagicMock()
    repo.update_result = AsyncMock(side_effect=update_error)
    repo.get_result = AsyncMock(return_value=row)
    repo.get_results_by_session = AsyncMock(return_value=rows or [])
    repo.create_request = AsyncMock(return_value="req-new")
    repo.db = None
    return repo


@pytest.mark.anyio
class TestReadBackStatus:
    async def test_repo_error_returns_fallback(self):
        repo = _repo()
        repo.get_result = AsyncMock(side_effect=RuntimeError("db"))
        status, conf = await _read_back_status(
            diagnosis_repo=repo, db=MagicMock(), request_id="r1",
            fallback_ok=True, reason=None,
        )
        assert status == OrganStatus.COMPLETED.value and conf == 0.0

    async def test_row_none_with_fallback_false(self):
        repo = _repo(row=None)
        status, _ = await _read_back_status(
            diagnosis_repo=repo, db=MagicMock(), request_id="r1",
            fallback_ok=False, reason=None,
        )
        assert status == OrganStatus.FAILED.value

    async def test_timeout_reason_maps_failed(self):
        repo = _repo(row=SimpleNamespace(status="completed", confidence=0.9))
        status, conf = await _read_back_status(
            diagnosis_repo=repo, db=MagicMock(), request_id="r1",
            fallback_ok=True, reason=mod.REASON_PER_ORGAN_TIMEOUT,
        )
        assert status == OrganStatus.FAILED.value and conf == 0.9

    async def test_status_mappings(self):
        for raw, expected in (
            ("completed", OrganStatus.COMPLETED.value),
            ("needs_review", OrganStatus.NEEDS_REVIEW.value),
            ("failed", OrganStatus.FAILED.value),
        ):
            repo = _repo(row=SimpleNamespace(status=raw, confidence=0.5))
            status, _ = await _read_back_status(
                diagnosis_repo=repo, db=MagicMock(), request_id="r1",
                fallback_ok=True, reason=None,
            )
            assert status == expected, raw

    async def test_unknown_status_fallback(self):
        for ok, expected in ((True, OrganStatus.COMPLETED.value), (False, OrganStatus.FAILED.value)):
            repo = _repo(row=SimpleNamespace(status="processing", confidence=0.3))
            status, _ = await _read_back_status(
                diagnosis_repo=repo, db=MagicMock(), request_id="r1",
                fallback_ok=ok, reason=None,
            )
            assert status == expected


@pytest.mark.anyio
class TestTimeoutAndProgress:
    async def test_mark_timeout_update_failure_swallowed(self):
        repo = _repo(update_error=RuntimeError("db"))
        await _mark_organ_timeout(repo, "r1")  # 不应抛

    async def test_persist_progress_update_failure_swallowed(self):
        session_repo = MagicMock()
        session_repo.update_session = AsyncMock(side_effect=RuntimeError("db"))
        await _persist_progress(session_repo, "s1", {"thyroid": "completed"})

    async def test_persist_progress_ok(self):
        session_repo = MagicMock()
        session_repo.update_session = AsyncMock()
        session_repo.db = None
        await _persist_progress(session_repo, "s1", {"thyroid": "completed"})


@pytest.mark.anyio
class TestPushAndLatency:
    async def test_push_disabled_early_return(self, monkeypatch):
        monkeypatch.setattr(mod.settings, "multi_organ_progress_push_enabled", False)
        await _push_organ_switching("s1", "thyroid", switching=True)  # 早退

    async def test_push_enabled_ok_and_failure(self, monkeypatch):
        monkeypatch.setattr(mod.settings, "multi_organ_progress_push_enabled", True)
        sent = []

        async def _send(payload, sid):
            sent.append(sid)

        async def _boom(payload, sid):
            raise ConnectionError("ws down")

        with patch(
            "app.api.websocket.nav.manager.send_personal_message", _send
        ):
            await _push_organ_switching("s1", "thyroid", switching=True)
        assert sent == ["s1"]

        with patch(
            "app.api.websocket.nav.manager.send_personal_message", _boom
        ):
            await _push_organ_switching("s1", "thyroid", switching=False)  # 吞异常

    def test_record_latency_clean_and_retry_buckets(self):
        clean = MultiOrganOutcome(
            session_id="s",
            session_status="completed",
            organ_results=[OrganRunResult(organ="thyroid", status="completed")],
            total_ms=5000.0,
        )
        _record_latency(clean)  # 不应抛

        dirty = MultiOrganOutcome(
            session_id="s",
            session_status="partial",
            organ_results=[
                OrganRunResult(organ="liver", status="failed", reason="pipeline_error")
            ],
            total_ms=30000.0,
        )
        _record_latency(dirty)

    def test_record_latency_collector_error_swallowed(self, monkeypatch):
        outcome = MultiOrganOutcome(session_id="s", session_status="failed", total_ms=1.0)
        monkeypatch.setattr(
            "app.observability.metrics_collector.get_metrics_collector",
            MagicMock(side_effect=RuntimeError("no collector")),
            raising=False,
        )
        _record_latency(outcome)  # debug 吞掉


@pytest.mark.anyio
class TestResolveAndCommit:
    async def test_resolve_request_id_reuses_rerunnable(self):
        row = SimpleNamespace(organ="thyroid", status="failed", request_id="req-old")
        repo = _repo()
        got = await _resolve_request_id(
            diagnosis_repo=repo, existing=[row], session_id="s1",
            organ=Organ.THYROID, response_tier=ResponseTier.STANDARD,
        )
        assert got == "req-old"
        repo.create_request.assert_not_awaited()

    async def test_resolve_request_id_creates_new(self):
        repo = _repo()
        got = await _resolve_request_id(
            diagnosis_repo=repo, existing=[], session_id="s1",
            organ=Organ.THYROID, response_tier=ResponseTier.STANDARD,
        )
        assert got == "req-new"

    async def test_resolve_request_id_create_failure(self):
        repo = _repo()
        repo.create_request = AsyncMock(side_effect=RuntimeError("db"))
        got = await _resolve_request_id(
            diagnosis_repo=repo, existing=[], session_id="s1",
            organ=Organ.THYROID, response_tier=ResponseTier.STANDARD,
        )
        assert got is None

    async def test_resolve_organs_dedup_and_order(self):
        assert _resolve_organs(["cardiac", "thyroid", "thyroid"], None) == [
            Organ.THYROID, Organ.CARDIAC,
        ]
        assert _resolve_organs(["brain"], None) == []

    def test_default_response_tier_invalid_falls_back(self, monkeypatch):
        monkeypatch.setattr(mod.settings, "multi_organ_default_response_tier", "bogus")
        assert _default_response_tier() == ResponseTier.STANDARD

    async def test_commit_if_possible_variants(self):
        await _commit_if_possible(SimpleNamespace())  # 无 commit → 早退

        class BoomCommit:
            def commit(self):
                raise RuntimeError("commit boom")

        await _commit_if_possible(BoomCommit())  # 吞异常

        db = MagicMock()
        db.commit.return_value = None
        await _commit_if_possible(db)
        db.commit.assert_called_once()
