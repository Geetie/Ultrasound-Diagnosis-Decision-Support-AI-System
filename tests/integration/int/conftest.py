"""T-GA-INT-01 集成测试 fixtures。

复用 :mod:`tests.integration.rag.conftest` 的基础 fixtures（fake redis、
mock dispatcher、mock fusion engine 等），仅补全 INT-01 特有的 worker
池和管线编排 fixtures。
"""
from __future__ import annotations

import asyncio
import pytest
from typing import List, Optional

from app.config.redis_keys import RedisKey


@pytest.fixture
def int_queue_key() -> str:
    """返回 INT-01 使用的 Redis 队列 key。"""
    return RedisKey.DIAGNOSIS_PENDING_QUEUE


@pytest.fixture
def int_dlq_key() -> str:
    """返回 INT-01 使用的 Redis DLQ key。"""
    return RedisKey.DIAGNOSIS_DLQ


@pytest.fixture
def int_worker_count() -> int:
    """测试用 worker 数量（默认 2，减少资源占用）。"""
    return 2


@pytest.fixture
def int_dlq_max_retries() -> int:
    """测试用 DLQ 最大重试数（默认 3）。"""
    return 3


__all__ = [
    "int_queue_key",
    "int_dlq_key",
    "int_worker_count",
    "int_dlq_max_retries",
]
