"""MonitorAgent 入管线接线守卫。

## 为什么有这条测试

2026-09-28 接线审计发现：``MonitorAgent.track_diagnosis`` 早已实现，但
**没有任何生产调用方** —— R1（低置信度）/R2（性能降级）/R3（同器官连续失败）
三条规则从未在诊断链路上运行过。``agents/monitor.py`` 的单测只证明
"规则本身正确"，证明不了"规则被调用"（同一会话里这类缺陷已出现第四次）。

## 两处必须钉住的语义

1. **终态归一**：MonitorAgent 的 R1/R2 判据是 ``status == "completed"``，而管线
   还有 ``needs_review`` 这个终态（融合层已标记需复核）——它恰恰是低置信度最常见
   的情形。若原样传入，R1/R2 会被静默跳过，因此归一为 ``completed``。
   失败/超时/取消**不**归一，以保证 R3 的失败判定不被污染。
2. **失败安全**：监控是旁路，取不到代理、器官解析失败、代理内部报错都只告警，
   绝不影响诊断结论。
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents import monitor as monitor_module
from app.services import diagnosis_pipeline as pipeline_module
from app.services.diagnosis_pipeline import _safe_track_diagnosis

PIPELINE_PY = Path(__file__).resolve().parents[3] / "app" / "services" / "diagnosis_pipeline.py"


def _row(organ: str = "thyroid", session_id: str = "sess-1") -> SimpleNamespace:
    """DB 行替身：``diagnosis_results.organ`` 是字符串（database.py:65）。"""
    return SimpleNamespace(organ=organ, session_id=session_id)


@pytest.fixture
def monitor_spy(monkeypatch):
    """把进程内 MonitorAgent 换成 spy，并让 _get_monitor_agent 取到它。"""
    from app.main import app as app_instance

    spy = MagicMock()
    spy.track_diagnosis = AsyncMock()
    monkeypatch.setattr(app_instance.state, "monitor_agent", spy, raising=False)
    return spy


class TestPipelineActuallyCallsMonitor:
    async def test_helper_invokes_track_diagnosis_once(self, monitor_spy):
        await _safe_track_diagnosis(
            _row(),
            request_id="req-1",
            status="completed",
            processing_time_ms=1200,
            confidence=0.91,
            sg_ok=True,
        )

        monitor_spy.track_diagnosis.assert_awaited_once()
        monitor_request, monitor_response = monitor_spy.track_diagnosis.await_args.args
        assert monitor_request.target_organ.value == "thyroid"
        assert monitor_response.request_id == "req-1"
        assert monitor_response.status == "completed"
        assert monitor_response.quality_control.confidence_score == pytest.approx(0.91)
        assert monitor_response.processing_time_ms == 1200

    def test_run_pipeline_has_three_call_sites(self):
        """源码断言：成功 / 失败 / 取消三条终态路径都必须接线。"""
        source = PIPELINE_PY.read_text(encoding="utf-8")
        code = "\n".join(
            line for line in source.splitlines() if not line.strip().startswith("#")
        )

        assert code.count("await _safe_track_diagnosis(") >= 3, (
            "管线的成功/失败/取消三条终态路径都应调用监控"
        )
        for status in ('status="cancelled"', 'status="failed"'):
            assert status in code


class TestTerminalStatusNormalization:
    async def test_needs_review_is_normalized_so_r1_r2_can_fire(self, monitor_spy):
        await _safe_track_diagnosis(
            _row(),
            request_id="req-2",
            status="needs_review",
            processing_time_ms=900,
            confidence=0.2,
        )

        _, monitor_response = monitor_spy.track_diagnosis.await_args.args
        assert monitor_response.status == "completed", (
            "needs_review 必须归一为 completed，否则低置信度不会触发 R1"
        )
        assert monitor_response.requires_manual_review is True

    async def test_failed_status_marks_ai_diagnosis_failed(self, monitor_spy):
        await _safe_track_diagnosis(
            _row(),
            request_id="req-3",
            status="failed",
            processing_time_ms=800,
            confidence=0.0,
        )

        _, monitor_response = monitor_spy.track_diagnosis.await_args.args
        assert monitor_response.status == "failed"
        assert monitor_response.ai_diagnosis_failed is True

    async def test_cancelled_is_not_an_ai_failure(self, monitor_spy):
        """取消是政策叫停，不是 AI 失败 —— 不应污染 R3 的失败窗口。"""
        await _safe_track_diagnosis(
            _row(),
            request_id="req-4",
            status="cancelled",
            processing_time_ms=700,
        )

        _, monitor_response = monitor_spy.track_diagnosis.await_args.args
        assert monitor_response.status == "cancelled"
        assert monitor_response.ai_diagnosis_failed is False


class TestFailSafe:
    async def test_missing_monitor_agent_is_noop(self, monkeypatch):
        from app.main import app as app_instance

        monkeypatch.setattr(app_instance.state, "monitor_agent", None, raising=False)
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        await _safe_track_diagnosis(
            _row(), request_id="req-5", status="completed", processing_time_ms=100
        )

        events = [c.args[0] for c in fake_logger.warning.call_args_list if c.args]
        assert "monitor_agent_unavailable" in events

    async def test_monitor_exception_is_swallowed(self, monitor_spy, monkeypatch):
        monitor_spy.track_diagnosis = AsyncMock(side_effect=RuntimeError("monitor blew up"))
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        # 不抛异常即为通过（诊断结论不得被监控拖垮）
        await _safe_track_diagnosis(
            _row(), request_id="req-6", status="completed", processing_time_ms=100
        )

        events = [c.args[0] for c in fake_logger.warning.call_args_list if c.args]
        assert "monitor_track_diagnosis_failed" in events

    async def test_unresolvable_organ_skips_with_warning(self, monitor_spy, monkeypatch):
        fake_logger = MagicMock()
        monkeypatch.setattr(pipeline_module, "logger", fake_logger)

        await _safe_track_diagnosis(
            _row(organ="not-an-organ"),
            request_id="req-7",
            status="completed",
            processing_time_ms=100,
        )

        monitor_spy.track_diagnosis.assert_not_awaited()
        events = [c.args[0] for c in fake_logger.warning.call_args_list if c.args]
        assert "monitor_organ_unresolved" in events


class TestRealMonitorRulesActuallyFire:
    """用**真实** MonitorAgent 验证 R1/R3 真会触发（不是只验证"被调用"）。"""

    @pytest.fixture
    def real_monitor(self, monkeypatch):
        from app.agents.monitor import MonitorAgentImpl

        monkeypatch.setattr(monitor_module, "logger", MagicMock())
        return MonitorAgentImpl(metrics_collector_factory=lambda: MagicMock())

    async def test_r1_fires_for_low_confidence(self, real_monitor, monkeypatch):
        from app.main import app as app_instance

        monkeypatch.setattr(app_instance.state, "monitor_agent", real_monitor, raising=False)

        await _safe_track_diagnosis(
            _row(),
            request_id="req-r1",
            status="needs_review",
            processing_time_ms=1000,
            confidence=0.05,
        )

        rules = [
            call.kwargs.get("rule")
            for call in monitor_module.logger.warning.call_args_list
            if call.args and call.args[0] == "monitor_anomaly"
        ]
        assert "R1" in rules

    async def test_r3_fires_after_consecutive_failures(self, real_monitor, monkeypatch):
        """窗口大小由实现自身决定（不写死 10），连续失败达窗口即触发 R3。"""
        from app.main import app as app_instance

        monkeypatch.setattr(app_instance.state, "monitor_agent", real_monitor, raising=False)
        window = real_monitor._window

        for i in range(window):
            await _safe_track_diagnosis(
                _row(),
                request_id=f"req-r3-{i}",
                status="failed",
                processing_time_ms=500,
                confidence=0.0,
            )

        rules = [
            call.kwargs.get("rule")
            for call in monitor_module.logger.error.call_args_list
            if call.args and call.args[0] == "monitor_anomaly"
        ]
        assert "R3" in rules
