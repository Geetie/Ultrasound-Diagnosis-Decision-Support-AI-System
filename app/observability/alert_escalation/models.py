import time
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AlertContact:
    name: str
    role: str
    phone: str = ""
    email: str = ""
    superior_hospital_ops: str = ""
    hospital_admin_duty: str = ""
    equipment_supplier: str = ""


@dataclass
class AlertEvent:
    alert_id: str
    level: str
    metric: str
    value: float
    threshold: float
    created_at: float
    confirmed_at: Optional[float] = None
    escalated_at: Optional[float] = None
    escalation_level: int = 0
    reminded_at: Optional[float] = None
    message: str = ""

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None

    @property
    def is_reminded(self) -> bool:
        return self.reminded_at is not None

    @property
    def elapsed_seconds(self) -> float:
        return time.time() - self.created_at
