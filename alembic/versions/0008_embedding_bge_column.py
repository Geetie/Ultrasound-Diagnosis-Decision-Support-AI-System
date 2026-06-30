"""Add embedding_bge column for bge-m3-medical 1024d vectors.

Revision ID: 0008
Revises: 0007
Create Date: 2026-06-30

P0-EMB-01 fix: implements the dual-column design from
`docs-for-guiding generating docs/超声诊疗决策支持系统/10-架构优化修订记录.md`
(v1.1 §三) which requires EmbeddingService to support both dim=768
(Ultrasound-CLIP) and dim=1024 (bge-m3-medical) simultaneously.

- `embedding Vector(768)` stays for Ultrasound-CLIP.
- `embedding_bge Vector(1024)` is added for bge-m3-medical.

Both columns get an HNSW cosine-distance index so neither path
degrades retrieval latency.
"""
from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # knowledge_chunks (authoritative KB index, alembic 0006)
    op.add_column(
        'knowledge_chunks',
        sa.Column('embedding_bge', Vector(1024), nullable=True),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_kc_embedding_bge_hnsw "
        "ON knowledge_chunks USING hnsw (embedding_bge vector_cosine_ops)"
    )

    # knowledge_base (RAG fast cache, alembic 0001)
    op.add_column(
        'knowledge_base',
        sa.Column('embedding_bge', Vector(1024), nullable=True),
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_bge_hnsw "
        "ON knowledge_base USING hnsw (embedding_bge vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_knowledge_embedding_bge_hnsw")
    op.drop_column('knowledge_base', 'embedding_bge')

    op.execute("DROP INDEX IF EXISTS idx_kc_embedding_bge_hnsw")
    op.drop_column('knowledge_chunks', 'embedding_bge')
