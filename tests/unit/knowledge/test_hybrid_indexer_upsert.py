"""守卫：chunk upsert 的 ON CONFLICT 子句必须引用物理列名 metadata。

背景（真机 2026-09-29 首次正式建库实测）：set_ 字符串键是数据库列名——
用 ORM 属性名 "meta" 会生成 `meta = excluded.metadata`，而物理列是
`metadata` → `column "meta" does not exist`，409 文档建库全部失败。
此前该路径从未在真机跑通过，属"实现了但从未接线"类缺陷。
"""
from __future__ import annotations

from sqlalchemy.dialects import postgresql

from app.knowledge.hybrid_indexer import _chunk_to_row, build_upsert_statement
from app.knowledge.protocols import KnowledgeChunk


def _sample_chunk() -> KnowledgeChunk:
    return KnowledgeChunk(
        chunk_id="doc#0000",
        kb_version="kb_v1.0.0",
        organ="thyroid",
        content="TI-RADS 4 类内容",
        metadata={"chunk_index": 0, "organ": "thyroid"},
        embedding=[0.1] * 1024,
        source_doc_id="doc",
        section_path="测试",
        token_count=10,
    )


def test_on_conflict_引用物理列_metadata() -> None:
    values = [_chunk_to_row(_sample_chunk())]
    stmt = build_upsert_statement(values)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "metadata" in sql
    assert "meta =" not in sql  # 属性名不得泄漏为物理列名


def test_set_子句包含全部更新列() -> None:
    values = [_chunk_to_row(_sample_chunk())]
    stmt = build_upsert_statement(values)
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    for col in (
        "kb_version",
        "organ",
        "content",
        "metadata",
        "embedding",
        "source_doc_id",
        "section_path",
        "token_count",
    ):
        assert f"{col} = excluded.{col}" in sql, col
