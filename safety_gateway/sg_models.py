"""Safety Gateway数据库模型模块

定义SafetyGateway服务使用的SQLAlchemy ORM模型。

数据模型：
- ConsentRecord: 患者知情同意记录
- OrganDiseaseThreshold: 器官-病种置信度阈值配置
"""

from sqlalchemy import TIMESTAMP, BigInteger, CheckConstraint, Column, Float, Index, String, Text
from sqlalchemy.sql import func

from safety_gateway.sg_db import Base


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
    updated_at = Column(TIMESTAMP(timezone=True), nullable=False, default=func.current_timestamp(), onupdate=func.current_timestamp())

    __table_args__ = (
        Index('idx_consent_patient_status', 'patient_id', 'status'),
        CheckConstraint("status IN ('granted', 'denied', 'withdrawn', 'pending')", name='ck_consent_status'),
    )


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
