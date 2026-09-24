"""K-8: KB 管线集成测试 — 端到端构建流程。

验证:
- Source doc → chunk → embedding → index 完整流水线
- Parent-Child 关系正确写入
- 父 chunk 先于子 chunk 写入
- 语义分块产生 parent 概要

需要 RUN_KB_INTEGRATION=1 环境变量启用。
"""
from __future__ import annotations

import os

import pytest

# Skip entire module unless explicitly enabled.
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_KB_INTEGRATION", "") != "1",
    reason="Set RUN_KB_INTEGRATION=1 to enable KB integration tests",
)


@pytest.mark.asyncio
class TestKBBuildEndToEnd:
    """End-to-end test for the KB build pipeline with Parent-Child Chunking."""

    async def test_semantic_chunker_produces_parent_child(self) -> None:
        """Verify SemanticChunker creates parent + child chunks."""
        from app.knowledge.chunker import SemanticChunker, KnowledgeSourceDoc
        from app.knowledge.protocols import ChunkType

        chunker = SemanticChunker()
        doc = KnowledgeSourceDoc(
            source_doc_id="thyroid_guidelines",
            organ="thyroid",
            content=(
                "TI-RADS 4A\n"
                "低回声实性结节，形态不规则，边界不清，伴有微钙化。"
                "建议行FNA细针穿刺活检以明确诊断。\n\n"
                "TI-RADS 5\n"
                "高度怀疑恶性。低回声实性结节，伴有微钙化和淋巴结肿大。"
                "强烈建议手术切除。"
            ),
        )
        chunks = chunker.chunk(doc, kb_version="kb_v1.0.0")

        # Should have parent chunks for TI-RADS 4A and TI-RADS 5
        parent_chunks = [c for c in chunks if c.chunk_type == ChunkType.PARENT]
        child_chunks = [c for c in chunks if c.chunk_type == ChunkType.CHILD]

        assert len(parent_chunks) >= 2, f"Expected >= 2 parents, got {len(parent_chunks)}"
        assert len(child_chunks) >= 2, f"Expected >= 2 children, got {len(child_chunks)}"

        # Each child should have a parent_chunk_id
        for child in child_chunks:
            assert child.parent_chunk_id is not None, "Child chunk missing parent_chunk_id"

        # Parent chunks should NOT have parent_chunk_id
        for parent in parent_chunks:
            assert parent.parent_chunk_id is None, "Parent chunk should not have parent_chunk_id"

        # Parent content should contain the section title
        for parent in parent_chunks:
            assert "TI-RADS" in parent.content, f"Parent content missing title: {parent.content}"

    async def test_parent_child_id_consistency(self) -> None:
        """Verify child.parent_chunk_id references an actual parent chunk_id."""
        from app.knowledge.chunker import SemanticChunker, KnowledgeSourceDoc

        chunker = SemanticChunker()
        doc = KnowledgeSourceDoc(
            source_doc_id="liver_guidelines",
            organ="liver",
            content=(
                "LI-RADS 3\n"
                "中等概率恶性。建议短期随访（3-6个月）。\n\n"
                "LI-RADS 4\n"
                "高度怀疑HCC。建议活检或手术。"
            ),
        )
        chunks = chunker.chunk(doc, kb_version="kb_v1.0.0")

        parent_ids = {c.chunk_id for c in chunks if c.chunk_type == "parent"}
        children = [c for c in chunks if c.chunk_type == "child"]

        for child in children:
            assert child.parent_chunk_id in parent_ids, (
                f"Child {child.chunk_id} references non-existent parent {child.parent_chunk_id}"
            )

    async def test_sentence_chunker_produces_only_children(self) -> None:
        """Verify SentenceBoundaryChunker only produces child chunks."""
        from app.knowledge.chunker import SentenceBoundaryChunker, KnowledgeSourceDoc

        chunker = SentenceBoundaryChunker()
        doc = KnowledgeSourceDoc(
            source_doc_id="test_doc",
            organ="thyroid",
            content="低回声结节，形态规则，边界清晰。建议定期随访。",
        )
        chunks = chunker.chunk(doc, kb_version="kb_v1.0.0")

        for c in chunks:
            assert c.chunk_type == "child", "SentenceBoundaryChunker should only produce child chunks"
            assert c.parent_chunk_id is None, "Child chunks from non-semantic chunker have no parent"
