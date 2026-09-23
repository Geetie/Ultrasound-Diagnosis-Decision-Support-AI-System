"""Add BRIN indexes on created_at for large time-series tables

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-13
"""
from alembic import op

revision = '0003'
down_revision = '0002_missing_indexes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_audit_logs_created_at_brin "
        "ON audit_logs USING brin (created_at) WITH (pages_per_range = 32)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_safety_events_created_at_brin "
        "ON safety_events USING brin (created_at) WITH (pages_per_range = 32)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_audit_logs_created_at_brin")
    op.execute("DROP INDEX IF EXISTS idx_safety_events_created_at_brin")
