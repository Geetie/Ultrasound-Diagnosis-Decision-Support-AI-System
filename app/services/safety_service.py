"""安全事件服务 - 统一的安全事件记录系统"""
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional, Dict, Any
from datetime import datetime, timezone
import uuid
import structlog

from app.models.database import SafetyEvent
from app.models.schemas import SafetyEventType, SafetySeverity

logger = structlog.get_logger()


class SafetyService:
    
    def __init__(self, db: AsyncSession):
        self.db = db
    
    async def log_event(
        self,
        event_type: SafetyEventType,
        severity: SafetySeverity,
        patient_id: Optional[str] = None,
        device_id: Optional[str] = None,
        kb_version: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> SafetyEvent:
        now = datetime.now(timezone.utc)
        event_id = uuid.uuid4().int >> 96
        
        safety_event = SafetyEvent(
            event_id=event_id,
            event_type=event_type.value,
            severity=severity.value,
            patient_id=patient_id,
            device_id=device_id,
            kb_version=kb_version,
            payload=payload,
            created_at=now,
            acknowledged=False,
        )
        
        self.db.add(safety_event)
        
        logger.warning(
            "safety_event_logged",
            event_type=event_type.value,
            severity=severity.value,
            patient_id=patient_id,
            device_id=device_id,
        )
        
        return safety_event
