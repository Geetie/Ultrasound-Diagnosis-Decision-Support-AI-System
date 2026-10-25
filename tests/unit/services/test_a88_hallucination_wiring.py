"""A.88 T5-3：幻觉检测接入生产管线（``hallucination_severity`` 有生产者的守卫）。

## 为什么要这条测试

``DiagnosisResult.hallucination_severity`` 此前**全仓无生产者**：检测器只在
``app/inference/benchmark.py``（评测路径）里被调用，结果接口只能填 ``None``。
接口侧当时的注释写得很诚实（"无生产者不得填值"），但那条诚实掩盖了一个事实：
**能力等于不存在**（《架构真相》§3.3「幻觉检测：只在评测/benchmark 路径」）。

## 钉住的五条不变量

1. **真的跑**：FULL 档（或开关打开）时，对 ``final_response.content`` 真调检测器；
2. **真的落**：结果写进 ``findings.fusion_result``，键名 ``hallucination_severity``
   与 schema 字段同名（读取侧可直接取）；
3. **"未评估" ≠ "评估过且干净"**：``hallucination_checked=False`` + 真实
   ``hallucination_reason``；**不许**用 ``None`` 把两种情形抹平；
4. **失败不阻断**：检测器抛错 → 诊断照常写库，只留 ``checked=False`` + 原因；
5. **不在非 FULL 档白跑**：FAST/COMPACT 档默认跳过（成本控制）。

反证腿：把 ``_write_result_to_db`` 里写 ``hallucination_severity`` 的那几行删掉，
``TestWrittenIntoFindings`` 立刻红；把 ``checked`` 的默认值改成 ``True``，
``TestNotCheckedIsNotClean`` 立刻红。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import FusionResult
from app.models.schemas import Organ
from app.services.diagnosis_pipeline import (
    _detect_hallucination_safely,
    _fusion_result_to_dict,
)


def _final(content: str = "甲状腺 · TR4 — 分级: TR4 总分: 7 置信度: 0.85 理由: 低回声结节"):
    return AgentResponse(
        agent_name="thyroid_specialist",
        organ=Organ.THYROID,
        content=content,
        confidence=0.85,
        evidence_chunk_ids=["c1"],
    )


def _fusion_without_organ() -> FusionResult:
    """organ 缺失的响应：检测器按器官取词表，没有器官就不能猜。"""
    return FusionResult(
        final_response=AgentResponse(
            agent_name="thyroid_specialist",
            organ=None,
            content="甲状腺 · TR4 — 分级: TR4",
            confidence=0.8,
        )
    )


def _fusion(content: str | None = None):
    return FusionResult(
        final_response=_final() if content is None else _final(content)
    )


class TestDetectionRuns:
    def test_full_tier_runs_and_reports_severity(self):
        """FULL 档必须真跑；干净的正文 → severity=None 但 checked=True。"""
        out = _detect_hallucination_safely(
            _fusion(), organ="thyroid", request_id="r1", is_full_tier=True
        )

        assert out["checked"] is True
        assert out["severity"] is None, "干净正文不该被安上一个严重度"
        assert out["is_hallucination"] is False
        assert out["reason"] == ""

    def test_contradictory_grading_yields_severity(self):
        """正文里同时出现 TR2 与 TR5（矛盾分级）→ 必须报出非空严重度。"""
        out = _detect_hallucination_safely(
            _fusion("分级: TR2 ... 分级: TR5"),
            organ="thyroid",
            request_id="r2",
            is_full_tier=True,
        )

        assert out["checked"] is True
        assert out["severity"] in ("severe", "moderate"), (
            f"矛盾分级应报出严重度，实际 {out['severity']}"
        )
        assert out["issue_count"] >= 1

    def test_forced_switch_runs_even_outside_full_tier(self):
        out = _detect_hallucination_safely(
            _fusion(),
            organ="thyroid",
            request_id="r3",
            force_enabled=True,
            is_full_tier=False,
        )

        assert out["checked"] is True

    def test_non_full_tier_skips_with_reason(self):
        out = _detect_hallucination_safely(
            _fusion(), organ="thyroid", request_id="r4", is_full_tier=False
        )

        assert out["checked"] is False
        assert out["severity"] is None
        assert out["reason"] == "not_full_tier_and_switch_off"


class TestNotCheckedIsNotClean:
    """反证腿：`None` 有两种含义，必须能区分。"""

    @pytest.mark.parametrize(
        ("fusion", "organ", "expected_reason"),
        [
            (FusionResult(final_response=None), "thyroid", "no_final_response"),
            (_fusion("   "), "thyroid", "empty_text"),
            # 请求侧没给 organ，响应侧 organ 也是 None → 真的无从取词表
            (_fusion_without_organ(), None, "no_organ"),
        ],
    )
    def test_each_skip_has_its_own_reason(self, fusion, organ, expected_reason):
        out = _detect_hallucination_safely(
            fusion, organ=organ, request_id="r5", is_full_tier=True
        )

        assert out["checked"] is False
        assert out["severity"] is None
        assert out["reason"] == expected_reason

    def test_detector_failure_is_traced_not_raised(self, monkeypatch):
        import app.inference.hallucination_detector as hd

        broken = MagicMock()
        broken.detect = MagicMock(side_effect=RuntimeError("词表坏了"))
        monkeypatch.setattr(hd, "get_default_detector", MagicMock(return_value=broken))

        out = _detect_hallucination_safely(
            _fusion(), organ="thyroid", request_id="r6", is_full_tier=True
        )

        assert out["checked"] is False
        assert out["severity"] is None
        assert out["reason"] == "detector_failed: RuntimeError"


class TestWrittenIntoFindings:
    """核心接线：结果必须真的进 ``findings.fusion_result``。"""

    def test_checked_severity_is_written_with_schema_key_name(self):
        from app.services import diagnosis_pipeline as mod

        written: dict = {}

        class _Repo:
            def __init__(self, db):
                pass

            async def update_result(self, **kwargs):
                written.update(kwargs)

        class _Session:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *exc):
                return False

            async def commit(self):
                return None

        request = SimpleNamespace(organ="thyroid", request_id="req-hallu")
        fusion = _fusion("分级: TR2 ... 分级: TR5")
        original = mod.DiagnosisRepository
        original_session = mod.AsyncSessionLocal
        import asyncio

        mod.DiagnosisRepository = _Repo
        mod.AsyncSessionLocal = MagicMock(return_value=_Session())
        try:
            asyncio.get_event_loop_policy()
            ok = asyncio.run(
                mod._write_result_to_db(
                    request=request,
                    fusion_result=fusion,
                    sg_result={"passed": True},
                    sg_ok=True,
                    timings={},
                    hallucination={
                        "checked": True,
                        "severity": "severe",
                        "is_hallucination": True,
                        "issue_count": 1,
                        "confidence": 0.4,
                        "issues": [],
                    },
                )
            )
        finally:
            mod.DiagnosisRepository = original
            mod.AsyncSessionLocal = original_session

        assert ok is True
        fusion_payload = written["findings"]["fusion_result"]
        assert fusion_payload["hallucination_severity"] == "severe"
        assert fusion_payload["hallucination_checked"] is True
        assert fusion_payload["hallucination_is_hallucination"] is True
        assert fusion_payload["hallucination_issue_count"] == 1

    def test_not_checked_writes_none_and_reason_not_false(self):
        import asyncio

        from app.services import diagnosis_pipeline as mod

        written: dict = {}

        class _Repo:
            def __init__(self, db):
                pass

            async def update_result(self, **kwargs):
                written.update(kwargs)

        class _Session:
            async def __aenter__(self):
                return _Session()

            async def __aexit__(self, *exc):
                return False

            async def commit(self):
                return None

        request = SimpleNamespace(organ="thyroid", request_id="req-hallu2")
        original = mod.DiagnosisRepository
        original_session = mod.AsyncSessionLocal
        mod.DiagnosisRepository = _Repo
        mod.AsyncSessionLocal = MagicMock(return_value=_Session())
        try:
            asyncio.run(
                mod._write_result_to_db(
                    request=request,
                    fusion_result=_fusion(),
                    sg_result={"passed": True},
                    sg_ok=True,
                    timings={},
                    hallucination={
                        "checked": False,
                        "severity": None,
                        "reason": "not_full_tier_and_switch_off",
                    },
                )
            )
        finally:
            mod.DiagnosisRepository = original
            mod.AsyncSessionLocal = original_session

        fusion_payload = written["findings"]["fusion_result"]
        assert fusion_payload["hallucination_severity"] is None
        assert fusion_payload["hallucination_checked"] is False
        assert (
            fusion_payload["hallucination_reason"] == "not_full_tier_and_switch_off"
        )
        # "没评估"不得写出任何"评估过"的细节
        assert "hallucination_issue_count" not in fusion_payload
        assert "hallucination_is_hallucination" not in fusion_payload

    def test_t2a_and_hallucination_are_independent_keys(self):
        """两个新块的键必须各自独立（一个缺席不影响另一个）。"""
        payload = _fusion_result_to_dict(_fusion())

        assert "hallucination_severity" not in payload, (
            "融合结果本身不含幻觉字段时，不得凭空虚写一个键（写入侧才注入）"
        )
