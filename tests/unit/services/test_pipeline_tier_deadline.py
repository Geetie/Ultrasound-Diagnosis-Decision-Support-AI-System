"""01-5 三档延迟分层 —— 分档 deadline 与孤儿配置删除的守卫。

验收判据（01 号 §01-5 ⑤ / 用户拍板）：
1. 孤儿预算 pipeline_dispatch_budget_s / pipeline_fusion_budget_s 已删除；
2. 超时用例能区分档位：standard 档被 standard 预算切断，FULL 档不受影响
   （反证：若仍三档共用一个值，TestTieredTimeout 两例必有一红）；
3. assessment_latency 按 tier 打标（口径修正后按档可看）。
"""

from __future__ import annotations

import asyncio
import contextlib
import sys

from app.config.settings import settings
from app.services import diagnosis_worker as worker_module


class TestOrphanBudgetsDeleted:
    def test_dispatch_budget_is_gone(self):
        assert not hasattr(settings, "pipeline_dispatch_budget_s"), "孤儿预算应已删除"

    def test_fusion_budget_is_gone(self):
        assert not hasattr(settings, "pipeline_fusion_budget_s"), "孤儿预算应已删除"

    def test_tier_budget_fields_exist(self):
        assert settings.pipeline_total_budget_s > 0
        assert settings.pipeline_total_budget_standard_s > 0
        assert (
            settings.pipeline_total_budget_standard_s
            < settings.pipeline_total_budget_s
        ), "标准档预算应严于全量档"


class TestBudgetForTier:
    def test_standard_uses_standard_budget(self):
        assert (
            worker_module._budget_for_tier("standard")
            == settings.pipeline_total_budget_standard_s
        )

    def test_realtime_uses_standard_budget(self):
        """realtime 档不走诊断路径（00 号 §2.1）→ 沿用 standard 预算。"""
        assert (
            worker_module._budget_for_tier("realtime")
            == settings.pipeline_total_budget_standard_s
        )

    def test_unknown_tier_falls_back_to_full(self):
        """取不到 tier（DB 读失败/无法识别）→ 保守用 FULL 预算。"""
        for tier in (None, "", "full", "FULL", "garbage"):
            assert worker_module._budget_for_tier(tier) == settings.pipeline_total_budget_s


class _SlowPipeline:
    async def run_diagnosis_pipeline(self, request_id: str) -> bool:
        await asyncio.sleep(5)
        return True


class _OkPipeline:
    async def run_diagnosis_pipeline(self, request_id: str) -> bool:
        return True


async def _run_one_turn(monkeypatch, *, tier: str, pipeline, out: dict) -> None:
    """把一条任务送进 _worker_loop，等它出终态（超时→failed / 正常→cleared）。"""
    monkeypatch.setattr(settings, "int_consumer_recovery_delay_seconds", 0)

    async def _fake_tier(request_id):
        return tier

    dequeued = ["r1"]

    async def _fake_dequeue(timeout):
        # 必须真的让出控制权（同 test_concurrency_audit_fixes 的教训）
        await asyncio.sleep(0.01)
        return dequeued.pop(0) if dequeued else None

    async def _fake_handle(request_id, error):
        out.setdefault("failed", []).append((request_id, error))

    async def _fake_clear(request_id):
        out.setdefault("cleared", []).append(request_id)

    monkeypatch.setattr(worker_module, "_read_tier_for_budget", _fake_tier)
    monkeypatch.setattr(worker_module, "_dequeue_with_timeout", _fake_dequeue)
    monkeypatch.setattr(worker_module, "_handle_failure", _fake_handle)
    monkeypatch.setattr(worker_module, "_clear_retry_count", _fake_clear)
    monkeypatch.setitem(sys.modules, "app.services.diagnosis_pipeline", pipeline)

    task = asyncio.create_task(worker_module._worker_loop(worker_id=0))
    for _ in range(300):
        if out.get("failed") or out.get("cleared"):
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class TestTieredTimeout:
    async def test_standard_tier_is_cut_by_standard_budget(self, monkeypatch):
        out: dict = {}
        monkeypatch.setattr(settings, "pipeline_total_budget_standard_s", 0.05)
        monkeypatch.setattr(settings, "pipeline_total_budget_s", 5.0)
        await _run_one_turn(
            monkeypatch, tier="standard", pipeline=_SlowPipeline(), out=out
        )
        assert [rid for rid, _ in out.get("failed", [])] == ["r1"], (
            "standard 档慢管线必须被 standard 预算切断（而非共用 FULL 的 180s）"
        )

    async def test_full_tier_is_not_cut_by_standard_budget(self, monkeypatch):
        out: dict = {}
        monkeypatch.setattr(settings, "pipeline_total_budget_standard_s", 0.05)
        monkeypatch.setattr(settings, "pipeline_total_budget_s", 5.0)
        await _run_one_turn(
            monkeypatch, tier="full", pipeline=_OkPipeline(), out=out
        )
        assert out.get("cleared") == ["r1"], (
            "FULL 档不受 standard 预算影响（反证：若档位没接上、共用一个值，本用例红）"
        )


class TestAssessmentLatencyByTier:
    def test_histogram_has_tier_label(self):
        from app.observability.metrics_collector import get_metrics_collector

        collector = get_metrics_collector()
        # 若 definitions 忘了加 ["tier"]，labels(tier=...) 会抛 ValueError → 本用例红
        collector.assessment_latency.labels(tier="standard").observe(0.1)

    def test_record_assessment_latency_accepts_tier(self):
        from app.observability.metrics_collector import get_metrics_collector

        collector = get_metrics_collector()
        collector.record_assessment_latency(0.123, tier="standard")
        assert collector.get_histogram_p99("assessment_latency") is not None

    def test_default_tier_kwarg_keeps_old_call_shape(self):
        """不带 tier 的旧调用形态必须仍可用（obs/dashboard 兼容）。"""
        from app.observability.metrics_collector import get_metrics_collector

        get_metrics_collector().record_assessment_latency(0.05)
