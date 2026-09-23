"""T-GA-INT-01 集成测试 fixtures。

复用 :mod:`tests.integration.rag.conftest` 的基础 fixtures（fake redis、
mock dispatcher、mock fusion engine 等），仅补全 INT-01 特有的 worker
池和管线编排 fixtures。
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.fixture(autouse=True)
def mock_diagnosis_repo_read():
    """把管线内的 DB 读取替换为内存 mock。

    ``run_diagnosis_pipeline`` 第一步是 ``await diagnosis_repo.get_result(...)``。
    本目录用例复用了被 mock 的 ``AsyncSessionLocal``，但 ``DiagnosisRepository``
    是真实类，其内部 ``await self.db.execute(...)`` 拿到的是 MagicMock，会抛
    ``TypeError: object MagicMock can't be used in 'await' expression``，
    导致管线在第一步就失败返回 False。

    这里统一替换为内存 mock 读取；需要真实仓库行为的用例可自行 patch
    （内层 patch 优先，不受影响）。
    """
    with patch("app.services.diagnosis_pipeline.DiagnosisRepository") as repo_cls:
        repo_cls.return_value.get_result = AsyncMock(
            return_value=MagicMock(
                request_id="req_int",
                session_id="sess_int",
                organ="thyroid",
            )
        )
        yield repo_cls


__all__ = [
    "int_queue_key",
    "int_dlq_key",
    "int_worker_count",
    "int_dlq_max_retries",
    "mock_diagnosis_repo_read",
]
