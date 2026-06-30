"""Shared fixtures for AGT track tests."""
from __future__ import annotations

from typing import List, Optional
from unittest.mock import AsyncMock

import pytest

from app.agents.protocols import Agent, AgentContext, AgentResponse
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
