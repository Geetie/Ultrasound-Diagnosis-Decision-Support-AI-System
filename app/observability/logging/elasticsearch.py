import asyncio
import os
from datetime import datetime, timezone
from typing import Any

import structlog

from .backend import LogStorageBackend
from .record import LogRecord

_logger = structlog.get_logger("app.observability.logging")


class ElasticsearchLogStorage(LogStorageBackend):
    def __init__(
        self,
        es_url: str | None = None,
        index_prefix: str = "ultrasound-logs",
    ):
        self.es_url = es_url or os.getenv("ELASTICSEARCH_URL", "http://localhost:9200")
        self.index_prefix = index_prefix
        self._client: Any | None = None
        self._async_lock: asyncio.Lock | None = None

    async def _get_client(self):
        if self._client is None:
            try:
                from elasticsearch import AsyncElasticsearch
                self._client = AsyncElasticsearch([self.es_url])
            except ImportError:  # silent-ok: elasticsearch 可选依赖缺失时禁用后端
                self._client = None
        return self._client

    async def awrite(self, record: LogRecord) -> None:
        client = await self._get_client()
        if client is None:
            _logger.debug("Elasticsearch client not available (import error)")
            return

        index_name = f"{self.index_prefix}-{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"

        try:
            await client.index(index=index_name, document=record.to_dict())
        except Exception as e:
            _logger.error("Failed to write log to Elasticsearch",
                          error=str(e),
                          es_url=self.es_url,
                          index=index_name)

    async def awrite_batch(self, records: list[LogRecord]) -> None:
        if not records:
            return

        client = await self._get_client()
        if client is None:
            _logger.debug("Elasticsearch client not available (import error)")
            for record in records:
                await self.awrite(record)
            return

        index_name = f"{self.index_prefix}-{datetime.now(timezone.utc).strftime('%Y.%m.%d')}"

        try:
            from elasticsearch.helpers import async_bulk

            actions = [
                {
                    "_index": index_name,
                    "_source": record.to_dict(),
                }
                for record in records
            ]

            await async_bulk(client, actions)
            _logger.debug("Batch write to Elasticsearch completed", count=len(records))
        except Exception as e:
            _logger.error("Failed to write batch logs to Elasticsearch",
                          error=str(e),
                          count=len(records))
            for record in records:
                await self.awrite(record)

    async def close(self) -> None:
        if self._client:
            await self._client.close()
            self._client = None
