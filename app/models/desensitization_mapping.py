from sqlalchemy import Column, String, Integer, LargeBinary, DateTime, Index
from sqlalchemy.sql import func
from app.config.database import Base


class DesensitizationMapping(Base):
    __tablename__ = 'desensitization_mappings'

    id = Column(String(64), primary_key=True)
    session_id = Column(String(64), nullable=False, index=True)
    encrypted_original = Column(LargeBinary, nullable=False)
    iv = Column(LargeBinary(12), nullable=False)
    desensitized_value = Column(String(256), nullable=False)
    field_type = Column(String(64), nullable=False)
    key_version = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.current_timestamp())

    __table_args__ = (
        Index('idx_desens_session_field', 'session_id', 'field_type'),
    )
