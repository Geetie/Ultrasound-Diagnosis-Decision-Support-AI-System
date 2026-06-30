"""Shared fixtures for AGT track tests."""
from __future__ import annotations

from typing import Any, List, Optional
from unittest.mock import AsyncMock

import pytest

from app.agents.protocols import Agent, AgentContext, AgentResponse
from app.inference.protocols import (
    InferenceEngine,
    InferenceError,
    InferenceErrorCode,
    InferenceRequest,
    InferenceResponse,
    ModelInfo,
    TokenUsage,
)
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def make_chunk(
    chunk_id: str = "c1",
    organ: str = "thyroid",
    content: str = "x",
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
        token_count=4,
    )


def make_chunks(n: int, organ: str = "thyroid") -> List[KnowledgeChunk]:
    """构造 n 个知识 chunk（用于 specialist 测试）。"""
    return [
        KnowledgeChunk(
            chunk_id=f"chunk_{i}",
            kb_version="kb_v1.0.0",
            organ=organ,
            content=f"chunk content {i}",
            metadata={},
            embedding=None,
            source_doc_id=f"doc_{i}",
            section_path="",
            token_count=10,
        )
        for i in range(n)
    ]


class FakeSpecialist(Agent):
    """可配置的 specialist 替身：每次 invoke 返回预设响应或抛出预设异常。"""

    def __init__(
        self,
        name: str,
        organ: Organ,
        content: str = "ok",
        confidence: float = 0.8,
        evidence: Optional[List[str]] = None,
        raise_exc: Optional[Exception] = None,
        healthy: bool = True,
    ) -> None:
        self._name = name
        self._organ = organ
        self._content = content
        self._confidence = confidence
        self._evidence = evidence or []
        self._raise = raise_exc
        self._healthy = healthy
        self.invoke_count = 0

    async def invoke(self, context: AgentContext) -> AgentResponse:
        self.invoke_count += 1
        if self._raise is not None:
            raise self._raise
        return AgentResponse(
            agent_name=self._name,
            organ=self._organ,
            content=self._content,
            confidence=self._confidence,
            evidence_chunk_ids=list(self._evidence),
            reasoning_trace=[f"{self._name}_done"],
        )

    def get_capability_tags(self) -> List[str]:
        return [self._organ.value, "specialist"]

    async def health_check(self) -> bool:
        return self._healthy

    async def close(self) -> None:
        """释放资源(测试替身无外部资源,实现为空)。

        必须实现 ``Agent`` ABC 的 ``close()`` 抽象方法,否则
        ``FakeSpecialist`` 无法被实例化(参见
        ``app/agents/protocols.py:Agent.close``)。
        """
        return None


class FakeRAGService:
    """可配置的 RAG service 替身：默认返回空结果，可注入异常。"""

    def __init__(self, chunks: Optional[List[KnowledgeChunk]] = None, raise_exc: Optional[Exception] = None) -> None:
        self._chunks = chunks or []
        self._raise = raise_exc
        self.retrieve_call_count = 0
        self.last_request = None

    async def retrieve(self, request):
        self.retrieve_call_count += 1
        self.last_request = request
        if self._raise is not None:
            raise self._raise
        from app.rag.protocols import RetrievalResult, ScoredChunk, RetrievalMethod
        return RetrievalResult(
            chunks=[
                ScoredChunk(
                    chunk=c,
                    score=0.9,
                    rerank_score=None,
                    retrieval_source=RetrievalMethod.HYBRID,
                )
                for c in self._chunks
            ],
        )

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        pass


class FakeRedis:
    """最小化的 Redis 替身，支持 get/setex 计数和注入异常。"""

    def __init__(self, raise_on_get: bool = False, raise_on_set: bool = False) -> None:
        self._store: dict[str, bytes] = {}
        self.raise_on_get = raise_on_get
        self.raise_on_set = raise_on_set
        self.get_count = 0
        self.setex_count = 0

    async def get_client(self) -> "FakeRedis":
        return self

    async def get(self, key: str):
        self.get_count += 1
        if self.raise_on_get:
            raise ConnectionError("simulated redis down")
        return self._store.get(key)

    async def setex(self, key: str, ttl: int, value):
        self.setex_count += 1
        if self.raise_on_set:
            raise ConnectionError("simulated redis down on write")
        if isinstance(value, str):
            value = value.encode("utf-8")
        self._store[key] = value

    def set_raw(self, key: str, value: bytes) -> None:
        self._store[key] = value


# ----------------------------------------------------------------------
# FakeKnowledgeAgent / FakeInferenceEngine（specialist 测试用）
# ----------------------------------------------------------------------


class FakeKnowledgeAgent:
    """specialist 测试用的 knowledge agent 替身。

    可注入：
    - ``chunks`` — 默认返回的 chunk 列表
    - ``raise_exc`` — 调用 ``query()`` 时抛出的异常
    - ``query_call_count`` — ``query()`` 调用次数（用于断言）
    """

    def __init__(
        self,
        chunks: Optional[List[KnowledgeChunk]] = None,
        raise_exc: Optional[Exception] = None,
    ) -> None:
        self._chunks = chunks if chunks is not None else make_chunks(3)
        self._raise = raise_exc
        self.query_call_count = 0
        self.last_query = None
        self.last_organ = None
        self.last_top_k = None

    async def query(
        self,
        query: str,
        organ: Organ,
        top_k: int = 10,
    ) -> List[KnowledgeChunk]:
        self.query_call_count += 1
        self.last_query = query
        self.last_organ = organ
        self.last_top_k = top_k
        if self._raise is not None:
            raise self._raise
        return list(self._chunks)[:top_k]

    async def health_check(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class FakeInferenceEngine:
    """specialist 测试用的 inference engine 替身。

    可注入：
    - ``text`` — 默认返回的生成文本（公开属性，可在测试中直接修改）
    - ``raise_exc`` — 调用 ``generate()`` 时抛出的异常
    - ``latency_ms`` — 模拟的推理延迟（秒）
    """

    def __init__(
        self,
        text: str = "TR4",
        raise_exc: Optional[Exception] = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.text = text
        self.raise_exc = raise_exc
        self.latency_ms = latency_ms
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
            usage=TokenUsage(prompt_tokens=100, completion_tokens=10, total_tokens=110),
            finish_reason="stop",
            request_id=request.request_id,
            session_id=request.session_id,
            latency_ms=self.latency_ms,
        )

    async def health_check(self) -> bool:
        return True

    async def get_model_info(self) -> ModelInfo:
        return ModelInfo(model_name="fake-model", model_version="v1")

    async def close(self) -> None:
        return None


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


@pytest.fixture
def thyroid_specialist():
    return FakeSpecialist(
        name="thyroid_specialist",
        organ=Organ.THYROID,
        content="[thyroid] result",
        confidence=0.85,
        evidence=["c1", "c2"],
    )


@pytest.fixture
def liver_specialist():
    return FakeSpecialist(
        name="liver_specialist",
        organ=Organ.LIVER,
        content="[liver] result",
        confidence=0.7,
        evidence=["c2", "c3"],
    )


@pytest.fixture
def cardiac_specialist():
    return FakeSpecialist(
        name="cardiac_specialist",
        organ=Organ.CARDIAC,
        content="[cardiac] result",
        confidence=0.6,
        evidence=["c4"],
    )


@pytest.fixture
def all_specialists(thyroid_specialist, liver_specialist, cardiac_specialist):
    return {
        Organ.THYROID: thyroid_specialist,
        Organ.LIVER: liver_specialist,
        Organ.CARDIAC: cardiac_specialist,
    }
