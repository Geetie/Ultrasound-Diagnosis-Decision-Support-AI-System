"""initial migration

Revision ID: 0001
Revises: 
Create Date: 2026-05-07 14:00:00.000000

"""
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'patients',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('gender', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patient_id')
    )

    op.create_table(
        'consent_records',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=True),
        sa.Column('consent_type', sa.String(length=32), nullable=False),
        sa.Column('consented', sa.Boolean(), server_default='false', nullable=False),
        sa.Column('consent_date', sa.TIMESTAMP(), nullable=True),
        sa.Column('withdrawal_date', sa.TIMESTAMP(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.patient_id'], )
    )

    op.create_table(
        'diagnosis_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=True),
        sa.Column('organ', sa.String(length=32), nullable=False),
        sa.Column('status', sa.String(length=32), server_default='pending', nullable=False),
        sa.Column('kb_version', sa.String(length=32), nullable=True),
        sa.Column('model_version', sa.String(length=32), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id'),
        sa.ForeignKeyConstraint(['patient_id'], ['patients.patient_id'], )
    )

    op.create_table(
        'diagnosis_results',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('organ', sa.String(length=32), nullable=False),
        sa.Column('tier', sa.String(length=16), nullable=False),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('findings', sa.Text(), nullable=True),
        sa.Column('recommendations', sa.Text(), nullable=True),
        sa.Column('knowledge_ids', sa.ARRAY(sa.Text()), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.ForeignKeyConstraint(['session_id'], ['diagnosis_sessions.session_id'], )
    )

    op.create_table(
        'knowledge_base',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kb_id', sa.String(length=64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=True),
        sa.Column('organ', sa.String(length=32), nullable=True),
        sa.Column('tier', sa.String(length=16), nullable=True),
        sa.Column('version', sa.String(length=32), nullable=True),
        sa.Column('confidence', sa.Float(), server_default='1.0', nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kb_id')
    )

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('operation', sa.String(length=64), nullable=False),
        sa.Column('operator', sa.String(length=64), nullable=True),
        sa.Column('details', sa.JSONB(), nullable=True),
        sa.Column('hash_chain', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index('idx_knowledge_embedding', 'knowledge_base', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('idx_diagnosis_sessions_session_id', 'diagnosis_sessions', ['session_id'], unique=False)
    op.create_index('idx_consent_patient_id', 'consent_records', ['patient_id'], unique=False)
    op.create_index('idx_audit_logs_session_id', 'audit_logs', ['session_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_audit_logs_session_id', table_name='audit_logs')
    op.drop_index('idx_consent_patient_id', table_name='consent_records')
    op.drop_index('idx_diagnosis_sessions_session_id', table_name='diagnosis_sessions')
    op.drop_index('idx_knowledge_embedding', table_name='knowledge_base')
    op.drop_table('audit_logs')
    op.drop_table('knowledge_base')
    op.drop_table('diagnosis_results')
    op.drop_table('diagnosis_sessions')
    op.drop_table('consent_records')
    op.drop_table('patients')
