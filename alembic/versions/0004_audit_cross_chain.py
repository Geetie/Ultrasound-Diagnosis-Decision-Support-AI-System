"""Add audit_cross_chain table and critical operation fields

Revision ID: 0004
Revises: 0003
Create Date: 2026-05-13
"""
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB  # JSONB 是 PG 方言类型，JSONB 不存在（2026-09-28 现场部署暴露）

from alembic import op

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
        sa.Column('session_hashes', JSONB),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.func.current_timestamp()),
    )
    op.create_index(
        'idx_audit_cross_chain_period',
        'audit_cross_chain',
        ['period_start', 'period_end'],
    )
    # 2026-09-28 真机修复：这两列**已由重写后的 0001 按模型建出**（模型把它们算作
    # audit_logs 的一部分），再用 `op.add_column` 补就必然报
    # `DuplicateColumnError: column "is_critical_operation" of relation "audit_logs" already exists`
    # —— 迁移链因此停在 0004、整批回滚、库是空的。
    # 改成 `ADD COLUMN IF NOT EXISTS` 的原生语句：既保留本版本"引入这两列"的语义，
    # 又对"基线已建"与"重跑"两种情况都幂等。
    op.execute('ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS is_critical_operation BOOLEAN')
    op.execute('ALTER TABLE audit_logs ADD COLUMN IF NOT EXISTS cross_chain_hash VARCHAR(64)')


def downgrade() -> None:
    # 与 upgrade 对称：用 IF EXISTS 避免"基线里本来就有、不该被这里删"的歧义。
    op.execute('ALTER TABLE audit_logs DROP COLUMN IF EXISTS cross_chain_hash')
    op.execute('ALTER TABLE audit_logs DROP COLUMN IF EXISTS is_critical_operation')
    op.drop_index('idx_audit_cross_chain_period', table_name='audit_cross_chain')
    op.drop_table('audit_cross_chain')
