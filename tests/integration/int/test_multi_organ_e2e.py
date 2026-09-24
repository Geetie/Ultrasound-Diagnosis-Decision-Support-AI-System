"""T-GB-INT-04 多器官联合诊断端到端与延迟验证。

验证目标（对应验收标准）：

- 三器官按规格顺序**串行**推理，单器官失败不阻断其余器官（部分完成）；
- 单器官超时 30s / 全局 90s 的预算来自 settings 且可被请求级覆盖；
- 端到端耗时落在全局预算内，并上报 ``record_multi_organ_latency``
  （该指标此前只有定义与读取方，无任何写入方）；
- 会话 ``organ_statuses`` 与汇总状态完整回写（前端多器官进度依赖）。
"""
from __future__ import annotations

import asyncio
import time
from contextlib import contextmanager
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.settings import settings
from app.models.schemas import SessionStatus
from app.services import multi_organ_orchestrator as mod
from app.services.multi_organ_orchestrator import run_multi_organ_diagnosis


class _AsyncCtx:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *exc_info):
        return False


@contextmanager
def _chain_env(*, behaviors: Dict[str, str] | None = None, session_organs=("thyroid", "liver", "cardiac")):
    """构造"会话 + 三器官请求 + 管线"的集成环境（仅 mock 外部边界）。"""
    behaviors = dict(behaviors or {})
    state: Dict[str, Any] = {"calls": [], "session_updates": [], "metrics": []}

    db = MagicMock()
    db.expire_all = MagicMock()
    db.commit = AsyncMock()

    session_obj = MagicMock()
    session_obj.session_id = "sess_multi_e2e"
    session_obj.target_organs = list(session_organs)
    session_obj.organ_statuses = {o: "pending" for o in session_organs}

    session_repo = MagicMock()
    session_repo.db = db
    session_repo.get_session = AsyncMock(return_value=session_obj)

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
        return f"req_{organ}"

    diagnosis_repo.create_request = AsyncMock(side_effect=_create_request)

    async def _get_result(request_id):
        organ = request_id.split("_", 1)[1]
        return MagicMock(
            status=behaviors.get(organ, "completed"), confidence=0.87
        )

    diagnosis_repo.get_result = AsyncMock(side_effect=_get_result)

    async def _pipeline(request_id):
        state["calls"].append(request_id)
        organ = request_id.split("_", 1)[1]
        behavior = behaviors.get(organ, "completed")
        if behavior == "timeout":
            await asyncio.sleep(30)
        if isinstance(behavior, Exception):
            raise behavior
        return behavior != "failed"

    async def _push(session_id, organ, *, switching):
        state.setdefault("switching", []).append((organ, switching))

    def _metrics_factory():
        collector = MagicMock()
        collector.record_multi_organ_latency = MagicMock(
            side_effect=lambda seconds, with_retry=False: state["metrics"].append(
                {"seconds": seconds, "with_retry": with_retry}
            )
        )
        return collector

    with patch.object(
        mod, "AsyncSessionLocal", MagicMock(return_value=_AsyncCtx(db))
    ), patch.object(
        mod, "SessionRepository", MagicMock(return_value=session_repo)
    ), patch.object(
        mod, "DiagnosisRepository", MagicMock(return_value=diagnosis_repo)
    ), patch.object(
        mod, "_push_organ_switching", AsyncMock(side_effect=_push)
    ), patch(
        "app.observability.metrics_collector.get_metrics_collector",
        _metrics_factory,
    ), patch(
        "app.services.diagnosis_pipeline.run_diagnosis_pipeline",
        AsyncMock(side_effect=_pipeline),
    ):
        state["session_repo"] = session_repo
        state["diagnosis_repo"] = diagnosis_repo
        yield state


def test_multi_organ_budget_defaults_match_spec():
    """规格：单器官 30s、多器官串行 90s。"""
    assert settings.multi_organ_per_organ_timeout_seconds == 30.0
    assert settings.multi_organ_total_timeout_seconds == 90.0


@pytest.mark.asyncio
async def test_multi_organ_serial_e2e_completes_within_budget():
    with _chain_env() as state:
        started = time.monotonic()
        outcome = await run_multi_organ_diagnosis("sess_multi_e2e")
        elapsed = time.monotonic() - started

    # 串行顺序固定为 甲状腺 → 肝脏 → 心脏
    assert state["calls"] == ["req_thyroid", "req_liver", "req_cardiac"]
    assert outcome.session_status == SessionStatus.COMPLETED.value
    assert outcome.partial is False
    assert outcome.completed_organs == ["thyroid", "liver", "cardiac"]
    assert outcome.organ_statuses == {
        "thyroid": "completed",
        "liver": "completed",
        "cardiac": "completed",
    }
    # 端到端耗时必须落在全局预算内
    assert elapsed < settings.multi_organ_total_timeout_seconds
    assert outcome.total_ms < settings.multi_organ_total_timeout_seconds * 1000

    # 指标上报（此前无写入方）
    assert len(state["metrics"]) == 1
    assert state["metrics"][0]["seconds"] > 0
    assert state["metrics"][0]["with_retry"] is False

    # 会话进度回写：首次 active → 逐器官 → 末次汇总
    updates = state["session_updates"]
    assert updates[0]["status"] == SessionStatus.ACTIVE.value
    assert updates[-1]["status"] == SessionStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_multi_organ_partial_completion_metric_marks_retry():
    """肝脏失败 → 其余器官仍完成，会话为 partial，指标带 with_retry。"""
    with _chain_env(behaviors={"liver": "failed"}) as state:
        outcome = await run_multi_organ_diagnosis("sess_multi_e2e")

    assert state["calls"] == ["req_thyroid", "req_liver", "req_cardiac"]
    assert outcome.session_status == SessionStatus.PARTIAL.value
    assert outcome.partial is True
    assert outcome.failed_organs == ["liver"]
    assert outcome.completed_organs == ["thyroid", "cardiac"]
    assert state["metrics"][0]["with_retry"] is True

    # 心跳式进度：每个器官都经历 in_progress
    in_progress = [
        u for u in state["session_updates"]
        if "in_progress" in (u["organ_statuses"] or {}).values()
    ]
    assert len(in_progress) >= 3


@pytest.mark.asyncio
async def test_multi_organ_per_organ_timeout_isolated():
    """单器官超时不拖垮整体（T-GB-AGT-01 的核心诉求）。"""
    with _chain_env(behaviors={"liver": "timeout"}) as state:
        outcome = await run_multi_organ_diagnosis(
            "sess_multi_e2e", per_organ_timeout_s=0.05, total_timeout_s=10.0
        )

    assert state["calls"] == ["req_thyroid", "req_liver", "req_cardiac"]
    liver = next(r for r in outcome.organ_results if r.organ == "liver")
    assert liver.reason == "per_organ_timeout"
    assert outcome.session_status == SessionStatus.PARTIAL.value
    # 超时器官落库为可重跑的 needs_review
    assert state["diagnosis_repo"].update_result.await_args.kwargs["status"] == "needs_review"


@pytest.mark.asyncio
async def test_multi_organ_global_budget_marks_remaining_skipped():
    with _chain_env(behaviors={"thyroid": "timeout"}) as state:
        outcome = await run_multi_organ_diagnosis(
            "sess_multi_e2e", per_organ_timeout_s=5.0, total_timeout_s=0.05
        )

    assert state["calls"] == ["req_thyroid"]
    assert outcome.skipped_organs == ["liver", "cardiac"]
    assert state["session_updates"][-1]["status"] == SessionStatus.FAILED.value


@pytest.mark.asyncio
async def test_multi_organ_progress_signals_pushed_per_organ():
    """前端多器官进度依赖 organ_switching（此前后端从未发送）。"""
    with _chain_env(session_organs=("thyroid", "liver")) as state:
        await run_multi_organ_diagnosis("sess_multi_e2e")

    assert state["switching"] == [
        ("thyroid", True),
        ("thyroid", False),
        ("liver", True),
        ("liver", False),
    ]
