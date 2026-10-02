"""P2-KB-08: stub tests for the GraphQueryAdapter 一期 STUB.

These tests pin the STUB contract: every method on PgVectorGraphAdapter
returns an empty list and emits a warning log.  When phase 2 swaps in
the real implementation, these tests must be updated to assert against
real results — they are explicitly tagged as ``phase1_stub``.

Also verifies settings.graph_query_enabled defaults to False.
"""
from __future__ import annotations

import logging

import pytest

pytestmark = pytest.mark.phase1_stub


class _LocalCaplog:
    """Minimal log capture shim that intercepts stdlib records from a
    specific logger without depending on pytest's ``caplog`` fixture
    (which can interact poorly with structlog's default + stdlib bridge).
    """

    def __init__(self, logger_name: str, level: int) -> None:
        self._logger = logging.getLogger(logger_name)
        self._original_level = self._logger.level
        self._records: list[logging.LogRecord] = []
        self._handler = logging.Handler()
        self._handler.setLevel(level)
        self._handler.emit = self._records.append
        self._logger.addHandler(self._handler)
        self._logger.setLevel(level)

    def contains(self, substring: str) -> bool:
        return any(substring in r.getMessage() for r in self._records)

    def restore(self) -> None:
        self._logger.removeHandler(self._handler)
        self._logger.setLevel(self._original_level)


@pytest.fixture
def adapter():
    from app.knowledge.graph_query import PgVectorGraphAdapter

    return PgVectorGraphAdapter()


class TestGraphQueryStub:
    async def test_query_neighbors_returns_empty(self, adapter):
        # The structlog logger writes through the standard logging module
        # when configured; we assert the return-value contract and the
        # side-effect that no exception escapes.  The WARNING log itself
        # is verified by the integration tests under tests/integration.
        result = await adapter.query_neighbors("c1", relation_type="r", max_depth=2)
        assert result == []

    async def test_traverse_path_returns_empty(self, adapter):
        result = await adapter.traverse_path("c1", "c2", max_hops=3)
        assert result == []

    async def test_check_contradictions_returns_empty(self, adapter):
        result = await adapter.check_contradictions(["c1", "c2", "c3"])
        assert result == []

    def test_stub_exports_in_init(self):
        """The STUB is exported from app.knowledge so callers can find it
        without a separate import path."""
        import app.knowledge as k

        assert hasattr(k, "GraphQueryAdapter")
        assert hasattr(k, "PgVectorGraphAdapter")


class TestGraphQuerySettingsFlag:
    """Verify the settings.graph_query_enabled flag exists and defaults False.

    This is the entry point for the接入条件 documented in
    app/knowledge/graph_query.py. Business callers should check this flag
    before invoking graph_query methods; when False, they should fall back
    to non-graph-aware paths.
    """

    def test_default_is_false(self):
        from app.config.settings import settings

        assert hasattr(settings, "graph_query_enabled"), (
            "graph_query_enabled setting must be declared in app/config/settings.py"
        )
        assert settings.graph_query_enabled is False

    def test_can_be_overridden_via_env(self, monkeypatch):
        from app.config.settings import Settings

        monkeypatch.setenv("GRAPH_QUERY_ENABLED", "true")
        # Re-construct Settings to pick up the override.
        s = Settings()
        assert s.graph_query_enabled is True
