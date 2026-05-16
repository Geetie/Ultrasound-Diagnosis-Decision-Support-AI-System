"""Add audit_cross_chain table and critical operation fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'audit_cross_chain',
        sa.Column('id', sa.String(36), primary_key=True),
        sa.Column('period_start', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('period_end', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('prev_cross_hash', sa.String(64)),
        sa.Column('cross_hash_self', sa.String(64), nullable=False),
        sa.Column('session_hashes', sa.JSONB),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    op.create_index(
        'idx_audit_cross_chain_period',
        'audit_cross_chain',
        ['period_start', 'period_end'],
    )
    op.add_column(
        'audit_logs',
        sa.Column('is_critical_operation', sa.Boolean, default=False),
    )
    op.add_column(
        'audit_logs',
        sa.Column('cross_chain_hash', sa.String(64)),
    )


def downgrade() -> None:
    op.drop_column('audit_logs', 'cross_chain_hash')
    op.drop_column('audit_logs', 'is_critical_operation')
    op.drop_index('idx_audit_cross_chain_period', table_name='audit_cross_chain')
    op.drop_table('audit_cross_chain')
