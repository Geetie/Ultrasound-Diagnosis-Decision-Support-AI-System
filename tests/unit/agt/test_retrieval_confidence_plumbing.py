"""A.142 / N4：把检索置信度带过 RAG→agent 边界（只观测，不决策）。

缺口：RAG 层有分（`ScoredChunk.score/rerank_score`），但 `KnowledgeAgent.query` 只回
`KnowledgeChunk`，分被丢弃 —— "低置信度升档"因此永远读不到置信度（N4）。

本轮只补**通道与观测**：
- `KnowledgeChunk.score/rerank_score` 带过边界（默认 None，向后兼容）；
- 专科侧算 `retrieval_confidence`（优先精排分，取 max）并挂到 `AgentResponse`；
- **不参与路由**：升档门槛要真机延迟数据才能定，先把分布变成可见。
"""

from __future__ import annotations

import pytest

from app.agents.protocols import AgentResponse
from app.agents.specialist.base import _retrieval_confidence
from app.knowledge.protocols import KnowledgeChunk
from app.models.schemas import Organ

pytestmark = pytest.mark.track_agt


def _chunk(cid: str, *, score=None, rerank=None) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=cid,
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="x",
        source_doc_id="doc",
        score=score,
        rerank_score=rerank,
    )


def test_confidence_prefers_rerank_and_takes_max():
    chunks = [_chunk("a", score=0.4, rerank=0.9), _chunk("b", score=0.8, rerank=0.3)]
    assert _retrieval_confidence(chunks) == pytest.approx(0.9)


def test_confidence_falls_back_to_raw_score():
    assert _retrieval_confidence([_chunk("a", score=0.42)]) == pytest.approx(0.42)


def test_confidence_none_when_scores_absent():
    assert _retrieval_confidence([_chunk("a")]) is None, "没分是'未测'，不是 0 分"


def test_confidence_none_for_empty_chunks():
    assert _retrieval_confidence([]) is None


def test_knowledge_chunk_defaults_keep_backward_compat():
    c = KnowledgeChunk(
        chunk_id="a#1", kb_version="kb_v1", organ="liver", content="x", source_doc_id="d"
    )
    assert c.score is None and c.rerank_score is None


def test_agent_response_carries_confidence():
    r = AgentResponse(
        agent_name="t", organ=Organ.THYROID, content="c", retrieval_confidence=0.77
    )
    assert r.retrieval_confidence == pytest.approx(0.77)
    assert AgentResponse(agent_name="t", content="c").retrieval_confidence is None


def test_call_rag_preserves_scores(monkeypatch):
    """`_call_rag` 必须把 ScoredChunk 的分带出来（此前是 `[sc.chunk for sc in ...]`）。"""
    import asyncio
    from types import SimpleNamespace

    from app.agents.knowledge import KnowledgeAgentImpl

    agent = KnowledgeAgentImpl.__new__(KnowledgeAgentImpl)

    class _Rag:
        async def retrieve(self, request):
            return SimpleNamespace(
                chunks=[
                    SimpleNamespace(
                        chunk=_chunk("a"), score=0.61, rerank_score=0.93
                    )
                ]
            )

    agent._rag = _Rag()
    agent._normalize_profile = lambda p: {}
    out = asyncio.run(agent._call_rag("q", Organ.THYROID, 5, None))
    assert out[0].score == pytest.approx(0.61)
    assert out[0].rerank_score == pytest.approx(0.93)
