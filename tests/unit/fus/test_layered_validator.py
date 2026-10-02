"""Unit tests for app.fusion.layered_validator (T-GA-FUS-01 分层校验).

覆盖：
- Layer 1 冲突检测 → needs_review=True；
- Layer 2 一致性 critical → needs_review=True；
- Layer 3 Self-RAG 失败 → needs_review=True（review_reason=self_rag_failed）；
- 三层全部通过 → needs_review=False；
- 边界：组件异常 → 不抛错，标记 needs_review。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_fus]

from app.fusion.conflict import ConflictDetector
from app.fusion.consistency import ConsistencyChecker
from app.fusion.layered_validator import LayeredValidator
from app.fusion.protocols import (
    FusionRequest,
)
from app.fusion.self_rag import SelfRAGEvaluator
from app.models.schemas import Organ
from tests.unit.fus.conftest import make_response

# ======================================================================
# Layer 1: 冲突检测
# ======================================================================


@pytest.mark.asyncio
async def test_layer1_conflict_triggers_review(
    consistency_checker,
    self_rag_evaluator_heuristic,
):
    """同器官不同分级 → needs_review=True, review_reason=conflict_detected。"""
    validator = LayeredValidator(
        conflict_detector=ConflictDetector(),
        consistency_checker=consistency_checker,
        self_rag_evaluator=self_rag_evaluator_heuristic,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(agent_name="a", organ=Organ.THYROID, content="TR3"),
            make_response(agent_name="b", organ=Organ.THYROID, content="TR4"),
        ]
    )
    result = await validator.validate(request)
    assert result.needs_review is True
    assert result.review_reason == "conflict_detected"


@pytest.mark.asyncio
async def test_layer1_no_conflict_continues(
    consistency_checker,
    self_rag_evaluator_heuristic,
):
    """无冲突 → 继续到下一层（一致性）。"""
    validator = LayeredValidator(
        conflict_detector=ConflictDetector(),
        consistency_checker=consistency_checker,
        self_rag_evaluator=self_rag_evaluator_heuristic,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.9),
        ]
    )
    result = await validator.validate(request)
    # 单响应高 conf → 无冲突，无一致性 critical
    assert result.conflict_report.has_conflict is False


# ======================================================================
# Layer 2: 一致性 critical
# ======================================================================


@pytest.mark.asyncio
async def test_layer2_consistency_critical_triggers_review(
    conflict_detector,
    self_rag_evaluator_heuristic,
):
    """一致性 critical → needs_review=True。"""
    validator = LayeredValidator(
        conflict_detector=conflict_detector,
        consistency_checker=ConsistencyChecker(rag_service=None),
        self_rag_evaluator=self_rag_evaluator_heuristic,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
        ],
        prior_diagnosis={
            "organ": "thyroid",
            "grade": "TR5",
            "confidence": 0.85,
        },  # 差 2 档
    )
    result = await validator.validate(request)
    assert result.needs_review is True
    assert result.review_reason == "consistency_failed"


@pytest.mark.asyncio
async def test_layer2_consistency_warning_continues(
    conflict_detector,
    self_rag_evaluator_heuristic,
):
    """一致性 WARNING（< critical）→ 继续 Layer 3。"""
    validator = LayeredValidator(
        conflict_detector=conflict_detector,
        consistency_checker=ConsistencyChecker(rag_service=None),
        self_rag_evaluator=self_rag_evaluator_heuristic,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
        ],
        prior_diagnosis={
            "organ": "thyroid",
            "grade": "TR3",
            "confidence": 0.5,  # 仅 confidence 差 0.35（warning）
        },
    )
    result = await validator.validate(request)
    # Layer 2 WARNING 不阻断（critical 才会）
    # Layer 3 Self-RAG 启发式：conf=0.85 + evidence 通过 → passed
    assert result.review_reason != "consistency_failed"


# ======================================================================
# Layer 3: Self-RAG
# ======================================================================


@pytest.mark.asyncio
async def test_layer3_self_rag_fail_triggers_review(
    conflict_detector,
    consistency_checker,
):
    """Self-RAG 失败 → needs_review=True, review_reason=self_rag_failed。"""
    # 构造一个强制失败的 Self-RAG（conf=0.0 + 空 evidence + 空 content）
    bad_self_rag = SelfRAGEvaluator(inference_engine=None)
    validator = LayeredValidator(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=bad_self_rag,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID,
                content="",  # 空 content → 启发式失败
                confidence=0.0,
                evidence=[],
            ),
        ],
    )
    result = await validator.validate(request)
    assert result.needs_review is True
    assert result.review_reason == "self_rag_failed"
    assert result.self_rag_passed is False


@pytest.mark.asyncio
async def test_layer3_self_rag_pass_no_review(
    conflict_detector,
    consistency_checker,
):
    """Self-RAG 通过 + 无冲突 + 一致性 OK → needs_review=False。"""
    validator = LayeredValidator(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID,
                content="TR3 边界清晰",
                confidence=0.9,
                evidence=["c1", "c2", "c3"],
            ),
        ],
    )
    result = await validator.validate(request)
    assert result.needs_review is False
    assert result.self_rag_passed is True


@pytest.mark.asyncio
async def test_layer3_no_self_rag_evaluator_continues(
    conflict_detector,
    consistency_checker,
):
    """self_rag_evaluator=None → 跳过 Layer 3。"""
    validator = LayeredValidator(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=None,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.9),
        ],
    )
    result = await validator.validate(request)
    # self_rag_passed 未设置（None）
    assert result.self_rag_passed is None


# ======================================================================
# 边界 / 异常
# ======================================================================


@pytest.mark.asyncio
async def test_layered_validator_constructor_validates_args():
    """构造器必须传入非 None 组件。"""
    with pytest.raises(ValueError):
        LayeredValidator(
            conflict_detector=None,  # type: ignore[arg-type]
            consistency_checker=ConsistencyChecker(),
        )
    with pytest.raises(ValueError):
        LayeredValidator(
            conflict_detector=ConflictDetector(),
            consistency_checker=None,  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_layered_validator_latency_measured(
    conflict_detector,
    consistency_checker,
    self_rag_evaluator_heuristic,
):
    """total_latency_ms 应被设置（>= 0）。"""
    validator = LayeredValidator(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=self_rag_evaluator_heuristic,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.9),
        ],
    )
    result = await validator.validate(request)
    assert result.total_latency_ms >= 0.0
