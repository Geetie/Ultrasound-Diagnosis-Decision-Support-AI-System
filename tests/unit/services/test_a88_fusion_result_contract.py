"""A.88 A3 守卫：结果契约不再说谎 —— 融合层真实信号必须写进 findings。

## 缺陷

``_fusion_result_to_dict`` 只搬运 ``needs_review`` / ``self_rag_passed`` 等少数键，
``conflict_report`` 与 ``consistency_result`` 里的判定整包丢弃 → 结果接口无法
知道"到底比没比过、有没有冲突、一致性多严重"，只能写死常量：

    consistency_flag=ConsistencyFlag.CONSISTENT   # 恒"一致"
    fusion_status=FusionStatus.NORMAL             # 恒"正常"

一致性校验在生产上一次都没跑过（A2 未接线 + A1 量纲错配让指南对比拿不到
chunk），契约却始终声明"一致" —— 医生据此会跳过复核。这与 2026-09-28 接线
审计修的 `self_rag_passed=False` / `consistency_check_passed=True` 是同一族：
**"未评估"被表达成"评估通过"**。

## 本文件钉住的键（Lead 侧前端/探针按这些名字读，不得改名）

``conflict_detected`` / ``conflict_types`` / ``consistency_severity`` /
``compared_with_prior`` / ``compared_with_guidelines`` / ``validate_degraded``

## 语义红线

* 只搬运、不推导：拿不到就**不写这个键**（"未评估"必须能与"评估过且通过"区分）；
* 对象与 dict 两种形态都要认（写路径拿对象、读路径拿落库后的 dict）。

反证腿：把新增键删掉、或把缺失时改成写死 False，本文件的用例即红。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.protocols import (
    ConflictReport,
    ConflictType,
    ConsistencyCheckResult,
    ConsistencySeverity,
    FusionResult,
)
from app.services.diagnosis_pipeline import _fusion_result_to_dict

pytestmark = [pytest.mark.track_fus]


def _final(content: str = "分级: TR3", confidence: float = 0.82) -> AgentResponse:
    return AgentResponse(
        agent_name="thyroid_specialist",
        content=content,
        confidence=confidence,
    )


class TestConflictSignals:
    """冲突检测的真实结果必须可见。"""

    def test_conflict_fields_are_carried(self):
        fusion = FusionResult(
            final_response=_final(),
            conflict_report=ConflictReport(
                has_conflict=True,
                conflict_types=[
                    ConflictType.GRADING_CONFLICT,
                    ConflictType.CONFIDENCE_CONFLICT,
                ],
            ),
        )

        payload = _fusion_result_to_dict(fusion)

        assert payload["conflict_detected"] is True
        assert payload["conflict_types"] == [
            "grading_conflict",
            "confidence_conflict",
        ]

    def test_no_conflict_is_false_not_missing(self):
        """真的跑过且没冲突 → False（这与"没跑过"不同，必须可区分）。"""
        fusion = FusionResult(
            final_response=_final(),
            conflict_report=ConflictReport(has_conflict=False),
        )

        payload = _fusion_result_to_dict(fusion)

        assert payload["conflict_detected"] is False
        assert payload["conflict_types"] == []


class TestConsistencySignals:
    """一致性校验的真实结果必须可见（"比过没有"是两个独立布尔）。"""

    def test_full_consistency_payload(self):
        fusion = FusionResult(
            final_response=_final(),
            consistency_result=ConsistencyCheckResult(
                is_consistent=False,
                severity=ConsistencySeverity.CRITICAL,
                compared_with_prior=True,
                compared_with_guidelines=True,
                detail="prior_grade_diff_critical",
            ),
        )

        payload = _fusion_result_to_dict(fusion)

        assert payload["consistency_severity"] == "critical"
        assert payload["compared_with_prior"] is True
        assert payload["compared_with_guidelines"] is True

    def test_never_compared_writes_false(self):
        """两路都没比过 → 两个键都是 False（供接口判"未评估"）。"""
        fusion = FusionResult(
            final_response=_final(),
            consistency_result=ConsistencyCheckResult(),
        )

        payload = _fusion_result_to_dict(fusion)

        assert payload["compared_with_prior"] is False
        assert payload["compared_with_guidelines"] is False
        assert payload["consistency_severity"] == "info"

    def test_default_fusion_result_reports_never_compared(self):
        """默认构造（无 validator 写入）不得自称"比过"。"""
        payload = _fusion_result_to_dict(FusionResult(final_response=_final()))

        assert payload["compared_with_prior"] is False
        assert payload["compared_with_guidelines"] is False


class TestValidateDegraded:
    def test_degraded_flag_is_carried(self):
        fusion = FusionResult(final_response=_final(), validate_degraded=True)
        assert _fusion_result_to_dict(fusion)["validate_degraded"] is True

    def test_not_degraded_is_false(self):
        fusion = FusionResult(final_response=_final(), validate_degraded=False)
        assert _fusion_result_to_dict(fusion)["validate_degraded"] is False


class TestDictFormIsUnderstood:
    """读路径拿到的是落库后的 dict —— 同一种语义不能只认对象。"""

    def test_dict_payload_keys_are_read(self):
        payload_in = {
            "needs_review": False,
            "conflict_report": {
                "has_conflict": True,
                "conflict_types": ["evidence_conflict"],
            },
            "consistency_result": {
                "severity": "warning",
                "compared_with_prior": True,
                "compared_with_guidelines": False,
            },
            "validate_degraded": True,
        }

        payload = _fusion_result_to_dict(payload_in)

        assert payload["conflict_detected"] is True
        assert payload["conflict_types"] == ["evidence_conflict"]
        assert payload["consistency_severity"] == "warning"
        assert payload["compared_with_prior"] is True
        assert payload["compared_with_guidelines"] is False
        assert payload["validate_degraded"] is True


class TestNotFabricated:
    """红线：拿不到的东西不许编。"""

    def test_none_returns_empty_dict(self):
        assert _fusion_result_to_dict(None) == {}

    def test_missing_consistency_result_writes_no_claim(self):
        """没有 consistency_result（例如融合失败占位）→ 不写"比过/没比过"。"""
        fusion = SimpleNamespace(
            final_response=_final(),
            needs_review=True,
            review_reason="fusion_layer_failed",
        )

        payload = _fusion_result_to_dict(fusion)

        assert "compared_with_prior" not in payload
        assert "compared_with_guidelines" not in payload
        assert "consistency_severity" not in payload

    def test_non_bool_compared_flags_are_not_coerced(self):
        """类型不对的"比过"标记不得被 bool() 强转成 True/False 假声明。"""
        fusion = SimpleNamespace(
            final_response=_final(),
            consistency_result=SimpleNamespace(
                severity="info",
                compared_with_prior=None,
                compared_with_guidelines="yes",
            ),
        )

        payload = _fusion_result_to_dict(fusion)

        assert "compared_with_prior" not in payload
        assert "compared_with_guidelines" not in payload

    def test_preserves_existing_keys(self):
        """既有键一个都不能少（本批只增不改）。"""
        payload = _fusion_result_to_dict(FusionResult(final_response=_final()))

        for key in (
            "needs_review",
            "review_reason",
            "self_rag_passed",
            "crag_triggered",
            "crag_recovered",
            "total_latency_ms",
            "final_confidence",
            "content",
        ):
            assert key in payload, f"既有键 {key} 丢失"
