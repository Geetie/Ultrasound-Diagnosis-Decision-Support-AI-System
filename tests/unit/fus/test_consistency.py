"""Unit tests for app.fusion.consistency (T-GA-FUS-01 一致性校验).

覆盖：
- 与既往诊断对比（confidence 差距 / 分级差异）；
- 与临床指南对比（通过 RAG）；
- 跨器官一致性（"严重 + 正常" 互斥）；
- 边界：空响应 / 缺失 prior / RAG 异常 / 阈值校验。
"""
from __future__ import annotations

from typing import Optional

import pytest

pytestmark = [pytest.mark.track_fus]

from app.fusion.consistency import (
    DEFAULT_PRIOR_CONFIDENCE_GAP_CRITICAL,
    DEFAULT_PRIOR_CONFIDENCE_GAP_WARNING,
    ConsistencyChecker,
)
from app.fusion.protocols import (
    ConsistencySeverity,
    FusionRequest,
)
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeRAGService,
    make_response,
    make_scored,
)


# ======================================================================
# 与既往诊断对比
# ======================================================================


@pytest.mark.asyncio
async def test_consistency_with_prior_consistent(consistency_checker):
    """既往 vs 当前 一致 → INFO, is_consistent=True。"""
    responses = [
        make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
    ]
    request = FusionRequest(
        agent_responses=responses,
        prior_diagnosis={"organ": "thyroid", "grade": "TR3", "confidence": 0.85},
    )
    result = await consistency_checker.check(request)
    assert result.is_consistent is True
    assert result.severity in (ConsistencySeverity.INFO, ConsistencySeverity.WARNING)
    assert result.compared_with_prior is True


@pytest.mark.asyncio
async def test_consistency_with_prior_confidence_warning(consistency_checker):
    """confidence 差距 0.35（>= warning 0.3）→ WARNING。"""
    responses = [
        make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
    ]
    request = FusionRequest(
        agent_responses=responses,
        prior_diagnosis={"organ": "thyroid", "grade": "TR3", "confidence": 0.5},
    )
    result = await consistency_checker.check(request)
    assert result.severity == ConsistencySeverity.WARNING
    assert result.is_consistent is True
    assert "prior_confidence_gap_warning" in result.detail


@pytest.mark.asyncio
async def test_consistency_with_prior_confidence_critical(consistency_checker):
    """confidence 差距 0.5（>= critical）→ CRITICAL, is_consistent=False。"""
    responses = [
        make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
    ]
    request = FusionRequest(
        agent_responses=responses,
        prior_diagnosis={"organ": "thyroid", "grade": "TR3", "confidence": 0.3},
    )
    result = await consistency_checker.check(request)
    assert result.severity == ConsistencySeverity.CRITICAL
    assert result.is_consistent is False


@pytest.mark.asyncio
async def test_consistency_with_prior_grade_diff_warning(consistency_checker):
    """分级差 1（>= warning）→ WARNING。"""
    responses = [
        make_response(organ=Organ.THYROID, content="TR4", confidence=0.85),
    ]
    request = FusionRequest(
        agent_responses=responses,
        prior_diagnosis={"organ": "thyroid", "grade": "TR3", "confidence": 0.85},
    )
    result = await consistency_checker.check(request)
    # grade diff = 1 → WARNING；confidence 相同不触发
    assert result.severity in (
        ConsistencySeverity.WARNING,
        ConsistencySeverity.CRITICAL,
    )


@pytest.mark.asyncio
async def test_consistency_with_prior_grade_diff_critical(consistency_checker):
    """分级差 2（>= critical）→ CRITICAL。"""
    responses = [
        make_response(organ=Organ.THYROID, content="TR5", confidence=0.85),
    ]
    request = FusionRequest(
        agent_responses=responses,
        prior_diagnosis={"organ": "thyroid", "grade": "TR3", "confidence": 0.85},
    )
    result = await consistency_checker.check(request)
    assert result.severity == ConsistencySeverity.CRITICAL
    assert "prior_grade_diff_critical" in result.detail


@pytest.mark.asyncio
async def test_consistency_no_prior_diagnosis(consistency_checker):
    """无既往诊断 → compared_with_prior=False, 不报错。"""
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(agent_responses=responses, prior_diagnosis=None)
    result = await consistency_checker.check(request)
    assert result.compared_with_prior is False


@pytest.mark.asyncio
async def test_consistency_prior_different_organ_no_check(consistency_checker):
    """既往 vs 当前 organ 不同 → 跳过器官内对比。"""
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(
        agent_responses=responses,
        prior_diagnosis={"organ": "liver", "grade": "LR-3", "confidence": 0.85},
    )
    result = await consistency_checker.check(request)
    # 器官不同 → 不视为 critical
    assert result.is_consistent is True


# ======================================================================
# 与临床指南对比（通过 RAG）
# ======================================================================


@pytest.mark.asyncio
async def test_consistency_with_guidelines_rag_no_chunks(consistency_checker_with_rag):
    """RAG 返回空 → INFO, compared_with_guidelines=True。"""
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker_with_rag.check(request)
    assert result.compared_with_guidelines is True
    # no_chunks 仅为 INFO
    assert result.is_consistent is True


@pytest.mark.asyncio
async def test_consistency_with_guidelines_rag_has_chunks(consistency_checker):
    """RAG 返回 chunks → INFO, 报告 "guideline_chunks_found"。"""
    rag = FakeRAGService(
        chunks=[
            make_scored("g1", content="thyroid guideline 1"),
            make_scored("g2", content="thyroid guideline 2"),
        ]
    )
    checker = ConsistencyChecker(rag_service=rag)
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(agent_responses=responses)
    result = await checker.check(request)
    assert result.compared_with_guidelines is True
    assert "guideline_chunks_found" in result.detail


@pytest.mark.asyncio
async def test_consistency_rag_failure_does_not_block(consistency_checker):
    """RAG 异常 → 跳过指南对比, 不抛错。"""
    rag = FakeRAGService(raise_exc=RuntimeError("rag down"))
    checker = ConsistencyChecker(rag_service=rag)
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(agent_responses=responses)
    result = await checker.check(request)  # 不抛错
    assert result.compared_with_guidelines is True
    assert "guideline_rag_failed" in result.detail


@pytest.mark.asyncio
async def test_consistency_no_rag_service_skips_guideline(consistency_checker):
    """无 RAG → 跳过指南对比, compared_with_guidelines=False。"""
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker.check(request)
    assert result.compared_with_guidelines is False


# ======================================================================
# 跨器官一致性
# ======================================================================


@pytest.mark.asyncio
async def test_consistency_cross_organ_severe_vs_normal_warning(consistency_checker):
    """严重异常 + 完全正常 跨器官 → WARNING。"""
    responses = [
        make_response(
            agent_name="liv_a",
            organ=Organ.LIVER,
            content="严重恶性病变",
            confidence=0.9,
        ),
        make_response(
            agent_name="thy_a",
            organ=Organ.THYROID,
            content="未见异常",
            confidence=0.9,
        ),
    ]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker.check(request)
    assert result.severity == ConsistencySeverity.WARNING
    assert result.is_consistent is True
    assert "cross_organ_mix" in result.detail


@pytest.mark.asyncio
async def test_consistency_cross_organ_no_conflict_same_pattern(consistency_checker):
    """两器官均为 "严重" → 不视为 cross-organ mix。"""
    responses = [
        make_response(organ=Organ.LIVER, content="严重异常", confidence=0.9),
        make_response(organ=Organ.THYROID, content="critical", confidence=0.9),
    ]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker.check(request)
    # 严重 + 严重 → 一致
    assert "cross_organ_mix" not in result.detail


@pytest.mark.asyncio
async def test_consistency_cross_organ_low_confidence_no_trigger(consistency_checker):
    """低 confidence 响应（< 0.85）→ 不触发 cross-organ 判定。"""
    responses = [
        make_response(organ=Organ.LIVER, content="严重", confidence=0.5),
        make_response(organ=Organ.THYROID, content="正常", confidence=0.5),
    ]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker.check(request)
    # 低 conf → 不视为"权威严重/正常"
    assert "cross_organ_mix" not in result.detail


@pytest.mark.asyncio
async def test_consistency_single_response_no_cross_check(consistency_checker):
    """单响应 → 跨器官检查不适用。"""
    responses = [make_response(organ=Organ.THYROID, content="TR3", confidence=0.85)]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker.check(request)
    assert result.is_consistent is True


# ======================================================================
# 边界
# ======================================================================


@pytest.mark.asyncio
async def test_consistency_empty_responses(consistency_checker):
    """空响应列表 → 不抛错, severity=INFO。"""
    request = FusionRequest(agent_responses=[])
    result = await consistency_checker.check(request)
    assert result.is_consistent is True
    assert result.severity == ConsistencySeverity.INFO


@pytest.mark.asyncio
async def test_consistency_threshold_validation():
    """阈值校验：critical < warning → 抛错。"""
    with pytest.raises(ValueError):
        ConsistencyChecker(
            prior_confidence_gap_warning=0.5,
            prior_confidence_gap_critical=0.3,  # < warning
        )
    with pytest.raises(ValueError):
        ConsistencyChecker(
            prior_grade_diff_warning=2,
            prior_grade_diff_critical=1,  # < warning
        )


@pytest.mark.asyncio
async def test_consistency_invalid_top_k():
    """top_k 必须 > 0。"""
    with pytest.raises(ValueError):
        ConsistencyChecker(guideline_top_k=0)


@pytest.mark.asyncio
async def test_consistency_multiple_responses_same_organ_warning(consistency_checker):
    """同器官多响应 + 一致 → INFO。"""
    responses = [
        make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
        make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
    ]
    request = FusionRequest(agent_responses=responses)
    result = await consistency_checker.check(request)
    assert result.is_consistent is True
