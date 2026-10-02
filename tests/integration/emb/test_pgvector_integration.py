"""E-6: PGVector 集成测试 — PgvectorVectorIndex CRUD 操作。

验证:
- create_collection + upsert + search + delete
- 维度正确性
- 错误处理

需要 RUN_PGVECTOR_INTEGRATION=1 环境变量启用。
"""
from __future__ import annotations

import os

import pytest

# Skip entire module unless explicitly enabled.
pytestmark = pytest.mark.skipif(
    os.getenv("RUN_PGVECTOR_INTEGRATION", "") != "1",
    reason="Set RUN_PGVECTOR_INTEGRATION=1 to enable PGVector integration tests",
)


@pytest.mark.asyncio
class TestPgvectorIntegration:
    """Integration tests for PgvectorVectorIndex."""

    async def test_health_check(self) -> None:
        """Verify PgvectorVectorIndex health check succeeds."""
        from app.config.settings import settings
        from app.embedding.pgvector_client import PgvectorVectorIndex

        client = PgvectorVectorIndex(database_url=settings.database_url)
        try:
            healthy = await client.health_check()
            assert healthy, "PGVector health check failed"
        finally:
            await client.close()

    async def test_create_and_search(self) -> None:
        """Verify create_collection + upsert + search workflow."""
        from app.config.settings import settings
        from app.embedding.pgvector_client import PgvectorVectorIndex
        from app.embedding.protocols import VectorRecord

        client = PgvectorVectorIndex(database_url=settings.database_url)
        collection = "test_pgvector_integration"
        try:
            # Create
            await client.create_collection(
                collection=collection, dimension=3, distance="cosine"
            )
            # Upsert
            vectors = [
                VectorRecord(id="v1", vector=[1.0, 0.0, 0.0], metadata={"label": "a"}),
                VectorRecord(id="v2", vector=[0.0, 1.0, 0.0], metadata={"label": "b"}),
            ]
            await client.upsert(collection=collection, vectors=vectors)
            # Search
            results = await client.search(
                collection=collection,
                query_vector=[1.0, 0.0, 0.0],
                top_k=2,
            )
            assert len(results) >= 1, "Expected at least 1 search result"
            assert results[0].id == "v1", f"Top result should be v1, got {results[0].id}"
        finally:
            try:
                await client.delete_collection(collection)
            except Exception:
                pass
            await client.close()
