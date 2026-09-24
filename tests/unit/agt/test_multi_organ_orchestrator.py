"""T-GB-AGT-01 多器官串行编排单元测试。

覆盖：
- 器官列表归一化（规格顺序 / 去重 / 非法值丢弃）；
- 会话状态汇总（completed / needs_review / partial / failed）与 partial 标记；
- 三器官串行执行顺序；
- 单器官失败与超时不阻断其余器官（部分完成）；
- 全局超时预算耗尽 → 剩余器官标记 skipped；
- 会话不存在 → 抛 SessionNotFoundError；
- 进度回写与 organ_switching 推送。
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.schemas import Organ, OrganStatus, SessionStatus
from app.services import multi_organ_orchestrator as mod
from app.services.multi_organ_orchestrator import (
    ORGAN_ORDER,
    OrganRunResult,
    SessionNotFoundError,
    _aggregate_session_status,
    _is_partial,
    _resolve_organs,
    run_multi_organ_diagnosis,
)

# ----------------------------------------------------------------------
# 测试替身
# ----------------------------------------------------------------------


class _AsyncCtx:
    """最小异步上下文管理器（替代 ``async with AsyncSessionLocal()``）。"""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info):
        return False


@contextmanager
def _orchestrator_env(
    *,
    session_organs=("thyroid", "liver", "cardiac"),
    exists=True,
    behaviors=None,
    row_status=None,
):
    """构造编排器依赖的 mock 环境。

    Args:
        session_organs: 会话 ``target_organs``。
        exists: 会话是否存在。
        behaviors: ``organ -> "completed" | "needs_review" | "failed" | "timeout"``，
            控制在管线侧的行为。
        row_status: ``request_id -> 回读状态``（覆盖 behaviors 的推断）。
    """
    behaviors = dict(behaviors or {})
    row_status = dict(row_status or {})
    state = {
        "pipeline_calls": [],
        "session_updates": [],
        "switching": [],
        "created_requests": [],
    }

    db = MagicMock()
    db.expire_all = MagicMock()
    db.commit = AsyncMock()

    session_obj = MagicMock()
    session_obj.session_id = "sess_multi"
    session_obj.target_organs = list(session_organs)
    session_obj.organ_statuses = {o: "pending" for o in session_organs}

    session_repo = MagicMock()
    session_repo.db = db
    session_repo.get_session = AsyncMock(return_value=session_obj if exists else None)

    async def _update_session(session_id, status=None, organ_statuses=None):
        state["session_updates"].append(
            {
                "status": status.value if status else None,
                "organ_statuses": dict(organ_statuses or {}),
            }
        )
        return session_obj

    session_repo.update_session = AsyncMock(side_effect=_update_session)

    diagnosis_repo = MagicMock()
    diagnosis_repo.db = db
    diagnosis_repo.get_results_by_session = AsyncMock(return_value=[])
    diagnosis_repo.update_result = AsyncMock(return_value=MagicMock())

    async def _create_request(session_id, organ, response_tier, **kwargs):
        rid = f"req_{organ}"
        state["created_requests"].append(rid)
        return rid

    diagnosis_repo.create_request = AsyncMock(side_effect=_create_request)

    async def _get_result(request_id):
        organ = request_id.split("_", 1)[1]
        status = row_status.get(request_id, behaviors.get(organ, "completed"))
        return MagicMock(status=status, confidence=0.91)

    diagnosis_repo.get_result = AsyncMock(side_effect=_get_result)

    async def _pipeline(request_id):
        state["pipeline_calls"].append(request_id)
        organ = request_id.split("_", 1)[1]
        behavior = behaviors.get(organ, "completed")
        if behavior == "timeout":
            await asyncio.sleep(30)  # 由 wait_for 取消
        if isinstance(behavior, Exception):
            raise behavior
        return behavior != "failed"

    async def _push(session_id, organ, *, switching):
        state["switching"].append((organ, switching))

    with patch.object(
        mod, "AsyncSessionLocal", MagicMock(return_value=_AsyncCtx(db))
    ), patch.object(
        mod, "SessionRepository", MagicMock(return_value=session_repo)
    ), patch.object(
        mod, "DiagnosisRepository", MagicMock(return_value=diagnosis_repo)
    ), patch.object(
        mod, "_push_organ_switching", AsyncMock(side_effect=_push)
    ), patch.object(
        mod, "_record_latency"
    ), patch(
        "app.services.diagnosis_pipeline.run_diagnosis_pipeline",
        AsyncMock(side_effect=_pipeline),
    ):
        state["db"] = db
        state["session_repo"] = session_repo
        state["diagnosis_repo"] = diagnosis_repo
        yield state


# ----------------------------------------------------------------------
# 纯函数：器官归一化
# ----------------------------------------------------------------------


class TestResolveOrgans:
    def test_orders_by_spec_sequence(self):
        assert _resolve_organs(None, ["cardiac", "thyroid"]) == [Organ.THYROID, Organ.CARDIAC]

    def test_falls_back_to_session_organs(self):
        assert _resolve_organs(None, ["liver"]) == [Organ.LIVER]

    def test_explicit_organs_take_precedence(self):
        assert _resolve_organs(["liver"], ["thyroid", "cardiac"]) == [Organ.LIVER]

    def test_dedupes_preserving_spec_order(self):
        assert _resolve_organs(["liver", "liver", "thyroid"], None) == [
            Organ.THYROID,
            Organ.LIVER,
        ]

    def test_drops_unknown_organs(self):
        assert _resolve_organs(["spleen", "thyroid"], None) == [Organ.THYROID]

    def test_returns_empty_when_nothing_usable(self):
        assert _resolve_organs(None, None) == []
        assert _resolve_organs(["unknown"], None) == []

    def test_accepts_enum_members(self):
        assert _resolve_organs([Organ.LIVER], None) == [Organ.LIVER]

    def test_spec_order_constant_covers_three_organs(self):
        assert ORGAN_ORDER == (Organ.THYROID, Organ.LIVER, Organ.CARDIAC)


# ----------------------------------------------------------------------
# 纯函数：状态汇总
# ----------------------------------------------------------------------


def _result(organ: str, status: OrganStatus) -> OrganRunResult:
    return OrganRunResult(organ=organ, status=status.value)


class TestAggregateStatus:
    def test_all_completed(self):
        results = [_result("thyroid", OrganStatus.COMPLETED), _result("liver", OrganStatus.COMPLETED)]
        assert _aggregate_session_status(results) == SessionStatus.COMPLETED.value
        assert _is_partial(results) is False

    def test_mixed_completed_and_failed_is_partial(self):
        results = [
            _result("thyroid", OrganStatus.COMPLETED),
            _result("liver", OrganStatus.FAILED),
            _result("cardiac", OrganStatus.COMPLETED),
        ]
        assert _aggregate_session_status(results) == SessionStatus.PARTIAL.value
        assert _is_partial(results) is True

    def test_confirmed_needs_review_without_failure(self):
        results = [
            _result("thyroid", OrganStatus.COMPLETED),
            _result("liver", OrganStatus.NEEDS_REVIEW),
        ]
        # 三器官均产出结论（其一待复核）→ needs_review，且不算“部分完成”
        assert _aggregate_session_status(results) == SessionStatus.NEEDS_REVIEW.value
        assert _is_partial(results) is False

    def test_all_needs_review(self):
        results = [
            _result("thyroid", OrganStatus.NEEDS_REVIEW),
            _result("liver", OrganStatus.NEEDS_REVIEW),
        ]
        assert _aggregate_session_status(results) == SessionStatus.NEEDS_REVIEW.value
        assert _is_partial(results) is False

    def test_needs_review_plus_skipped_is_partial(self):
        results = [
            _result("thyroid", OrganStatus.NEEDS_REVIEW),
            _result("liver", OrganStatus.SKIPPED),
        ]
        assert _aggregate_session_status(results) == SessionStatus.PARTIAL.value
        assert _is_partial(results) is True

    def test_all_failed(self):
        results = [_result("thyroid", OrganStatus.FAILED), _result("liver", OrganStatus.FAILED)]
        assert _aggregate_session_status(results) == SessionStatus.FAILED.value
        assert _is_partial(results) is False

    def test_all_skipped(self):
        results = [_result("thyroid", OrganStatus.SKIPPED)]
        assert _aggregate_session_status(results) == SessionStatus.FAILED.value

    def test_completed_plus_skipped_is_partial(self):
        results = [
            _result("thyroid", OrganStatus.COMPLETED),
            _result("liver", OrganStatus.SKIPPED),
        ]
        assert _aggregate_session_status(results) == SessionStatus.PARTIAL.value

    def test_empty_results_is_failed(self):
        assert _aggregate_session_status([]) == SessionStatus.FAILED.value


# ----------------------------------------------------------------------
# 编排主流程
# ----------------------------------------------------------------------


class TestRunMultiOrganDiagnosis:
    @pytest.mark.asyncio
    async def test_serial_order_and_full_completion(self):
        with _orchestrator_env() as state:
            outcome = await run_multi_organ_diagnosis("sess_multi")

        assert state["pipeline_calls"] == ["req_thyroid", "req_liver", "req_cardiac"]
        assert outcome.organs == ["thyroid", "liver", "cardiac"]
        assert outcome.session_status == SessionStatus.COMPLETED.value
        assert outcome.partial is False
        assert outcome.completed_organs == ["thyroid", "liver", "cardiac"]
        assert outcome.organ_statuses == {
            "thyroid": "completed",
            "liver": "completed",
            "cardiac": "completed",
        }

    @pytest.mark.asyncio
    async def test_failure_does_not_block_remaining_organs(self):
        with _orchestrator_env(behaviors={"liver": "failed"}) as state:
            outcome = await run_multi_organ_diagnosis("sess_multi")

        # 肝脏失败后，心脏仍被执行（串行不中断）
        assert state["pipeline_calls"] == ["req_thyroid", "req_liver", "req_cardiac"]
        assert outcome.session_status == SessionStatus.PARTIAL.value
        assert outcome.partial is True
        assert outcome.failed_organs == ["liver"]
        assert outcome.completed_organs == ["thyroid", "cardiac"]

    @pytest.mark.asyncio
    async def test_pipeline_exception_is_isolated(self):
        with _orchestrator_env(
            behaviors={"liver": RuntimeError("boom")},
            row_status={"req_liver": "failed"},
        ) as state:
            outcome = await run_multi_organ_diagnosis("sess_multi")

        assert len(state["pipeline_calls"]) == 3
        liver = next(r for r in outcome.organ_results if r.organ == "liver")
        assert liver.status == OrganStatus.FAILED.value
        assert liver.reason.startswith("pipeline_error")

    @pytest.mark.asyncio
    async def test_per_organ_timeout_marks_failed_and_continues(self):
        with _orchestrator_env(
            behaviors={"liver": "timeout"},
            row_status={"req_liver": "needs_review"},
        ) as state:
            outcome = await run_multi_organ_diagnosis(
                "sess_multi", per_organ_timeout_s=0.05, total_timeout_s=5.0
            )

        assert state["pipeline_calls"] == ["req_thyroid", "req_liver", "req_cardiac"]
        liver = next(r for r in outcome.organ_results if r.organ == "liver")
        # 超时 = 未产出结论（区别于"已产出但需复核"）
        assert liver.status == OrganStatus.FAILED.value
        assert liver.reason == "per_organ_timeout"
        assert outcome.failed_organs == ["liver"]
        assert outcome.session_status == SessionStatus.PARTIAL.value
        assert outcome.partial is True
        # 落库状态仍为 needs_review（可重跑）
        state["diagnosis_repo"].update_result.assert_awaited()
        assert (
            state["diagnosis_repo"].update_result.await_args.kwargs["status"]
            == "needs_review"
        )

    @pytest.mark.asyncio
    async def test_total_budget_exhaustion_skips_remaining_organs(self):
        with _orchestrator_env(behaviors={"thyroid": "timeout"}) as state:
            outcome = await run_multi_organ_diagnosis(
                "sess_multi", per_organ_timeout_s=5.0, total_timeout_s=0.05
            )

        # 首个器官耗尽全局预算，后续器官直接 skipped（未调用管线）
        assert state["pipeline_calls"] == ["req_thyroid"]
        assert outcome.skipped_organs == ["liver", "cardiac"]
        # 三个器官都没产出结论 → 会话失败（0 < X < N 的部分完成不成立）
        assert outcome.session_status == SessionStatus.FAILED.value
        assert outcome.partial is False
        skipped = next(r for r in outcome.organ_results if r.organ == "liver")
        assert skipped.reason == "total_budget_exhausted"

    @pytest.mark.asyncio
    async def test_organ_switching_pushed_on_enter_and_exit(self):
        with _orchestrator_env(session_organs=("thyroid",)) as state:
            await run_multi_organ_diagnosis("sess_multi")

        assert state["switching"] == [("thyroid", True), ("thyroid", False)]

    @pytest.mark.asyncio
    async def test_progress_persisted_with_final_session_status(self):
        with _orchestrator_env(behaviors={"liver": "failed"}) as state:
            await run_multi_organ_diagnosis("sess_multi")

        updates = state["session_updates"]
        # 首次写 active，逐器官写进度，末次写最终汇总状态
        assert updates[0]["status"] == SessionStatus.ACTIVE.value
        assert updates[-1]["status"] == SessionStatus.PARTIAL.value
        assert updates[-1]["organ_statuses"]["liver"] == OrganStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_in_progress_marked_before_pipeline_runs(self):
        with _orchestrator_env(session_organs=("thyroid",)) as state:
            await run_multi_organ_diagnosis("sess_multi")

        in_progress = [
            u for u in state["session_updates"] if u["organ_statuses"].get("thyroid") == "in_progress"
        ]
        assert in_progress, "器官开始执行前应先写 in_progress 进度"

    @pytest.mark.asyncio
    async def test_commits_transaction(self):
        with _orchestrator_env(session_organs=("thyroid",)) as state:
            await run_multi_organ_diagnosis("sess_multi")

        assert state["db"].commit.await_count >= 1

    @pytest.mark.asyncio
    async def test_reuses_reusable_existing_request(self):
        with _orchestrator_env(session_organs=("thyroid",)) as state:
            state["diagnosis_repo"].get_results_by_session = AsyncMock(
                return_value=[
                    MagicMock(organ="thyroid", status="processing", request_id="req_existing"),
                ]
            )
            outcome = await run_multi_organ_diagnosis("sess_multi")

        assert state["pipeline_calls"] == ["req_existing"]
        assert state["created_requests"] == []
        assert outcome.organ_results[0].request_id == "req_existing"

    @pytest.mark.asyncio
    async def test_explicit_organs_subset(self):
        with _orchestrator_env() as state:
            outcome = await run_multi_organ_diagnosis("sess_multi", organs=["cardiac"])

        assert state["pipeline_calls"] == ["req_cardiac"]
        assert outcome.organs == ["cardiac"]
        assert outcome.session_status == SessionStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_session_not_found_raises(self):
        with _orchestrator_env(exists=False), pytest.raises(SessionNotFoundError):
            await run_multi_organ_diagnosis("sess_missing")

    @pytest.mark.asyncio
    async def test_empty_organs_returns_early(self):
        with _orchestrator_env(session_organs=()) as state:
            outcome = await run_multi_organ_diagnosis("sess_multi")

        assert state["pipeline_calls"] == []
        assert outcome.organs == []
        assert outcome.total_ms >= 0.0

    @pytest.mark.asyncio
    async def test_outcome_as_dict_is_serializable(self):
        with _orchestrator_env(session_organs=("thyroid",)):
            outcome = await run_multi_organ_diagnosis("sess_multi")

        payload = outcome.as_dict()
        assert payload["session_status"] == SessionStatus.COMPLETED.value
        assert payload["organ_results"][0]["organ"] == "thyroid"
        assert isinstance(payload["total_ms"], float)

    @pytest.mark.asyncio
    async def test_latency_metric_recorded(self):
        with _orchestrator_env(session_organs=("thyroid",)) as state:
            with patch.object(mod, "_record_latency") as metric:
                await run_multi_organ_diagnosis("sess_multi")
        metric.assert_called_once()
        recorded = metric.call_args.args[0]
        assert recorded.session_id == "sess_multi"
        assert state["db"].commit.await_count >= 1
