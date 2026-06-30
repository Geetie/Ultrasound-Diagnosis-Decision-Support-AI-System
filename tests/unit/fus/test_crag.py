"""Unit tests for app.fusion.crag (T-GA-FUS-02 CRAG 补充检索).

覆盖：
- 成功恢复：RAG 返回新证据 → recovered=True, new_response 正确；
- 失败兜底：RAG 返回空 / RAG 异常 / 无 RAG service；
- 失败原因分类（evidence_insufficient / evidence_contradictory /
  feature_missing / unknown）。
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_fus]

from app.fusion.crag import CRAGHandler
from app.fusion.self_rag import SelfRAGEvalResult
from app.fusion.protocols import FusionRequest
from app.models.schemas import Organ
from tests.unit.fus.conftest import (
    FakeRAGService,
    make_response,
    make_scored,
)


# ======================================================================
# 成功恢复
# ======================================================================


@pytest.mark.asyncio
async def test_crag_recovered_with_new_chunks(crag_handler_with_chunks):
    """RAG 返回 3 条新证据 → recovered=True, new_response 包含新证据。"""
    resp = make_response(
        organ=Organ.THYROID,
        content="TR3",
        confidence=0.7,
        evidence=["old_c1"],
    )
    self_rag_result = SelfRAGEvalResult(
        passed=False,
        score=2.0,
        evidence_sufficiency=1.0,
        reason="self_rag_failed",
    )
    request = FusionRequest(
        agent_responses=[resp],
        request_id="test_req",
        session_id="test_sess",
        clinical_context={"user_query": "thyroid TR3 diagnosis"},
    )
    result = await crag_handler_with_chunks.handle(resp, self_rag_result, request)
    assert result.recovered is True
    assert result.new_response is not None
    # 合并证据：旧 + 新
    assert "old_c1" in result.new_response.evidence_chunk_ids
    assert "new_c1" in result.new_response.evidence_chunk_ids
    # confidence 应提升
    assert result.new_response.confidence > resp.confidence
    # trace 追加
    assert any("crag_recovery" in t for t in result.new_response.reasoning_trace)


@pytest.mark.asyncio
async def test_crag_evidence_dedup(crag_handler_with_chunks):
    """合并证据时去重（旧 + 新均不重复）。"""
    resp = make_response(
        organ=Organ.THYROID,
        content="TR3",
        confidence=0.7,
        evidence=["old_c1", "new_c1"],  # new_c1 已存在
    )
    self_rag_result = SelfRAGEvalResult(passed=False, score=0.0)
    request = FusionRequest(agent_responses=[resp])
    result = await crag_handler_with_chunks.handle(resp, self_rag_result, request)
    assert result.recovered is True
    evidence = result.new_response.evidence_chunk_ids
    # 验证去重
    assert len(evidence) == len(set(evidence))


@pytest.mark.asyncio
async def test_crag_confidence_boost_capped():
    """confidence 提升有上限（不超过 1.0）。"""
    # 配置一个 1.0 confidence 的响应 + 新证据
    chunks = [make_scored("c_new", score=0.9)]
    handler = CRAGHandler(rag_service=FakeRAGService(chunks=chunks))
    resp = make_response(
        organ=Organ.THYROID,
        content="TR3",
        confidence=0.95,  # 0.95 + 0.2 = 1.15 → cap at 1.0
    )
    self_rag_result = SelfRAGEvalResult(passed=False, score=0.0)
    request = FusionRequest(agent_responses=[resp])
    result = await handler.handle(resp, self_rag_result, request)
    assert result.recovered is True
    assert result.new_response.confidence == 1.0  # 截断


# ======================================================================
# 失败兜底
# ======================================================================


@pytest.mark.asyncio
async def test_crag_no_rag_service():
    """RAG Service=None → recovered=False, reason="rag_unavailable"。"""
    handler = CRAGHandler(rag_service=None)
    resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)
    self_rag_result = SelfRAGEvalResult(passed=False, score=0.0)
    request = FusionRequest(agent_responses=[resp])
    result = await handler.handle(resp, self_rag_result, request)
    assert result.recovered is False
    assert "rag_unavailable" in result.reason


@pytest.mark.asyncio
async def test_crag_rag_returns_empty():
    """RAG 返回空 → recovered=False, reason="crag_retrieval_returned_empty"。"""
    handler = CRAGHandler(rag_service=FakeRAGService(chunks=[]))
    resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)
    self_rag_result = SelfRAGEvalResult(passed=False, score=0.0)
    request = FusionRequest(agent_responses=[resp])
    result = await handler.handle(resp, self_rag_result, request)
    assert result.recovered is False
    assert "empty" in result.reason


@pytest.mark.asyncio
async def test_crag_rag_raises():
    """RAG 抛异常 → recovered=False, reason 包含异常类型。"""
    handler = CRAGHandler(
        rag_service=FakeRAGService(raise_exc=RuntimeError("rag down"))
    )
    resp = make_response(organ=Organ.THYROID, content="TR3", confidence=0.7)
    self_rag_result = SelfRAGEvalResult(passed=False, score=0.0)
    request = FusionRequest(agent_responses=[resp])
    result = await handler.handle(resp, self_rag_result, request)
    assert result.recovered is False
    assert "rag_failed" in result.reason
    assert "RuntimeError" in result.reason


# ======================================================================
# 失败原因分类
# ======================================================================


def test_crag_classify_failure_insufficient():
    """evidence_sufficiency < 2 → evidence_insufficient。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    result = SelfRAGEvalResult(
        passed=False, score=1.0, evidence_sufficiency=1.0
    )
    assert handler._classify_failure(result) == "evidence_insufficient"


def test_crag_classify_failure_contradictory():
    """evidence_consistency < 2 → evidence_contradictory。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    result = SelfRAGEvalResult(
        passed=False,
        score=1.0,
        evidence_sufficiency=3.0,
        evidence_consistency=1.0,
    )
    assert handler._classify_failure(result) == "evidence_contradictory"


def test_crag_classify_failure_feature_missing():
    """feature_completeness < 2 → feature_missing。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    result = SelfRAGEvalResult(
        passed=False,
        score=1.0,
        evidence_sufficiency=3.0,
        evidence_consistency=3.0,
        feature_completeness=1.0,
    )
    assert handler._classify_failure(result) == "feature_missing"


def test_crag_classify_failure_unknown():
    """无明确信号（所有维度都 ≥ 2）→ unknown。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    result = SelfRAGEvalResult(
        passed=False,
        score=2.0,
        evidence_sufficiency=3.0,  # 全部 ≥ 2.0
        evidence_consistency=3.0,
        feature_completeness=3.0,
        reason="some random reason",
    )
    assert handler._classify_failure(result) == "unknown"


# ======================================================================
# Query 构造
# ======================================================================


def test_crag_enhanced_query_with_user_query():
    """优先使用 clinical_context.user_query 作为 base。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    request = FusionRequest(
        agent_responses=[],
        clinical_context={"user_query": "thyroid TR3 边界"},
    )
    resp = make_response(organ=Organ.THYROID, content="TR3")
    query = handler._build_enhanced_query(request, resp, "evidence_insufficient")
    assert "thyroid TR3 边界" in query
    assert "证据" in query or "需要" in query


def test_crag_enhanced_query_fallback_to_content():
    """无 user_query → fallback 到 response.content。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    request = FusionRequest(agent_responses=[])
    resp = make_response(organ=Organ.THYROID, content="TR3 fallback")
    query = handler._build_enhanced_query(request, resp, "unknown")
    assert "TR3 fallback" in query


# ======================================================================
# 边界 / 校验
# ======================================================================


def test_crag_top_k_validation():
    """crag_top_k 必须 > 0。"""
    with pytest.raises(ValueError):
        CRAGHandler(rag_service=FakeRAGService(), crag_top_k=0)


def test_crag_default_top_k():
    """默认 top_k = 20（验收要求：CRAG 触发时扩大 top_k）。"""
    handler = CRAGHandler(rag_service=FakeRAGService())
    assert handler._crag_top_k == 20
