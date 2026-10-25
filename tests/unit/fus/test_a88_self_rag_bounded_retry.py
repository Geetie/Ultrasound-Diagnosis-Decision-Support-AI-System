"""A.88 T5-5：Self-RAG **预算感知的有界重试**（机制 + 反证腿）。

## 为什么不是"直接把 ``self_rag_max_retries`` 改成 1"

A.51（2026-10-11/12，实测在 16 号附录 A.51）把 ``self_rag_max_retries`` 置 0，
换来 validate **P50 717.5ms / P99 884.6ms**（此前 P95=P99=2295.7ms）。其数据是
**双峰**：743~783ms（一次通过）vs 1853~2295ms（失败后重试）。也就是说
**一次重试本身就值 +1.11s**，最坏落点 2295.7ms ≈ 2.3s。

因此"重试 + 预算"能不能把 P99 压回 2.3s 以下，取决于**重试占比**：
只要 >1% 的请求走到重试，P99 就会落进 1.85~2.3s 那个峰（实测上界已经贴着 2.3s）。
这个占比在本仓库没有实测数据 → **无法论证**。按任务要求：**不改默认值**，
改为交付"机制已支持 + 开启条件 + 风险说明"（诚实交付 > 冒险改默认）。

## 本文件钉住的是"机制"，不是"默认值"

机制：重试必须**与 validate 共用同一个截止时刻**（deadline）。这样无论配几次
重试，validate 的总耗时都封顶在 ``pipeline_validate_budget_s`` 内 —— 重试
再也不可能把 validate 拖到预算外，超预算仍走既有
``validate_degraded`` 优雅降级（不失败、不挂死）。

反证腿：
* 让 ``_retry_affordable`` 永远返回 True（= 不看预算）→
  ``TestBudgetAwareRetry::test_retry_skipped_when_budget_insufficient`` 红；
* 把 deadline 传成 None 且无预算 → 退化为旧行为（无限重试到 max_retries），
  ``test_no_deadline_keeps_legacy_retry_behaviour`` 钉住这是**显式**的。
"""
from __future__ import annotations

import time

from app.agents.protocols import AgentResponse
from app.fusion.self_rag import SelfRAGEvaluator
from app.models.schemas import Organ


def _resp(confidence: float = 0.8) -> AgentResponse:
    return AgentResponse(
        agent_name="thyroid_specialist",
        organ=Organ.THYROID,
        content="TR4",
        confidence=confidence,
        evidence_chunk_ids=["c1"],
    )


class _ScriptedEngine:
    """按顺序返回预设评估文本（每次 generate 消耗一条）。"""

    def __init__(self, texts: list[str], delay_s: float = 0.0) -> None:
        self._texts = list(texts)
        self._delay = delay_s
        self.calls = 0

    async def generate(self, request):
        self.calls += 1
        if self._delay:
            import asyncio

            await asyncio.sleep(self._delay)
        from app.inference.protocols import (
            InferenceResponse,
            TokenUsage,
        )

        text = self._texts[min(self.calls - 1, len(self._texts) - 1)]
        return InferenceResponse(
            text=text,
            model="scripted",
            usage=TokenUsage(),
            finish_reason="stop",
            request_id=request.request_id,
            session_id=request.session_id,
            latency_ms=1.0,
        )

    async def health_check(self) -> bool:
        return True

    async def get_model_info(self):
        from app.inference.protocols import ModelInfo

        return ModelInfo(model_name="scripted", model_version="v1")

    async def close(self) -> None:
        return None


FAIL_TEXT = (
    "evidence_sufficiency: 1\n"
    "evidence_consistency: 1\n"
    "feature_completeness: 1\n"
    "missing_features: 钙化\n"
)
PASS_TEXT = (
    "evidence_sufficiency: 5\n"
    "evidence_consistency: 5\n"
    "feature_completeness: 5\n"
    "missing_features: 无\n"
)


class TestBudgetAwareRetry:
    """核心：预算不够就不重试（这不是"少试一次"，是"不许超预算"）。"""

    async def test_retry_happens_when_budget_allows(self):
        engine = _ScriptedEngine([FAIL_TEXT, PASS_TEXT])
        evaluator = SelfRAGEvaluator(inference_engine=engine, max_retries=1)

        result = await evaluator.evaluate(
            _resp(), deadline=time.monotonic() + 30.0
        )

        assert engine.calls == 2, "预算充足时应该真的重试一次"
        assert result.passed is True

    async def test_retry_skipped_when_budget_insufficient(self):
        """反证腿：去掉预算判据 → 会打第 2 次 LLM，本用例红。"""
        engine = _ScriptedEngine([FAIL_TEXT, PASS_TEXT])
        evaluator = SelfRAGEvaluator(inference_engine=engine, max_retries=1)

        result = await evaluator.evaluate(
            # 截止时刻已经过去（预算耗尽）→ 不许再发一次请求
            _resp(),
            deadline=time.monotonic() - 1.0,
            per_attempt_cost_s=1.2,
        )

        assert engine.calls == 1, "预算已耗尽还重试 = 把 validate 拖出预算外"
        assert result.passed is False
        assert "retry_skipped_budget" in result.reason

    async def test_retry_skipped_when_remaining_too_small_for_next_call(self):
        """剩余预算 < 单次调用成本估计 → 同样不重试（提前收手，避免半路被砍）。"""
        engine = _ScriptedEngine([FAIL_TEXT, PASS_TEXT])
        evaluator = SelfRAGEvaluator(inference_engine=engine, max_retries=1)

        result = await evaluator.evaluate(
            _resp(),
            deadline=time.monotonic() + 0.5,
            per_attempt_cost_s=1.2,
        )

        assert engine.calls == 1
        assert "retry_skipped_budget" in result.reason

    async def test_no_deadline_keeps_legacy_retry_behaviour(self):
        """不传 deadline = 显式选择旧行为（按 max_retries 重试）。"""
        engine = _ScriptedEngine([FAIL_TEXT, PASS_TEXT])
        evaluator = SelfRAGEvaluator(inference_engine=engine, max_retries=1)

        result = await evaluator.evaluate(_resp())

        assert engine.calls == 2
        assert result.passed is True

    async def test_zero_retries_never_calls_twice(self):
        engine = _ScriptedEngine([FAIL_TEXT, PASS_TEXT])
        evaluator = SelfRAGEvaluator(inference_engine=engine, max_retries=0)

        result = await evaluator.evaluate(_resp(), deadline=time.monotonic() + 30.0)

        assert engine.calls == 1
        assert result.passed is False

    async def test_zero_score_still_short_circuits_even_with_budget(self):
        """既有 T-Perf 口径不变：确定性零分重发同样输入不会变，直接返回。"""
        zero_text = (
            "evidence_sufficiency: 0\n"
            "evidence_consistency: 0\n"
            "feature_completeness: 0\n"
            "missing_features: 无\n"
        )
        engine = _ScriptedEngine([zero_text, PASS_TEXT])
        evaluator = SelfRAGEvaluator(inference_engine=engine, max_retries=2)

        result = await evaluator.evaluate(_resp(), deadline=time.monotonic() + 30.0)

        assert engine.calls == 1
        assert "zero_score_no_retry" in result.reason


class TestDefaultIsStillOff:
    """红线：默认值**不许**因为本项改动而变（A.51 降载口径要保住）。"""

    def test_repo_default_config_stays_zero(self):
        from app.config.settings import settings

        assert settings.self_rag_max_retries == 0, (
            "改了默认值就会回到 A.51 之前的双峰 P99 —— 论证不足时不得改默认"
        )

    def test_main_wires_settings_value_not_a_hardcoded_one(self):
        """main.py 必须从 settings 取（配置已支持开启），不许写死常量。"""
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[3] / "app" / "main.py"
        ).read_text(encoding="utf-8")

        assert 'getattr(settings, "self_rag_max_retries", 0)' in source


class TestBudgetIsPassedDown:
    """预算必须从 FusionEngineImpl 真的传到 Self-RAG（否则机制是死的）。"""

    def test_layered_validator_forwards_deadline(self):
        import inspect

        from app.fusion.layered_validator import LayeredValidator

        sig = inspect.signature(LayeredValidator.validate)
        assert "deadline" in sig.parameters, (
            "validate 不接收 deadline → Self-RAG 的重试预算无从得知"
        )

    def test_engine_passes_budget_deadline(self):
        import inspect

        from app.fusion.service import FusionEngineImpl

        assert "_deadline_for_validate" in inspect.getsource(FusionEngineImpl), (
            "FusionEngineImpl 必须把 validate 预算换成 deadline 往下传"
        )
