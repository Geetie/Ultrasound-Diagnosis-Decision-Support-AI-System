"""Unit tests for builder._attach_relations (图谱边投影，graph_query.py 契约).

Covers:
- 单块文档不产边
- 相邻块互指（next_section/prev_section），目标为真实 chunk_id
- resume 跳过的块（不在 enriched 中）不写元数据，但邻居边仍指向它
- 上游已有 relations 时不覆盖
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.track_knowledge]

from app.knowledge.builder import KnowledgeIndexBuilderImpl
from app.knowledge.protocols import KnowledgeChunk


def _chunk(kb_version: str, doc: str, index: int) -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id=f"{kb_version}:{doc}#{index:04d}",
        kb_version=kb_version,
        organ="thyroid",
        content=f"chunk {index}",
        metadata={"chunk_index": index},
        source_doc_id=doc,
        section_path="",
        token_count=4,
    )


def test_single_chunk_no_edges():
    raw = [_chunk("kb_v1", "d1", 0)]
    KnowledgeIndexBuilderImpl._attach_relations(raw, list(raw))
    assert "relations" not in raw[0].metadata


def test_adjacent_chunks_reference_each_other():
    raw = [_chunk("kb_v1", "d1", i) for i in range(3)]
    enriched = [raw[0], raw[1], raw[2]]
    KnowledgeIndexBuilderImpl._attach_relations(raw, enriched)
    assert raw[0].metadata["relations"] == [
        {"target": "kb_v1:d1#0001", "relation": "next_section"}
    ]
    assert raw[1].metadata["relations"] == [
        {"target": "kb_v1:d1#0002", "relation": "next_section"},
        {"target": "kb_v1:d1#0000", "relation": "prev_section"},
    ]
    assert raw[2].metadata["relations"] == [
        {"target": "kb_v1:d1#0001", "relation": "prev_section"}
    ]


def test_resumed_chunk_not_enriched_but_still_referenced():
    raw = [_chunk("kb_v1", "d1", i) for i in range(3)]
    enriched = [raw[0], raw[2]]  # raw[1] 已入库（resume 跳过）
    KnowledgeIndexBuilderImpl._attach_relations(raw, enriched)
    assert "relations" not in raw[1].metadata
    assert raw[0].metadata["relations"] == [
        {"target": "kb_v1:d1#0001", "relation": "next_section"}
    ]
    assert raw[2].metadata["relations"] == [
        {"target": "kb_v1:d1#0001", "relation": "prev_section"}
    ]


def test_existing_relations_not_overwritten():
    raw = [_chunk("kb_v1", "d1", i) for i in range(2)]
    raw[0].metadata["relations"] = [{"target": "x", "relation": "custom"}]
    KnowledgeIndexBuilderImpl._attach_relations(raw, [raw[0], raw[1]])
    assert raw[0].metadata["relations"] == [{"target": "x", "relation": "custom"}]
    assert "relations" in raw[1].metadata
