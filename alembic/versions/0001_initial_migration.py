"""initial migration

Revision ID: 0001
Revises:
Create Date: 2026-05-07 14:00:00.000000

## 2026-09-28 真机部署暴露后重写（对齐 `app/models/database.py`）

原版本的 DDL 是照**更早一版的模型**写的，与现行模型严重不一致，导致
`alembic upgrade head` **在任何环境都跑不通**：

* **6 张表全链缺失**：`safety_events` / `sessions_archive` / `devices` /
  `feedback_records` / `users` / `organ_disease_thresholds` —— 其中
  `safety_events` 被 0002（GIN 索引）与 0003（BRIN 索引）引用，
  于是必然报 `relation "safety_events" does not exist`；
* **多张表缺列**：`sessions.target_organs` / `organ_statuses` /
  `knowledge_base_version` / `device_id` / `expired_at` / `consent_status_at_creation`、
  `consent_records.record_id` / `status` / `granted_at` / `withdrawn_at` / `consent_version`、
  `diagnosis_results.request_id` / `status` / `result_pushed`、
  `audit_logs.log_id` / `target_type` / `target_id` / `log_type` / `hash_self` 等 ——
  0002 建 `sessions.target_organs` 的 GIN 索引时必然报
  `column "target_organs" does not exist`（真机原样报过）；
* **列名与模型不符**：`audit_logs` 的 `operation/operator/details/hash_chain`
  对应模型里的 `action/actor/detail/hash_prev` —— 0002 给 `audit_logs.detail`
  建 GIN 时因此报 `column "detail" does not exist`；
* **类型不符**：`diagnosis_results.findings` 建成了 `Text`，而模型与 0002 的
  GIN 索引都要求 `JSONB`；
* **主键不符**：`sessions` 做成 `id` 自增主键，模型是 `session_id` 主键
  （`diagnosis_results.session_id` 的外键正指向它）。

本次按"改动面最小 + 以模型为权威"的原则就地修正 0001（而不是新增一版补丁迁移）：
0001 是 create-only 的基线，且该链条**从未在真实库上成功执行过**，
把基线改对才能让"空库 → upgrade head → 与模型一致"成立。

另补 `CREATE EXTENSION IF NOT EXISTS vector`：`Vector(768)` 列类型与 0006/0008 的
hnsw 索引都依赖 pgvector，而**原链条没有任何一处创建该扩展**，等价于隐含要求
"环境里必须先手工装好"—— 真机上就是手工 `CREATE EXTENSION` 才让 create_all 跑通的。
"""
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `Vector(...)` 列与后续 hnsw 索引都依赖 pgvector；基线必须自包含。
    op.execute('CREATE EXTENSION IF NOT EXISTS vector')

    op.create_table(
        'patients',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.Text(), nullable=True),
        sa.Column('birth_date', sa.Date(), nullable=True),
        sa.Column('gender', sa.String(length=10), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('patient_id')
    )

    op.create_table(
        'consent_records',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('record_id', sa.BigInteger(), nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('granted_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('withdrawn_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('consent_version', sa.String(length=16), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('record_id')
    )

    op.create_table(
        'sessions',
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('target_organs', JSONB(), nullable=False),
        sa.Column('organ_statuses', JSONB(), nullable=False),
        sa.Column('knowledge_base_version', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('expired_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('consent_status_at_creation', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('session_id')
    )

    op.create_table(
        'diagnosis_results',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('organ', sa.String(length=32), nullable=False),
        sa.Column('tier', sa.String(length=16), nullable=False),
        sa.Column('clinical_text', sa.Text(), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=False),
        sa.Column('findings', JSONB(), nullable=True),
        sa.Column('recommendations', sa.Text(), nullable=True),
        sa.Column('knowledge_ids', sa.ARRAY(sa.Text()), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=True),
        sa.Column('result_pushed', sa.Boolean(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('request_id'),
        sa.ForeignKeyConstraint(['session_id'], ['sessions.session_id'])
    )

    op.create_table(
        'knowledge_base',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('kb_id', sa.String(length=64), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', Vector(768), nullable=True),
        sa.Column('organ', sa.String(length=32), nullable=True),
        sa.Column('tier', sa.String(length=16), nullable=True),
        sa.Column('version', sa.String(length=32), nullable=True),
        sa.Column('confidence', sa.Float(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('kb_id')
    )

    op.create_table(
        'audit_logs',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('log_id', sa.String(length=36), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=True),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('actor', sa.String(length=64), nullable=False),
        sa.Column('target_type', sa.String(length=32), nullable=True),
        sa.Column('target_id', sa.String(length=64), nullable=True),
        sa.Column('detail', JSONB(), nullable=True),
        sa.Column('log_type', sa.String(length=16), nullable=False),
        sa.Column('hash_prev', sa.String(length=64), nullable=True),
        sa.Column('hash_self', sa.String(length=64), nullable=False),
        sa.Column('is_critical_operation', sa.Boolean(), nullable=True),
        sa.Column('cross_chain_hash', sa.String(length=64), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('log_id')
    )

    # ---- 以下 6 张表原链条从未创建（其中 safety_events 被 0002/0003 引用） ----

    op.create_table(
        'safety_events',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('event_id', sa.BigInteger(), nullable=False),
        sa.Column('event_type', sa.String(length=64), nullable=False),
        sa.Column('severity', sa.String(length=16), nullable=False),
        sa.Column('kb_version', sa.String(length=32), nullable=True),
        sa.Column('device_id', sa.String(length=64), nullable=True),
        sa.Column('patient_id', sa.String(length=64), nullable=True),
        sa.Column('payload', JSONB(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('acknowledged', sa.Boolean(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id')
    )

    op.create_table(
        'sessions_archive',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('patient_id', sa.String(length=64), nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('target_organs', JSONB(), nullable=False),
        sa.Column('organ_statuses', JSONB(), nullable=False),
        sa.Column('knowledge_base_version', sa.String(length=32), nullable=False),
        sa.Column('consent_status_at_creation', sa.String(length=16), nullable=False),
        sa.Column('archive_timestamp', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('archived_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('patient_type', sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id')
    )

    op.create_table(
        'devices',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('device_id', sa.String(length=64), nullable=False),
        sa.Column('device_name', sa.String(length=128), nullable=False),
        sa.Column('device_type', sa.String(length=64), nullable=False),
        sa.Column('location', sa.String(length=256), nullable=False),
        sa.Column('jetpack_version', sa.String(length=32), nullable=True),
        sa.Column('api_key', sa.String(length=128), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_id')
    )

    op.create_table(
        'feedback_records',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('feedback_id', sa.String(length=64), nullable=False),
        sa.Column('request_id', sa.String(length=64), nullable=False),
        sa.Column('session_id', sa.String(length=64), nullable=False),
        sa.Column('feedback_type', sa.String(length=32), nullable=False),
        sa.Column('feedback_level', sa.String(length=8), nullable=False),
        sa.Column('reviewer_id', sa.String(length=64), nullable=False),
        sa.Column('content', JSONB(), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('feedback_id')
    )

    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('username', sa.String(length=64), nullable=False),
        sa.Column('email', sa.String(length=256), nullable=True),
        sa.Column('full_name', sa.String(length=128), nullable=True),
        sa.Column('hashed_password', sa.String(length=256), nullable=False),
        sa.Column('disabled', sa.Boolean(), nullable=False),
        sa.Column('role', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('username')
    )

    op.create_table(
        'organ_disease_thresholds',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('organ', sa.String(length=32), nullable=False),
        sa.Column('disease_type', sa.String(length=64), nullable=True),
        sa.Column('confidence_threshold', sa.Float(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('created_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column('updated_at', sa.TIMESTAMP(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )

    op.create_index('idx_knowledge_embedding', 'knowledge_base', ['embedding'], unique=False, postgresql_using='hnsw', postgresql_ops={'embedding': 'vector_cosine_ops'})
    op.create_index('idx_diagnosis_sessions_session_id', 'sessions', ['session_id'], unique=False)
    op.create_index('idx_consent_patient_id', 'consent_records', ['patient_id'], unique=False)
    op.create_index('idx_audit_logs_session_id', 'audit_logs', ['session_id'], unique=False)
    # 模型 __table_args__ 里声明、而后续版本都没有创建的两条（0002 已建的不在此重复，
    # 否则重复建索引会失败）：
    op.create_index('idx_audit_session_time', 'audit_logs', ['session_id', 'created_at'], unique=False)
    op.create_index('idx_safety_event_type_time', 'safety_events', ['event_type', 'created_at'], unique=False)
    op.create_index('idx_organ_disease', 'organ_disease_thresholds', ['organ', 'disease_type'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_organ_disease', table_name='organ_disease_thresholds')
    op.drop_index('idx_safety_event_type_time', table_name='safety_events')
    op.drop_index('idx_audit_session_time', table_name='audit_logs')
    op.drop_index('idx_audit_logs_session_id', table_name='audit_logs')
    op.drop_index('idx_consent_patient_id', table_name='consent_records')
    op.drop_index('idx_diagnosis_sessions_session_id', table_name='sessions')
    op.drop_index('idx_knowledge_embedding', table_name='knowledge_base')
    op.drop_table('organ_disease_thresholds')
    op.drop_table('users')
    op.drop_table('feedback_records')
    op.drop_table('devices')
    op.drop_table('sessions_archive')
    op.drop_table('safety_events')
    op.drop_table('audit_logs')
    op.drop_table('knowledge_base')
    op.drop_table('diagnosis_results')
    op.drop_table('sessions')
    op.drop_table('consent_records')
    op.drop_table('patients')
