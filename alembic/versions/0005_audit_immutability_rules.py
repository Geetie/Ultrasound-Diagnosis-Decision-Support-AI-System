"""Add immutability RULEs for audit_logs and audit_cross_chain tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-17
"""
from alembic import op
import sqlalchemy as sa


revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE RULE audit_log_no_update AS ON UPDATE TO audit_logs DO INSTEAD NOTHING;
        CREATE RULE audit_log_no_delete AS ON DELETE TO audit_logs DO INSTEAD NOTHING;
        CREATE RULE audit_cross_chain_no_update AS ON UPDATE TO audit_cross_chain DO INSTEAD NOTHING;
        CREATE RULE audit_cross_chain_no_delete AS ON DELETE TO audit_cross_chain DO INSTEAD NOTHING;
    """)


def downgrade() -> None:
    op.execute("""
        DROP RULE IF EXISTS audit_log_no_update ON audit_logs;
        DROP RULE IF EXISTS audit_log_no_delete ON audit_logs;
        DROP RULE IF EXISTS audit_cross_chain_no_update ON audit_cross_chain;
        DROP RULE IF EXISTS audit_cross_chain_no_delete ON audit_cross_chain;
    """)
