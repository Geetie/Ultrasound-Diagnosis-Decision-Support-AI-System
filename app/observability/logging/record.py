import time
from datetime import datetime, timezone
from typing import Any

from app.observability.enums import LogCategory, NtpSyncStatus


class LogRecord:
    def __init__(
        self,
        category: LogCategory,
        message: str,
        extra: dict[str, Any] | None = None,
        ntp_sync_status: NtpSyncStatus | None = None,
        **kwargs,
    ):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.category = category.value
        self.message = message
        self.extra = extra or {}
        self.ntp_sync_status = ntp_sync_status.value if ntp_sync_status else None
        self.kwargs = kwargs
        self._enqueue_time = time.time()

    def to_dict(self) -> dict[str, Any]:
        result = {
            "timestamp": self.timestamp,
            "category": self.category,
            "message": self.message,
            "ntp_sync_status": self.ntp_sync_status,
        }
        for k, v in self.extra.items():
            result[f"extra_{k}"] = v
        for k, v in self.kwargs.items():
            result[f"param_{k}"] = v
        return result
