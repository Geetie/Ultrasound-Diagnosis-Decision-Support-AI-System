"""add missing indexes and GIN indexes for JSONB columns

Revision ID: 0002_missing_indexes
Revises: 0001_initial_migration
Create Date: 2026-05-12
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = '0002_missing_indexes'
down_revision = '0001_initial_migration'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index('idx_sessions_patient_id', 'sessions', ['patient_id'])
    op.create_index('idx_sessions_status', 'sessions', ['status'])
    op.create_index(
        'idx_sessions_patient_status',
        'sessions',
        ['patient_id', 'status'],
    )
    op.create_index(
        'idx_consent_patient_created',
        'consent_records',
        ['patient_id', 'created_at'],
    )

    op.execute('CREATE INDEX IF NOT EXISTS idx_sessions_target_organs_gin ON sessions USING GIN (target_organs)')
    op.execute('CREATE INDEX IF NOT EXISTS idx_sessions_organ_statuses_gin ON sessions USING GIN (organ_statuses)')
    op.execute('CREATE INDEX IF NOT EXISTS idx_diagnosis_results_findings_gin ON diagnosis_results USING GIN (findings)')
    op.execute('CREATE INDEX IF NOT EXISTS idx_safety_events_payload_gin ON safety_events USING GIN (payload)')
    op.execute('CREATE INDEX IF NOT EXISTS idx_audit_logs_detail_gin ON audit_logs USING GIN (detail)')

    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_active "
        "ON sessions (session_id, patient_id, status) "
        "WHERE status IN ('pending', 'in_progress')"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_completed "
        "ON sessions (session_id, patient_id, status, created_at) "
        "WHERE status IN ('completed', 'failed', 'terminated')"
    )


def downgrade() -> None:
    op.drop_index('idx_sessions_patient_id', table_name='sessions')
    op.drop_index('idx_sessions_status', table_name='sessions')
    op.drop_index('idx_sessions_patient_status', table_name='sessions')
    op.drop_index('idx_consent_patient_created', table_name='consent_records')

    op.execute('DROP INDEX IF EXISTS idx_sessions_target_organs_gin')
    op.execute('DROP INDEX IF EXISTS idx_sessions_organ_statuses_gin')
    op.execute('DROP INDEX IF EXISTS idx_diagnosis_results_findings_gin')
    op.execute('DROP INDEX IF EXISTS idx_safety_events_payload_gin')
    op.execute('DROP INDEX IF EXISTS idx_audit_logs_detail_gin')
    op.execute('DROP INDEX IF EXISTS idx_sessions_active')
    op.execute('DROP INDEX IF EXISTS idx_sessions_completed')
