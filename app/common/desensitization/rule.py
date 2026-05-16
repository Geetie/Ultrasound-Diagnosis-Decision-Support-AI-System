from typing import Any

import structlog
from pydantic import BaseModel

logger = structlog.get_logger()


class DesensitizationRule(BaseModel):
    name: str
    pattern: str
    replacement: str
    description: str | None = None
    enabled: bool = True
    priority: int = 10
    is_patch: bool = False
