"""Add parent_chunk_id and chunk_type columns for Parent-Child Chunking (K-1 fix)

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-30

Adds Parent-Child Chunking support per §5.5 design spec:
- parent_chunk_id: nullable String(128) referencing chunk_id of the parent chunk.
- chunk_type: 'parent' (section-level context) or 'child' (fine-grained retrieval).

Existing rows are backfilled with chunk_type='child' (the default for flat chunks).
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add parent_chunk_id column (nullable — flat chunks have no parent).
    op.add_column(
        'knowledge_chunks',
        sa.Column('parent_chunk_id', sa.String(128), nullable=True),
    )

    # Add chunk_type column (NOT NULL, default 'child' for backward compat).
    op.add_column(
        'knowledge_chunks',
        sa.Column('chunk_type', sa.String(16), nullable=False, server_default='child'),
    )

    # Backfill existing rows: all existing chunks are 'child' type.
    # (server_default='child' already handles this for pre-existing rows.)

    # Indexes for efficient parent lookup and type filtering.
    op.create_index('idx_kc_parent', 'knowledge_chunks', ['parent_chunk_id'])
    op.create_index('idx_kc_type', 'knowledge_chunks', ['chunk_type'])


def downgrade() -> None:
    op.drop_index('idx_kc_type', table_name='knowledge_chunks')
    op.drop_index('idx_kc_parent', table_name='knowledge_chunks')
    op.drop_column('knowledge_chunks', 'chunk_type')
    op.drop_column('knowledge_chunks', 'parent_chunk_id')
