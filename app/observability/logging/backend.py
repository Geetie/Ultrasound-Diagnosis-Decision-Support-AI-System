import asyncio
from abc import ABC, abstractmethod
from typing import List, Optional

from .record import LogRecord


class LogStorageBackend(ABC):
    _async_lock: Optional[asyncio.Lock] = None

    @abstractmethod
    async def awrite(self, record: LogRecord) -> None:
        raise NotImplementedError

    @abstractmethod
    async def awrite_batch(self, records: List[LogRecord]) -> None:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError

    def write(self, record: LogRecord) -> None:
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.create_task(self.awrite(record))
            else:
                loop.run_until_complete(self.awrite(record))
        except RuntimeError:
            asyncio.run(self.awrite(record))

    def _get_async_lock(self) -> asyncio.Lock:
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock
