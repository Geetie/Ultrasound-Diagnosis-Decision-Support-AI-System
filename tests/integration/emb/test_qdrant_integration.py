"""Qdrant 真实容器集成测试(可选)。

环境要求:
- Docker 可用
- Qdrant 容器可通过 docker-compose.dev.yml 启动
- 设置 RUN_QDRANT_INTEGRATION=1 启用(默认跳过,避免 CI 误跑)

验证目标:
- 真实 Qdrant 容器 upsert 1000 条向量
- search top-10 延迟 P99 < 100ms
- filter 过滤正确性
- collection 生命周期(create/delete)
"""

from __future__ import annotations

import os
import subprocess
import time

import numpy as np
import pytest

from app.embedding.protocols import VectorRecord

RUN_QDRANT_INTEGRATION = os.environ.get("RUN_QDRANT_INTEGRATION", "0") == "1"
QDRANT_HOST = os.environ.get("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.environ.get("QDRANT_PORT", "6333"))


def _is_docker_available() -> bool:
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        )
        return result.returncode == 0
    except Exception:
        return False


def _is_qdrant_reachable() -> bool:
    try:
        import socket

        with socket.create_connection((QDRANT_HOST, QDRANT_PORT), timeout=2):
            return True
    except Exception:
        return False


pytestmark = [
    pytest.mark.integration,
    pytest.mark.track_emb,
    pytest.mark.skipif(
        not RUN_QDRANT_INTEGRATION,
        reason="set RUN_QDRANT_INTEGRATION=1 to run Qdrant integration tests",
    ),
    pytest.mark.skipif(
        not _is_docker_available(),
        reason="docker not available",
    ),
    pytest.mark.skipif(
        not _is_qdrant_reachable(),
        reason=f"Qdrant not reachable at {QDRANT_HOST}:{QDRANT_PORT}",
    ),
]


@pytest.fixture(scope="module")
def qdrant_client():
    from app.embedding.qdrant_client import QdrantVectorIndex

    client = QdrantVectorIndex(host=QDRANT_HOST, port=QDRANT_PORT, timeout=10.0)
    yield client
    import asyncio

    asyncio.run(client.close())


@pytest.fixture(scope="module")
def test_collection():
    name = f"emb_integration_{int(time.time())}"
    yield name


@pytest.mark.asyncio
async def test_qdrant_create_collection(qdrant_client, test_collection):
    await qdrant_client.create_collection(test_collection, dimension=768, distance="cosine")


@pytest.mark.asyncio
async def test_qdrant_upsert_and_search_top10_under_100ms(qdrant_client, test_collection):
    dim = 768
    n = 1000
    np.random.seed(42)
    records = [
        VectorRecord(
            id=f"v{i}",
            vector=np.random.randn(dim).astype(np.float32).tolist(),
            metadata={"organ": "thyroid" if i % 2 == 0 else "liver", "idx": i},
        )
        for i in range(n)
    ]
    await qdrant_client.upsert(test_collection, records)

    latencies_ms = []
    np.random.seed(99)
    for _ in range(100):
        q = np.random.randn(dim).astype(np.float32).tolist()
        t0 = time.monotonic()
        results = await qdrant_client.search(test_collection, q, top_k=10)
        latencies_ms.append((time.monotonic() - t0) * 1000)
        assert len(results) == 10

    p50 = float(np.percentile(latencies_ms, 50))
    p95 = float(np.percentile(latencies_ms, 95))
    p99 = float(np.percentile(latencies_ms, 99))
    print(f"\n[Qdrant 1000条向量检索延迟] P50={p50:.2f}ms P95={p95:.2f}ms P99={p99:.2f}ms")
    assert p99 < 100.0, f"P99 {p99:.2f}ms exceeds 100ms target"


@pytest.mark.asyncio
async def test_qdrant_filter_query(qdrant_client, test_collection):
    dim = 768
    q = np.random.randn(dim).astype(np.float32).tolist()
    results = await qdrant_client.search(
        test_collection, q, top_k=10, filter={"organ": "thyroid"}
    )
    assert len(results) <= 10
    for r in results:
        assert r.metadata.get("organ") == "thyroid"


@pytest.mark.asyncio
async def test_qdrant_health_check(qdrant_client):
    healthy = await qdrant_client.health_check()
    assert healthy is True


@pytest.mark.asyncio
async def test_qdrant_delete_collection(qdrant_client, test_collection):
    await qdrant_client.delete_collection(test_collection)
    q = np.random.randn(768).astype(np.float32).tolist()
    with pytest.raises(Exception):
        await qdrant_client.search(test_collection, q, top_k=5)
