from sqlalchemy import (
    Column, Integer, String, Text, Float, Date, TIMESTAMP, Boolean, 
    ARRAY, JSON, ForeignKey, BigInteger, Index
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector
from app.config.database import Base


class Patient(Base):
    __tablename__ = 'patients'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(Text)
    birth_date = Column(Date)
    gender = Column(String(10))
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp(), onupdate=func.current_timestamp())


class ConsentRecord(Base):
    __tablename__ = 'consent_records'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    record_id = Column(BigInteger, unique=True, nullable=False, index=True)
    patient_id = Column(String(64), nullable=False, index=True)
    status = Column(String(16), nullable=False)
    granted_at = Column(TIMESTAMP(timezone=True))
    withdrawn_at = Column(TIMESTAMP(timezone=True))
    consent_version = Column(String(16), nullable=False, default="1.0")
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())

    __table_args__ = (
        Index('idx_consent_patient_status', 'patient_id', 'status'),
    )


class DiagnosisSession(Base):
    __tablename__ = 'sessions'
    __table_args__ = (
        Index('idx_sessions_patient_status', 'patient_id', 'status'),
    )

    session_id = Column(String(64), primary_key=True)  # 业务主键：外部系统生成的会话标识符，保留String类型
    patient_id = Column(String(64), nullable=False, index=True)
    device_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False, default='pending', index=True)
    target_organs = Column(JSONB, nullable=False)
    organ_statuses = Column(JSONB, nullable=False)
    knowledge_base_version = Column(String(32), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())
    expired_at = Column(TIMESTAMP(timezone=True))
    consent_status_at_creation = Column(String(16), nullable=False)


class DiagnosisResult(Base):
    __tablename__ = 'diagnosis_results'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    request_id = Column(String(64), unique=True, nullable=False, index=True)
    session_id = Column(String(64), index=True)
    organ = Column(String(32), nullable=False)
    tier = Column(String(16), nullable=False)
    confidence = Column(Float, nullable=False)
    findings = Column(JSONB)
    recommendations = Column(Text)
    knowledge_ids = Column(ARRAY(Text))
    status = Column(String(32))
    result_pushed = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())


class KnowledgeBase(Base):
    __tablename__ = 'knowledge_base'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    kb_id = Column(String(64), unique=True, nullable=False, index=True)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(768))
    organ = Column(String(32))
    tier = Column(String(16))
    version = Column(String(32))
    confidence = Column(Float, default=1.0)
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp(), onupdate=func.current_timestamp())


class SafetyEvent(Base):
    __tablename__ = 'safety_events'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    event_id = Column(BigInteger, unique=True, nullable=False, index=True)
    event_type = Column(String(64), nullable=False, index=True)
    severity = Column(String(16), nullable=False)
    kb_version = Column(String(32))
    device_id = Column(String(64))
    patient_id = Column(String(64))
    payload = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    acknowledged = Column(Boolean, default=False)

    __table_args__ = (
        Index('idx_safety_event_type_time', 'event_type', 'created_at'),
    )


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    log_id = Column(String(36), unique=True, nullable=False, index=True)
    session_id = Column(String(64), index=True)
    action = Column(String(64), nullable=False, index=True)
    actor = Column(String(64), nullable=False)
    target_type = Column(String(32))
    target_id = Column(String(64))
    detail = Column(JSONB)
    log_type = Column(String(16), nullable=False, default='operation')
    hash_prev = Column(String(64))
    hash_self = Column(String(64), nullable=False)
    is_critical_operation = Column(Boolean, default=False)
    cross_chain_hash = Column(String(64))
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())

    __table_args__ = (
        Index('idx_audit_session_time', 'session_id', 'created_at'),
    )


class AuditCrossChain(Base):
    __tablename__ = 'audit_cross_chain'

    id = Column(String(36), primary_key=True)
    period_start = Column(TIMESTAMP(timezone=True), nullable=False)
    period_end = Column(TIMESTAMP(timezone=True), nullable=False)
    prev_cross_hash = Column(String(64))
    cross_hash_self = Column(String(64), nullable=False)
    session_hashes = Column(JSONB)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())

    __table_args__ = (
        Index('idx_audit_cross_chain_period', 'period_start', 'period_end'),
    )


class SessionArchive(Base):
    __tablename__ = 'sessions_archive'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    patient_id = Column(String(64), nullable=False)
    device_id = Column(String(64), nullable=False)
    status = Column(String(32), nullable=False)
    target_organs = Column(JSONB, nullable=False)
    organ_statuses = Column(JSONB, nullable=False)
    knowledge_base_version = Column(String(32), nullable=False)
    consent_status_at_creation = Column(String(16), nullable=False)
    archive_timestamp = Column(TIMESTAMP(timezone=True), nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False)
    archived_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    patient_type = Column(String(16), nullable=False, default='outpatient')


class Device(Base):
    __tablename__ = 'devices'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    device_id = Column(String(64), unique=True, nullable=False, index=True)
    device_name = Column(String(128), nullable=False)
    device_type = Column(String(64), nullable=False)
    location = Column(String(256), nullable=False)
    jetpack_version = Column(String(32))
    api_key = Column(String(128), nullable=False)
    status = Column(String(32), nullable=False, default='active')
    created_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), default=func.current_timestamp(), onupdate=func.current_timestamp())


class FeedbackRecord(Base):
    __tablename__ = 'feedback_records'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    feedback_id = Column(String(64), unique=True, nullable=False, index=True)
    request_id = Column(String(64), nullable=False, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    feedback_type = Column(String(32), nullable=False)
    feedback_level = Column(String(8), nullable=False)
    reviewer_id = Column(String(64), nullable=False)
    content = Column(JSONB, nullable=False)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())


class User(Base):
    __tablename__ = 'users'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(64), unique=True, nullable=False, index=True)
    email = Column(String(256))
    full_name = Column(String(128))
    hashed_password = Column(String(256), nullable=False)
    disabled = Column(Boolean, nullable=False, default=False)
    role = Column(String(64), nullable=False, default='ULTRASOUND_DOCTOR')
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())


class OrganDiseaseThreshold(Base):
    __tablename__ = 'organ_disease_thresholds'
    
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    organ = Column(String(32), nullable=False, index=True)
    disease_type = Column(String(64), index=True)
    confidence_threshold = Column(Float, nullable=False)
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        Index('idx_organ_disease', 'organ', 'disease_type'),
    )


# T-GA-DATA-01: Knowledge base index construction pipeline authoritative table.
# Distinct from KnowledgeBase (which serves as the RAG retrieval fast cache);
# this table holds the source-of-truth chunked content produced by
# scripts/init_knowledge_base.py. DDL lives in
# alembic/versions/0006_knowledge_chunks.py.
class KnowledgeChunkModel(Base):
    __tablename__ = 'knowledge_chunks'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), unique=True, nullable=False, index=True)
    kb_version = Column(String(32), nullable=False, index=True)
    organ = Column(String(32), nullable=False, index=True)
    content = Column(Text, nullable=False)
    meta = Column('metadata', JSONB, nullable=False, default={})
    embedding = Column(Vector(768))
    source_doc_id = Column(String(128), nullable=False, index=True)
    section_path = Column(Text, nullable=False, default='')
    token_count = Column(Integer, nullable=False, default=0)
    created_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        Index('idx_kc_kb_organ', 'kb_version', 'organ'),
    )