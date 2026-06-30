"""Shared fixtures for FUS integration tests (T-GA-FUS-01/02 E2E).

与 ``tests/unit/fus/conftest.py`` 复用 — 这里直接重新导出常用 fixture，
避免在两个目录下复制代码。
"""
from __future__ import annotations

# 复用 unit tests conftest 的所有 fixture
from tests.unit.fus.conftest import (  # noqa: F401
    FakeInferenceEngine,
    FakeRAGService,
    make_chunk,
    make_response,
    make_scored,
    conflict_detector,
    consistency_checker,
    consistency_checker_with_rag,
    crag_handler,
    crag_handler_with_chunks,
    self_rag_evaluator_heuristic,
    self_rag_evaluator_with_llm,
)
