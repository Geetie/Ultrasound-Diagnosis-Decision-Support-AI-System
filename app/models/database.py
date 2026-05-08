from sqlalchemy import Column, Integer, String, Text, Float, Date, TIMESTAMP, Boolean, ARRAY, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
from pgvector.sqlalchemy import Vector

Base = declarative_base()


class Patient(Base):
    __tablename__ = 'patients'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), unique=True, nullable=False)
    name = Column(Text)
    birth_date = Column(Date)
    gender = Column(String(10))
    created_at = Column(TIMESTAMP, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, default=func.current_timestamp())


class ConsentRecord(Base):
    __tablename__ = 'consent_records'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    patient_id = Column(String(64), ForeignKey('patients.patient_id'))
    consent_type = Column(String(32), nullable=False)
    consented = Column(Boolean, nullable=False, default=False)
    consent_date = Column(TIMESTAMP)
    withdrawal_date = Column(TIMESTAMP)
    created_at = Column(TIMESTAMP, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, default=func.current_timestamp())


class DiagnosisSession(Base):
    __tablename__ = 'diagnosis_sessions'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), unique=True, nullable=False)
    patient_id = Column(String(64), ForeignKey('patients.patient_id'))
    organ = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default='pending')
    kb_version = Column(String(32))
    model_version = Column(String(32))
    created_at = Column(TIMESTAMP, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, default=func.current_timestamp())


class DiagnosisResult(Base):
    __tablename__ = 'diagnosis_results'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), ForeignKey('diagnosis_sessions.session_id'))
    organ = Column(String(32), nullable=False)
    tier = Column(String(16), nullable=False)
    confidence = Column(Float, nullable=False)
    findings = Column(Text)
    recommendations = Column(Text)
    knowledge_ids = Column(ARRAY(Text))
    created_at = Column(TIMESTAMP, default=func.current_timestamp())


class KnowledgeBase(Base):
    __tablename__ = 'knowledge_base'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(64), unique=True, nullable=False)
    content = Column(Text, nullable=False)
    embedding = Column(Vector(768))
    organ = Column(String(32))
    tier = Column(String(16))
    version = Column(String(32))
    confidence = Column(Float, default=1.0)
    created_at = Column(TIMESTAMP, default=func.current_timestamp())
    updated_at = Column(TIMESTAMP, default=func.current_timestamp())


class AuditLog(Base):
    __tablename__ = 'audit_logs'
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64))
    operation = Column(String(64), nullable=False)
    operator = Column(String(64))
    details = Column(JSON)
    hash_chain = Column(String(64))
    created_at = Column(TIMESTAMP, default=func.current_timestamp())