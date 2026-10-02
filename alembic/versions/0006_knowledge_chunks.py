"""Create knowledge_chunks table for T-GA-DATA-01 KB index builder

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-29

Creates the authoritative storage for the knowledge base index construction
pipeline. Distinct from the existing `knowledge_base` table (which serves as
the RAG retrieval fast cache); this table holds chunked content with
structured metadata and pgvector embeddings.
"""
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = '0006'
down_revision = '0005'
branch_labels = None
depends_on = None


_TABLE = 'knowledge_chunks'


def upgrade() -> None:
    op.create_table(
        _TABLE,
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('chunk_id', sa.String(length=128), nullable=False),
        sa.Column('kb_version', sa.String(length=32), nullable=False),
        sa.Column('organ', sa.String(length=32), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('metadata', JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column('embedding', Vector(768), nullable=True),
        sa.Column('source_doc_id', sa.String(length=128), nullable=False),
        sa.Column('section_path', sa.Text(), nullable=False, server_default=''),
        sa.Column('token_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column(
            'created_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.Column(
            'updated_at',
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text('CURRENT_TIMESTAMP'),
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('chunk_id', name='uq_kc_chunk_id'),
    )

    # B-tree indexes for filtering and lookup.
    op.create_index('idx_kc_kb_version', _TABLE, ['kb_version'])
    op.create_index('idx_kc_organ', _TABLE, ['organ'])
    op.create_index('idx_kc_kb_organ', _TABLE, ['kb_version', 'organ'])
    op.create_index('idx_kc_source_doc', _TABLE, ['source_doc_id'])

    # GIN index on metadata JSONB for filter queries (classifications, etc.).
    op.execute(
        f'CREATE INDEX IF NOT EXISTS idx_kc_metadata_gin '
        f'ON {_TABLE} USING GIN (metadata)'
    )

    # HNSW index for cosine-distance vector search. Falls back gracefully
    # on older pgvector versions (<0.5) by skipping creation.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_kc_embedding_hnsw "
        f"ON {_TABLE} USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    op.execute('DROP INDEX IF EXISTS idx_kc_embedding_hnsw')
    op.execute('DROP INDEX IF EXISTS idx_kc_metadata_gin')
    op.drop_index('idx_kc_source_doc', table_name=_TABLE)
    op.drop_index('idx_kc_kb_organ', table_name=_TABLE)
    op.drop_index('idx_kc_organ', table_name=_TABLE)
    op.drop_index('idx_kc_kb_version', table_name=_TABLE)
    op.drop_table(_TABLE)
