# -*- coding: utf-8 -*-
"""Smoke: backfill idempotency + graph neighbor query end-to-end."""
import os
import sys

os.environ["GRAPH_QUERY_ENABLED"] = "1"  # must precede app imports
sys.path.insert(0, ".")

import asyncio  # noqa: E402

from sqlalchemy import text  # noqa: E402

from app.config.database import AsyncSessionLocal  # noqa: E402
from app.knowledge.graph_query import PgVectorGraphAdapter  # noqa: E402


async def main() -> int:
    # 1) idempotency probe
    async with AsyncSessionLocal() as session:
        n = (
            await session.execute(
                text(
                    "SELECT count(*) FROM knowledge_chunks "
                    "WHERE kb_version='kb_v1.3.0' AND metadata ? 'relations'"
                )
            )
        ).scalar()
    print("chunks with relations:", n)

    # 2) neighbor query
    adapter = PgVectorGraphAdapter()
    seed = "kb_v1.3.0:dialogue_cardiac_外科_0797#0000"
    neighbors = await adapter.query_neighbors(seed, max_depth=1)
    print("neighbors of", seed, "->", [c.chunk_id for c in neighbors])
    assert any(c.chunk_id.endswith("#0001") for c in neighbors), "expected #0001"
    contradictions = await adapter.check_contradictions(
        ["kb_v1.3.0:dialogue_cardiac_外科_0797#0000"]
    )
    print("contradictions:", contradictions)
    print("SMOKE PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
