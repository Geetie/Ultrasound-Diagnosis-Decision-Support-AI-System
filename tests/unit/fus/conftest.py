"""Shared fixtures for FUS track tests (T-GA-FUS-01/02).

提供 FUS 测试套件共用的：
- AgentResponse 工厂函数
- ConflictDetector / ConsistencyChecker 默认实例
- SelfRAGEvaluator 启发式实例（无 LLM）
- 模拟 RAG Service
- 模拟 Inference Engine
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.protocols import AgentResponse
from app.fusion.conflict import ConflictDetector
from app.fusion.consistency import ConsistencyChecker
from app.fusion.crag import CRAGHandler
from app.fusion.self_rag import SelfRAGEvaluator
from app.fusion.service import FusionEngineImpl
from app.inference.protocols import (
    InferenceEngine,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    TokenUsage,
)
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ
from app.rag.protocols import (
    RAGService,
    RetrievalMethod,
    RetrievalRequest,
    RetrievalResult,
    ScoredChunk,
)


# ----------------------------------------------------------------------
# AgentResponse 工厂
# ----------------------------------------------------------------------


def make_response(
    agent_name: str = "thyroid_specialist",
    organ: Organ = Organ.THYROID,
    content: str = "TR3 边界清晰",
    confidence: float = 0.85,
    evidence: Optional[List[str]] = None,
    needs_review: bool = False,
    reasoning_trace: Optional[List[str]] = None,
) -> AgentResponse:
    """构造一个 AgentResponse（用于融合层测试）。"""
    return AgentResponse(
        agent_name=agent_name,
        organ=organ,
        content=content,
        confidence=confidence,
        evidence_chunk_ids=list(evidence or []),
        needs_review=needs_review,
        reasoning_trace=list(reasoning_trace or []),
    )


# ----------------------------------------------------------------------
# RAG / Inference mocks
# ----------------------------------------------------------------------


def make_chunk(
    chunk_id: str = "c1",
    organ: str = "thyroid",
    content: str = "evidence content",
) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=chunk_id,
        kb_version="kb_v1.0.0",
        organ=organ,
        content=content,
        metadata={},
        embedding=None,
        source_doc_id="d1",
        section_path="",
        token_count=10,
    )


def make_scored(
    chunk_id: str = "c1",
    score: float = 0.9,
    source: RetrievalMethod = RetrievalMethod.DENSE,
    organ: str = "thyroid",
    content: str = "evidence content",
) -> ScoredChunk:
    return ScoredChunk(
        chunk=make_chunk(chunk_id, organ=organ, content=content),
        score=score,
        rerank_score=score,
        retrieval_source=source,
    )


class FakeRAGService:
    """可配置的 RAG service 替身：默认返回空结果，可注入异常。"""

    def __init__(
        self,
        chunks: Optional[List[ScoredChunk]] = None,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self._chunks = chunks or []
        self._raise = raise_exc
        self.retrieve_call_count = 0
        self.last_request: Optional[RetrievalRequest] = None

    async def retrieve(self, request: RetrievalRequest) -> RetrievalResult:
        self.retrieve_call_count += 1
        self.last_request = request
        if self._raise is not None:
            raise self._raise
        return RetrievalResult(
            chunks=list(self._chunks),
            retrieval_method=RetrievalMethod.DENSE,
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class FakeInferenceEngine:
    """可配置的 LLM 引擎替身：默认返回 "5 5 5 无"（满 pass）。"""

    def __init__(
        self,
        text: str = "evidence_sufficiency: 5\n"
        "evidence_consistency: 5\n"
        "feature_completeness: 5\n"
        "missing_features: 无",
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self.text = text
        self.raise_exc = raise_exc
        self.generate_call_count = 0
        self.last_request: Optional[InferenceRequest] = None

    async def generate(self, request: InferenceRequest) -> InferenceResponse:
        self.generate_call_count += 1
        self.last_request = request
        if self.raise_exc is not None:
            raise self.raise_exc
        return InferenceResponse(
            text=self.text,
            model="fake-model",
            usage=TokenUsage(prompt_tokens=100, completion_tokens=20, total_tokens=120),
            finish_reason="stop",
            request_id=request.request_id,
            session_id=request.session_id,
            latency_ms=10.0,
        )

    async def health_check(self) -> bool:
        return True

    async def get_model_info(self) -> ModelInfo:
        return ModelInfo(model_name="fake-model", model_version="v1")

    async def close(self) -> None:
        return None


# ----------------------------------------------------------------------
# 默认组件 fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def conflict_detector() -> ConflictDetector:
    return ConflictDetector()


@pytest.fixture
def consistency_checker() -> ConsistencyChecker:
    """无 RAG 的一致性校验器（跳过指南对比）。"""
    return ConsistencyChecker(rag_service=None)


@pytest.fixture
def consistency_checker_with_rag() -> ConsistencyChecker:
    """带 RAG 的一致性校验器（fake RAG 返回空）。"""
    return ConsistencyChecker(rag_service=FakeRAGService())


@pytest.fixture
def self_rag_evaluator_heuristic() -> SelfRAGEvaluator:
    """启发式 Self-RAG（无 LLM）。"""
    return SelfRAGEvaluator(inference_engine=None)


@pytest.fixture
def self_rag_evaluator_with_llm() -> SelfRAGEvaluator:
    """带 LLM 的 Self-RAG（fake LLM 返回满 pass）。"""
    return SelfRAGEvaluator(inference_engine=FakeInferenceEngine())


@pytest.fixture
def crag_handler() -> CRAGHandler:
    """带 RAG 的 CRAG（fake RAG 返回空）。"""
    return CRAGHandler(rag_service=FakeRAGService())


@pytest.fixture
def crag_handler_with_chunks() -> CRAGHandler:
    """带新证据的 CRAG（fake RAG 返回 3 条新证据）。"""
    chunks = [
        make_scored("new_c1", score=0.9, content="new evidence 1"),
        make_scored("new_c2", score=0.8, content="new evidence 2"),
        make_scored("new_c3", score=0.7, content="new evidence 3"),
    ]
    return CRAGHandler(rag_service=FakeRAGService(chunks=chunks))
