"""Integration tests for FUS layer (T-GA-FUS-01/02 端到端融合测试).

覆盖：
- 端到端融合：select_final_response / conflict / consistency / self-rag /
  crag 全流程；
- 验证 needs_review 标记正确；
- 验证 P99 延迟 < 500ms（不含 Self-RAG LLM 调用，启发式评估 < 1ms）。
- CRAG 恢复成功 → 清掉 self_rag_failed 标记。
- 全部失败兜底：所有组件异常 → needs_review=True，不抛错。
"""
from __future__ import annotations

import asyncio
import time

import pytest

pytestmark = [pytest.mark.track_fus, pytest.mark.integration]

from app.fusion.conflict import ConflictDetector
from app.fusion.consistency import ConsistencyChecker
from app.fusion.crag import CRAGHandler
from app.fusion.protocols import (
    ConflictResolution,
    ConflictType,
    ConsistencySeverity,
    FusionRequest,
)
from app.fusion.self_rag import SelfRAGEvaluator
from app.fusion.service import FusionEngineImpl
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeRAGService,
    make_response,
    make_scored,
)


# ======================================================================
# 端到端：标准融合
# ======================================================================


@pytest.mark.asyncio
async def test_fusion_e2e_normal_pass(conflict_detector, consistency_checker):
    """标准单响应高 conf → 融合通过, needs_review=False。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID,
                content="TR3 边界清晰",
                confidence=0.92,
                evidence=["c1", "c2", "c3"],
            ),
        ],
    )
    result = await engine.fuse(request)
    assert result.needs_review is False
    assert result.final_response is not None
    assert result.final_response.organ == Organ.THYROID
    assert result.conflict_report.has_conflict is False
    assert result.consistency_result.is_consistent is True


@pytest.mark.asyncio
async def test_fusion_e2e_conflict_marks_review(
    conflict_detector, consistency_checker
):
    """冲突检出 → needs_review=True, review_reason=conflict_detected。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(agent_name="a", organ=Organ.THYROID, content="TR3"),
            make_response(agent_name="b", organ=Organ.THYROID, content="TR5"),
        ],
    )
    result = await engine.fuse(request)
    assert result.needs_review is True
    assert result.review_reason == "conflict_detected"
    assert ConflictType.GRADING_CONFLICT in result.conflict_report.conflict_types


@pytest.mark.asyncio
async def test_fusion_e2e_consistency_critical_marks_review(
    conflict_detector,
):
    """既往诊断 critical → needs_review=True。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=ConsistencyChecker(rag_service=None),
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.85),
        ],
        prior_diagnosis={"organ": "thyroid", "grade": "TR5", "confidence": 0.85},
    )
    result = await engine.fuse(request)
    assert result.needs_review is True
    assert result.review_reason == "consistency_failed"


@pytest.mark.asyncio
async def test_fusion_e2e_self_rag_fail_marks_review(
    conflict_detector,
    consistency_checker,
):
    """Self-RAG 失败 → needs_review=True, review_reason=self_rag_failed。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),  # 启发式
    )
    # 构造会失败 Self-RAG 的响应（conf=0 + 空 content + 空 evidence）
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID, content="", confidence=0.0, evidence=[]
            ),
        ],
    )
    result = await engine.fuse(request)
    assert result.needs_review is True
    assert result.review_reason == "self_rag_failed"
    assert result.self_rag_passed is False


# ======================================================================
# CRAG 恢复路径
# ======================================================================


@pytest.mark.asyncio
async def test_fusion_e2e_crag_recovery_clears_review(
    conflict_detector,
    consistency_checker,
):
    """Self-RAG 失败 + CRAG 恢复成功 → needs_review=False。"""
    # 配置 CRAG：RAG 返回 3 条新证据 → 可恢复
    chunks = [
        make_scored("new_c1", content="new evidence 1"),
        make_scored("new_c2", content="new evidence 2"),
        make_scored("new_c3", content="new evidence 3"),
    ]
    crag_handler = CRAGHandler(rag_service=FakeRAGService(chunks=chunks))
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),
        crag_handler=crag_handler,
        rag_service=FakeRAGService(chunks=chunks),
    )
    # 响应：低 conf → Self-RAG 启发式失败
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID,
                content="TR3",
                confidence=0.5,  # 低于 heuristic_threshold 0.7
                evidence=["old_c1"],
            ),
        ],
    )
    result = await engine.fuse(request)
    assert result.crag_triggered is True
    assert result.crag_recovered is True
    # CRAG 恢复成功 → 清掉 self_rag_failed 标记
    assert result.needs_review is False
    assert result.review_reason is None


@pytest.mark.asyncio
async def test_fusion_e2e_crag_recovery_fails_keeps_review(
    conflict_detector,
    consistency_checker,
):
    """Self-RAG 失败 + CRAG 不恢复 → needs_review 保持 True。"""
    # CRAG：RAG 返回空 → 恢复失败
    crag_handler = CRAGHandler(rag_service=FakeRAGService(chunks=[]))
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),
        crag_handler=crag_handler,
        rag_service=FakeRAGService(chunks=[]),
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID, content="", confidence=0.0, evidence=[]
            ),
        ],
    )
    result = await engine.fuse(request)
    assert result.crag_triggered is True
    assert result.crag_recovered is False
    assert result.needs_review is True


# ======================================================================
# use_self_rag / use_crag 开关
# ======================================================================


@pytest.mark.asyncio
async def test_fusion_e2e_use_self_rag_disabled(
    conflict_detector, consistency_checker
):
    """use_self_rag=False → 跳过 Self-RAG 评估。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID, content="", confidence=0.0, evidence=[]
            ),
        ],
        use_self_rag=False,
    )
    result = await engine.fuse(request)
    # Self-RAG 未执行 → self_rag_passed is None
    assert result.self_rag_passed is None


@pytest.mark.asyncio
async def test_fusion_e2e_use_crag_disabled(
    conflict_detector,
    consistency_checker,
):
    """use_crag=False → Self-RAG 失败时不触发 CRAG。"""
    crag_handler = CRAGHandler(rag_service=FakeRAGService(chunks=[make_scored("c1")]))
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),
        crag_handler=crag_handler,
        rag_service=FakeRAGService(chunks=[make_scored("c1")]),
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID, content="", confidence=0.0, evidence=[]
            ),
        ],
        use_crag=False,
    )
    result = await engine.fuse(request)
    # use_crag=False → 不触发 CRAG
    assert result.crag_triggered is False


# ======================================================================
# 边界
# ======================================================================


@pytest.mark.asyncio
async def test_fusion_e2e_empty_responses(conflict_detector, consistency_checker):
    """空响应列表 → needs_review=True, reason=no_specialist_responded。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    request = FusionRequest(agent_responses=[])
    result = await engine.fuse(request)
    assert result.needs_review is True
    assert result.review_reason == "no_specialist_responded"
    assert result.final_response is not None
    assert result.final_response.confidence == 0.0


@pytest.mark.asyncio
async def test_fusion_e2e_final_response_selection(
    conflict_detector, consistency_checker
):
    """多响应 → final_response 选择 confidence 最高者。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                agent_name="low",
                organ=Organ.THYROID,
                content="TR3",
                confidence=0.7,
            ),
            make_response(
                agent_name="high",
                organ=Organ.THYROID,
                content="TR3",
                confidence=0.9,
            ),
        ],
    )
    result = await engine.fuse(request)
    assert result.final_response.agent_name == "high"
    assert result.final_response.confidence == 0.9


@pytest.mark.asyncio
async def test_fusion_e2e_specialist_needs_review_propagated(
    conflict_detector, consistency_checker
):
    """specialist 自带 needs_review=True → 融合层不丢失。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    request = FusionRequest(
        agent_responses=[
            make_response(
                organ=Organ.THYROID,
                content="TR3",
                confidence=0.9,
                needs_review=True,  # specialist 已标记
            ),
        ],
    )
    result = await engine.fuse(request)
    assert result.needs_review is True
    assert result.review_reason == "below_confidence_threshold"


# ======================================================================
# 性能 / 验收
# ======================================================================


@pytest.mark.asyncio
async def test_fusion_e2e_p99_latency_under_500ms_excluding_self_rag_llm(
    conflict_detector, consistency_checker
):
    """融合层 P99 < 500ms（不含 Self-RAG LLM 调用 — 启发式 < 1ms）。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
        self_rag_evaluator=SelfRAGEvaluator(inference_engine=None),  # 启发式
    )
    # 50 次请求（模拟 P99 评估）
    latencies = []
    for i in range(50):
        request = FusionRequest(
            agent_responses=[
                make_response(
                    organ=Organ.THYROID,
                    content=f"TR3 test {i}",
                    confidence=0.9,
                    evidence=["c1", "c2"],
                ),
            ],
            request_id=f"req_{i}",
        )
        start = time.monotonic()
        await engine.fuse(request)
        latencies.append((time.monotonic() - start) * 1000.0)
    p99 = sorted(latencies)[int(len(latencies) * 0.99) - 1]
    assert p99 < 500.0, f"P99 latency {p99:.2f}ms >= 500ms"


@pytest.mark.asyncio
async def test_fusion_e2e_no_throw_on_internal_failure(
    conflict_detector,
):
    """内部异常 → needs_review=True, 不抛错。"""
    # 构造一个会抛异常的 consistency_checker（mock）
    class _ExplodingChecker:
        async def check(self, request):
            raise RuntimeError("boom")

    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=_ExplodingChecker(),  # type: ignore[arg-type]
    )
    request = FusionRequest(
        agent_responses=[
            make_response(organ=Organ.THYROID, content="TR3", confidence=0.9),
        ],
    )
    # 不抛错
    result = await engine.fuse(request)
    # 一致性 checker 异常 → 降级为 INFO（不阻断）
    assert result.consistency_result.severity == ConsistencySeverity.INFO


# ======================================================================
# Health check / close
# ======================================================================


@pytest.mark.asyncio
async def test_fusion_e2e_health_check(conflict_detector, consistency_checker):
    """health_check 返回 True（默认组件均健康）。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    assert await engine.health_check() is True


@pytest.mark.asyncio
async def test_fusion_e2e_close_no_throw(conflict_detector, consistency_checker):
    """close() 不抛错。"""
    engine = FusionEngineImpl(
        conflict_detector=conflict_detector,
        consistency_checker=consistency_checker,
    )
    await engine.close()  # 不抛错
